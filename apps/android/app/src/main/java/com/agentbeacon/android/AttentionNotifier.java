package com.agentbeacon.android;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.os.SystemClock;
import android.service.notification.StatusBarNotification;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

final class AttentionNotifier {
    private static final String CHANNEL = "attention_sound_v1", PREFIX = "beacon.attention.";
    private static final int ID = 4200;
    private static String scope;
    private static AlertTracker tracker;
    private static String persisted;

    static void ensureChannel(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel existing = manager.getNotificationChannel(CHANNEL);
        if (existing != null) {
            existing.setName(AppText.tr(context, "Questions and completed work"));
            existing.setDescription(AppText.tr(context, "Alerts only when an agent needs your answer or finishes a request. Starts and routine updates stay silent."));
            manager.createNotificationChannel(existing); return;
        }
        NotificationChannel channel = new NotificationChannel(CHANNEL, AppText.tr(context, "Questions and completed work"), NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription(AppText.tr(context, "Alerts only when an agent needs your answer or finishes a request. Starts and routine updates stay silent."));
        channel.setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
        channel.enableVibration(true); channel.setVibrationPattern(new long[] {0, 180, 80, 180});
        manager.createNotificationChannel(channel);
    }

    static void received(Context context, Snapshot snapshot, Credentials credentials) {
        if (!ConnectionStore.matches(context, credentials)) return;
        String nextScope;
        try { nextScope = credentials.deviceId + ":" + android.util.Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(credentials.token.getBytes(StandardCharsets.US_ASCII)), android.util.Base64.NO_WRAP); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
        SharedPreferences prefs = context.getSharedPreferences("beacon_attention", Context.MODE_PRIVATE);
        if (!nextScope.equals(scope)) {
            scope = nextScope;
            persisted = nextScope.equals(prefs.getString("scope", "")) ? prefs.getString("tracker", null) : null;
            tracker = new AlertTracker(persisted);
        }
        if (!tracker.isCurrent(snapshot)) return;
        long now = SystemClock.elapsedRealtime();
        List<AlertTracker.Event> events = tracker.accept(snapshot, now);
        String saved = tracker.save();
        if (!saved.equals(persisted)) { prefs.edit().putString("scope", scope).putString("tracker", saved).apply(); persisted = saved; }
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        // Answered/completed questions stop occupying the notification shade.
        StatusBarNotification[] activeNotices = manager.getActiveNotifications();
        for (Snapshot.SessionRow row : snapshot.sessions) {
            if (!row.active || row.isFresh(now, snapshot.requestStartedElapsedMs) && ("ready".equals(row.actualState)
                    || "working".equals(row.actualState) && (!row.reminderActive || row.reminderRemainingMs(now, snapshot.requestStartedElapsedMs) == 0))) {
                // Only cancel question notices; a completion remains until read or timed out.
                for (StatusBarNotification notice : activeNotices) {
                    if ((PREFIX + row.source + ":" + row.sessionId).equals(notice.getTag())
                            && notice.getNotification().extras.getBoolean("beacon_question", false)) manager.cancel(notice.getTag(), ID);
                }
            }
        }
        if (!manager.areNotificationsEnabled()) return;
        ensureChannel(context);
        // A provider may save the chat title after completion. Repair the visible
        // notice silently instead of leaving its temporary label until dismissal.
        for (StatusBarNotification notice : activeNotices) {
            String tag = notice.getTag();
            if (tag == null || !tag.startsWith(PREFIX)) continue;
            Snapshot.SessionRow row = AlertTracker.find(snapshot, tag.substring(PREFIX.length()));
            if (row == null) continue;
            boolean question = notice.getNotification().extras.getBoolean("beacon_question", false);
            boolean resolved = !row.active || row.isFresh(now, snapshot.requestStartedElapsedMs) && ("ready".equals(row.actualState)
                    || "working".equals(row.actualState) && (!row.reminderActive || row.reminderRemainingMs(now, snapshot.requestStartedElapsedMs) == 0));
            if (question && resolved) continue;
            String label = contextLine(context, row.source, row.sessionName, row.project);
            if (label.contentEquals(notice.getNotification().extras.getCharSequence(Notification.EXTRA_TEXT, ""))) continue;
            manager.notify(tag, ID, Notification.Builder.recoverBuilder(context, notice.getNotification())
                    .setContentText(label).setStyle(new Notification.BigTextStyle().bigText(label)).setOnlyAlertOnce(true).build());
        }
        // Coalesce each chat to its latest actionable event in this snapshot.
        for (AlertTracker.Event event : events) {
            boolean question = "awaiting_user".equals(event.state);
            String contextLine = contextLine(context, event.source, event.name, event.project);
            Snapshot.SessionRow row = AlertTracker.find(snapshot, event.key());
            long timeout = question && row != null && "working".equals(row.actualState) && row.reminderActive
                    ? Math.max(1L, row.reminderRemainingMs(now, snapshot.requestStartedElapsedMs))
                    : question ? 30L * 60 * 1000 : 5L * 60 * 1000;
            android.os.Bundle extras = new android.os.Bundle(); extras.putBoolean("beacon_question", question);
            Notification notification = new Notification.Builder(context, CHANNEL)
                    .setSmallIcon(question ? R.drawable.ic_beacon_waiting : R.drawable.ic_beacon_ready)
                    .setContentTitle(question ? AppText.tr(context, "Your answer is needed") : AppText.tr(context, "Ready for the next request"))
                    .setContentText(contextLine).setStyle(new Notification.BigTextStyle().bigText(contextLine))
                    .setColor(MainActivity.stateColor(event.state)).setCategory(question ? Notification.CATEGORY_MESSAGE : Notification.CATEGORY_STATUS)
                    .setAutoCancel(true).setOnlyAlertOnce(false).setTimeoutAfter(timeout)
                    .addExtras(extras).setContentIntent(PendingIntent.getActivity(context, 18, new Intent(context, MainActivity.class), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE)).build();
            manager.notify(PREFIX + event.key(), ID, notification);
        }
    }

    private static String contextLine(Context context, String source, String name, String project) {
        return SessionOverview.providerName(source) + " · " + AppText.chat(context, name)
                + (project == null || "new-chat".equalsIgnoreCase(project) ? "" : " · " + project);
    }

    static void languageChanged(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        Snapshot snapshot = SnapshotCache.load(context);
        for (StatusBarNotification notice : manager.getActiveNotifications()) {
            if (notice.getTag() == null || !notice.getTag().startsWith(PREFIX)) continue;
            Notification previous = notice.getNotification();
            long remaining = previous.getTimeoutAfter() - Math.max(0, System.currentTimeMillis() - notice.getPostTime());
            if (remaining <= 0) continue;
            boolean question = previous.extras.getBoolean("beacon_question", false);
            Notification.Builder builder = Notification.Builder.recoverBuilder(context, previous)
                    .setContentTitle(AppText.tr(context, question ? "Your answer is needed" : "Ready for the next request"))
                    .setOnlyAlertOnce(true).setTimeoutAfter(remaining);
            Snapshot.SessionRow row = snapshot == null ? null : AlertTracker.find(snapshot, notice.getTag().substring(PREFIX.length()));
            if (row != null) {
                String label = contextLine(context, row.source, row.sessionName, row.project);
                builder.setContentText(label).setStyle(new Notification.BigTextStyle().bigText(label));
            }
            manager.notify(notice.getTag(), ID, builder.build());
        }
    }

    static void cancelAll(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null) for (StatusBarNotification notice : manager.getActiveNotifications())
            if (notice.getTag() != null && notice.getTag().startsWith(PREFIX)) manager.cancel(notice.getTag(), ID);
    }
    private AttentionNotifier() {}
}
