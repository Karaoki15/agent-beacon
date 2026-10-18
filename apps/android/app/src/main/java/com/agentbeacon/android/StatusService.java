package com.agentbeacon.android;

import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class StatusService extends Service {
    static final String ACTION_START = "com.agentbeacon.android.action.START_MONITOR";
    static final String ACTION_STOP = "com.agentbeacon.android.action.STOP_MONITOR";
    static final String ACTION_SNAPSHOT = "com.agentbeacon.android.action.SNAPSHOT";
    static final String ACTION_CHECK = "com.agentbeacon.android.action.CHECK_CONNECT";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final PollGenerationGate pollGate = new PollGenerationGate();
    private volatile boolean stopped;
    private int pollFailures;
    private boolean discoveryPending = true;
    private BeaconClient.LiveRequest activeRequest;
    private final SnapshotDelivery delivery = new SnapshotDelivery(handler);
    private Runnable leaseExpiry;
    private static boolean running;
    private NetworkReconnect networkReconnect;
    private OfflineBudget offlineBudget;
    private final Runnable offlineDeadline = () -> {
        if (stopped || offlineBudget == null) return;
        if (offlineBudget.expired(SystemClock.elapsedRealtime())) pauseOffline();
        else scheduleOfflineDeadline();
    };

    static boolean isRunning() { return running; }

    @Override public void onCreate() {
        super.onCreate();
        networkReconnect = new NetworkReconnect(this, () -> {
            if (stopped || !running) return;
            int generation = pollGate.invalidate();
            delivery.cancel();
            if (activeRequest != null) activeRequest.cancel();
            pollFailures = 0;
            discoveryPending = true;
            handler.post(() -> poll(generation));
        });
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (activeRequest != null) activeRequest.cancel();
        delivery.cancel();
        StatusNotifier.ensureChannels(this);
        if (intent == null && !getSharedPreferences("beacon_runtime", MODE_PRIVATE).getBoolean("monitor_enabled", false)) {
            stopped = true; running = false; stopSelf(startId); return START_NOT_STICKY;
        }
        if (intent != null && (ACTION_START.equals(intent.getAction()) || ACTION_CHECK.equals(intent.getAction()))) {
            getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("promoted_suppressed", false).apply();
        }
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            pollGate.invalidate();
            stopped = true;
            running = false;
            MonitorState.pause(this, "manual");
            // Keep queued HTTP completions: each one releases the single-flight gate.
            // Their generation check prevents stale results from updating status or
            // scheduling another poll. onDestroy clears callbacks once this instance dies.
            stopForeground(true);
            StatusNotifier.cancelStatus(this);
            AttentionNotifier.cancelAll(this);
            broadcastSnapshot();
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        if (ConnectionStore.load(this) == null || !StatusNotifier.enabled(this)) {
            getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", false).apply();
            stopped = true;
            running = false;
            stopForeground(true);
            StatusNotifier.cancelStatus(this);
            broadcastSnapshot();
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        stopped = false;
        pollFailures = 0;
        discoveryPending = true;
        boolean explicit = intent != null && (ACTION_START.equals(intent.getAction()) || ACTION_CHECK.equals(intent.getAction()));
        offlineBudget = MonitorState.begin(this, explicit);
        MonitorState.resumed(this);
        scheduleOfflineDeadline();
        final int startGeneration = pollGate.invalidate();
        StatusNotifier.ensureChannels(this);
        if (Build.VERSION.SDK_INT >= 29) startForeground(StatusNotifier.MONITOR_ID, StatusNotifier.monitoring(this), ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        else startForeground(StatusNotifier.MONITOR_ID, StatusNotifier.monitoring(this));
        running = true;
        getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", true).apply();
        StatusNotifier.resetPresentation();
        publishCached();
        handler.post(() -> poll(startGeneration));
        return START_STICKY;
    }

    private void poll(final int pollGeneration) {
        if (stopped || pollGeneration != pollGate.current()) return;
        if (offlineBudget != null && offlineBudget.expired(SystemClock.elapsedRealtime())) { pauseOffline(); return; }
        if (!StatusNotifier.enabled(StatusService.this)) {
            stopped = true; running = false; getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", false).apply();
            stopForeground(true); StatusNotifier.cancelStatus(StatusService.this); BeaconWidget.refresh(StatusService.this); stopSelf(); return;
        }
        final Credentials credentials = ConnectionStore.load(StatusService.this);
        if (credentials == null) {
            stopped = true; running = false; getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", false).apply();
            stopForeground(true); StatusNotifier.cancelStatus(StatusService.this); BeaconWidget.refresh(StatusService.this); stopSelf(); return;
        }
        if (!pollGate.begin(pollGeneration)) return;
        final BeaconClient.LiveRequest request = new BeaconClient.LiveRequest();
        activeRequest = request;
        final boolean saver = UpdateSettings.batterySaver(this);
        final boolean discover = discoveryPending;
        discoveryPending = false;
        worker.execute(() -> {
            try {
                if (discover) {
                    Credentials found = ConnectionRecovery.find(this, credentials, request, saver);
                    if (found != null) { handler.post(() -> applyRecovery(pollGeneration, credentials, found)); return; }
                }
                if (request.cancelled()) throw new java.io.InterruptedIOException("Cancelled");
                boolean streamed = BeaconClient.updates(credentials, saver, request, (response, transport, responseMs) -> handler.post(() -> {
                    if (!isCurrent(pollGeneration, credentials)) return;
                    pollFailures = 0;
                    offlineBudget.received(SystemClock.elapsedRealtime()); scheduleOfflineDeadline();
                    UpdateDiagnostics.received(response, transport, responseMs);
                    AttentionNotifier.received(this, response, credentials);
                    delivery.receive(this, response, credentials, () -> isCurrent(pollGeneration, credentials), this::publishCached);
                }));
                handler.post(() -> {
                    boolean current = pollGate.complete(pollGeneration);
                    if (stopped) return;
                    if (!current) { handler.post(() -> poll(pollGate.current())); return; }
                    if (streamed) handler.postDelayed(() -> poll(pollGeneration), UpdateSettings.reconnectDelay(saver));
                    else schedulePoll(pollGeneration);
                });
            } catch (Exception error) {
                Credentials found = ConnectionRecovery.find(this, credentials, request, saver);
                if (found != null) { handler.post(() -> applyRecovery(pollGeneration, credentials, found)); return; }
                handler.post(() -> {
                    boolean current = pollGate.complete(pollGeneration);
                    if (stopped) return;
                    if (!current) { handler.post(() -> poll(pollGate.current())); return; }
                    if (!ConnectionStore.matches(this, credentials)) { schedulePoll(pollGeneration); return; }
                    pollFailures = Math.min(6, pollFailures + 1);
                    delivery.cancel();
                    UpdateDiagnostics.failed(error);
                    Log.w("AgentBeacon", "Live connection interrupted (" + error.getClass().getSimpleName() + ")");
                    publishCached();
                    schedulePoll(pollGeneration);
                });
            }
        });
    }

    private boolean isCurrent(int generation, Credentials credentials) {
        return !stopped && generation == pollGate.current() && ConnectionStore.matches(this, credentials);
    }

    private void applyRecovery(int generation, Credentials previous, Credentials verified) {
        boolean current = pollGate.complete(generation);
        if (stopped) return;
        if (current && ConnectionStore.matches(this, previous)) {
            try {
                if (ConnectionStore.relocate(this, previous, verified)) {
                    delivery.cancel(); pollFailures = 0; publishCached();
                }
            } catch (Exception error) {
                Log.w("AgentBeacon", "Could not save recovered connection (" + error.getClass().getSimpleName() + ")");
                pollFailures = Math.min(6, pollFailures + 1); schedulePoll(pollGate.current()); return;
            }
        }
        handler.post(() -> poll(pollGate.current()));
    }

    private void publishCached() {
        Snapshot cached = SnapshotCache.load(this);
        if (cached == null) StatusNotifier.cancelStatus(this);
        else { StatusNotifier.postStatus(this, cached); scheduleLeaseExpiry(cached); }
        broadcastSnapshot();
    }

    private void scheduleOfflineDeadline() {
        handler.removeCallbacks(offlineDeadline);
        if (!stopped && offlineBudget != null) handler.postDelayed(offlineDeadline, Math.max(1L, offlineBudget.remaining(SystemClock.elapsedRealtime())));
    }

    private void pauseOffline() {
        stopped = true; running = false; pollGate.invalidate();
        if (activeRequest != null) activeRequest.cancel();
        delivery.cancel(); handler.removeCallbacksAndMessages(null);
        MonitorState.pause(this, "offline");
        stopForeground(true); StatusNotifier.cancelStatus(this); AttentionNotifier.cancelAll(this);
        broadcastSnapshot(); stopSelf();
    }

    private void schedulePoll(int generation) {
        boolean saver = UpdateSettings.batterySaver(this);
        long delay = pollFailures > 0 ? UpdateSettings.retryDelay(pollFailures, saver)
                : PollSchedule.delayMs(SnapshotCache.load(this), SystemClock.elapsedRealtime(), MainActivity.isVisible(), 0);
        if (saver && !MainActivity.isVisible() && pollFailures == 0) delay = Math.max(delay, 8000L);
        handler.postDelayed(() -> poll(generation), delay);
    }

    private void scheduleLeaseExpiry(Snapshot snapshot) {
        if (leaseExpiry != null) handler.removeCallbacks(leaseExpiry);
        long now = SystemClock.elapsedRealtime();
        StatusPresentation presentation = StatusPresentation.select(this, snapshot, !stopped, now);
        if (presentation.remainingMs <= 0) return;
        long expires = now + presentation.remainingMs;
        if (snapshot.isQuestionReminderOnly(SystemClock.elapsedRealtime())) {
            expires = Math.min(expires, SystemClock.elapsedRealtime() + snapshot.questionReminderRemainingMs(SystemClock.elapsedRealtime()));
        }
        leaseExpiry = () -> {
            if (stopped) return;
            Snapshot cached = SnapshotCache.load(this);
            if (cached != null) {
                StatusNotifier.postStatus(this, cached);
                scheduleLeaseExpiry(cached);
            }
            broadcastSnapshot();
        };
        handler.postDelayed(leaseExpiry, Math.max(1L, expires - SystemClock.elapsedRealtime()));
    }

    private void broadcastSnapshot() {
        sendBroadcast(new Intent(ACTION_SNAPSHOT).setPackage(getPackageName()));
        BeaconWidget.refresh(this);
    }

    @Override public void onDestroy() {
        if (networkReconnect != null) networkReconnect.close();
        pollGate.invalidate();
        running = false;
        stopped = true;
        if (activeRequest != null) activeRequest.cancel();
        delivery.cancel(); handler.removeCallbacksAndMessages(null); worker.shutdownNow();
        BeaconWidget.refresh(this);
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
