package com.agentbeacon.android;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.os.Build;
import android.os.SystemClock;
import android.util.Log;

import java.lang.reflect.Method;

final class StatusNotifier {
    static final String CHANNEL_MONITOR = "monitor_quiet_v2";
    static final String CHANNEL_STATUS = "status_quiet_v2";
    static final int MONITOR_ID = 4100;
    static final int STATUS_ID = 4101;
    private static final String PREFS = "beacon_runtime";
    private static String lastStatusKey;
    private static long refreshAfter;
    private static long postedExpiry;
    private static boolean lastPromotionRequested;
    private StatusNotifier() {}

    static void resetPresentation() { lastStatusKey = null; refreshAfter = 0L; postedExpiry = 0L; }

    static void languageChanged(Context context) {
        ensureChannels(context); resetPresentation();
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null && StatusService.isRunning() && enabled(context)) manager.notify(MONITOR_ID, monitoring(context));
        Snapshot snapshot = SnapshotCache.load(context);
        if (snapshot != null && StatusService.isRunning()) postStatus(context, snapshot);
        AttentionNotifier.languageChanged(context);
    }

    static void ensureChannels(Context context) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        if (manager.getNotificationChannel(CHANNEL_MONITOR) == null) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_MONITOR, AppText.tr(context, "Background monitoring (silent)"), NotificationManager.IMPORTANCE_LOW);
            channel.setSound(null, null); channel.enableVibration(false); channel.setShowBadge(false);
            channel.setDescription(AppText.tr(context, "Shows while Agent Beacon checks your paired private server.")); manager.createNotificationChannel(channel);
        }
        if (manager.getNotificationChannel(CHANNEL_STATUS) == null) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_STATUS, AppText.tr(context, "Agent status (silent)"), NotificationManager.IMPORTANCE_LOW);
            channel.setSound(null, null); channel.enableVibration(false); channel.setShowBadge(false);
            channel.setDescription(AppText.tr(context, "Fresh state updates from Agent Beacon.")); manager.createNotificationChannel(channel);
        }
        NotificationChannel monitor = manager.getNotificationChannel(CHANNEL_MONITOR);
        monitor.setName(AppText.tr(context, "Background monitoring (silent)"));
        monitor.setDescription(AppText.tr(context, "Shows while Agent Beacon checks your paired private server."));
        manager.createNotificationChannel(monitor);
        NotificationChannel status = manager.getNotificationChannel(CHANNEL_STATUS);
        status.setName(AppText.tr(context, "Agent status (silent)"));
        status.setDescription(AppText.tr(context, "Fresh state updates from Agent Beacon."));
        manager.createNotificationChannel(status);
        AttentionNotifier.ensureChannel(context);
    }

    static boolean enabled(Context context) {
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null || !manager.areNotificationsEnabled()) return false;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel monitor = manager.getNotificationChannel(CHANNEL_MONITOR);
            NotificationChannel status = manager.getNotificationChannel(CHANNEL_STATUS);
            return monitor != null && monitor.getImportance() != NotificationManager.IMPORTANCE_NONE
                    && status != null && status.getImportance() != NotificationManager.IMPORTANCE_NONE;
        }
        return true;
    }

    static Notification monitoring(Context context) {
        ensureChannels(context);
        Intent open = new Intent(context, MainActivity.class);
        PendingIntent content = PendingIntent.getActivity(context, 11, open, PendingIntent.FLAG_UPDATE_CURRENT | immutableFlag());
        Intent stop = new Intent(context, StatusService.class).setAction(StatusService.ACTION_STOP);
        PendingIntent stopPending = PendingIntent.getService(context, 12, stop, PendingIntent.FLAG_UPDATE_CURRENT | immutableFlag());
        return new Notification.Builder(context, CHANNEL_MONITOR)
                .setSmallIcon(R.drawable.ic_beacon_monitor).setContentTitle(AppText.tr(context, "Agent Beacon is monitoring"))
                .setContentText(AppText.tr(context, "Keeping your agent status up to date")).setContentIntent(content)
                .setShowWhen(false).setOngoing(true).setOnlyAlertOnce(true).addAction(android.R.drawable.ic_media_pause, AppText.tr(context, "Stop"), stopPending).build();
    }

    static void postStatus(Context context, Snapshot snapshot) {
        if (!enabled(context)) return;
        long now = SystemClock.elapsedRealtime();
        StatusPresentation presentation = StatusPresentation.select(context, snapshot, StatusService.isRunning(), now);
        boolean reminderOnly = !presentation.reconnecting && snapshot.isQuestionReminderOnly(now);
        String state = presentation.state;
        boolean suppressed = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("promoted_suppressed", false);
        if (suppressed) { cancelStatus(context); return; }
        boolean requestPromotion = !presentation.reconnecting && !reminderOnly && snapshot.isFresh(now)
                && ("working".equals(state) || "awaiting_user".equals(state))
                && context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("promoted_opt_in", false)
                && !suppressed;
        int color = MainActivity.stateColor(state);
        long remaining = presentation.remainingMs;
        long timeout = Math.max(1, Math.min(86400000L, remaining));
        String statusKey = AppLanguage.language(context) + ":" + state + ":" + reminderOnly + ":" + requestPromotion + ":" + presentation.reconnecting;
        long expiry = now + remaining;
        // Renew the timed notice before its lease ends, while avoiding a full
        // notification repaint for every unchanged status response.
        if (statusKey.equals(lastStatusKey) && now < refreshAfter
                && ("unknown".equals(state) || expiry >= postedExpiry - 100L)) return;
        Notification.Builder builder = new Notification.Builder(context, CHANNEL_STATUS)
                .setSmallIcon(iconFor(state)).setLargeIcon(statusDot(color))
                .setContentTitle(presentation.reconnecting ? AppText.tr(context, "Last known: ") + AppText.state(context, state)
                        : reminderOnly ? AppText.tr(context, "Question reminder") : "Agent Beacon: " + AppText.state(context, state))
                .setContentText(presentation.reconnecting ? AppText.tr(context, "Reconnecting to your server…")
                        : reminderOnly ? AppText.reminder(context, snapshot.questionReminderRemainingMs(now)) : AppText.tr(context, "Your agents on this computer"))
                .setColor(color).setColorized(false).setShowWhen(false)
                .setTimeoutAfter("unknown".equals(state) ? 0L : timeout).setAutoCancel(!requestPromotion).setOngoing(requestPromotion).setOnlyAlertOnce(true)
                .setContentIntent(PendingIntent.getActivity(context, 13, new Intent(context, MainActivity.class), PendingIntent.FLAG_UPDATE_CURRENT | immutableFlag()));
        boolean promotionRequested = requestPromotion && requestPromotedOngoing(builder);
        if (requestPromotion && !promotionRequested) {
            builder.setOngoing(false).setAutoCancel(true);
        } else if (requestPromotion) {
            Intent dismissed = new Intent(context, PromotedDismissReceiver.class).setAction(PromotedDismissReceiver.ACTION_DISMISSED);
            builder.setDeleteIntent(PendingIntent.getBroadcast(context, 14, dismissed, PendingIntent.FLAG_UPDATE_CURRENT | immutableFlag()));
        }
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        manager.notify(STATUS_ID, builder.build());
        lastPromotionRequested = promotionRequested;
        lastStatusKey = statusKey;
        postedExpiry = expiry;
        refreshAfter = now + ("unknown".equals(state) ? 30000L : Math.max(1L, Math.min(5000L, remaining / 2L)));
    }

    private static int iconFor(String state) {
        if ("working".equals(state)) return R.drawable.ic_beacon_working;
        if ("awaiting_user".equals(state)) return R.drawable.ic_beacon_waiting;
        if ("ready".equals(state)) return R.drawable.ic_beacon_ready;
        return R.drawable.ic_beacon_unknown;
    }

    private static Bitmap statusDot(int color) {
        Bitmap bitmap = Bitmap.createBitmap(64, 64, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(color); paint.setAlpha(45); canvas.drawCircle(32, 32, 31, paint);
        paint.setAlpha(255); canvas.drawCircle(32, 32, 24, paint);
        return bitmap;
    }

    static boolean supportsPromotedOngoing() {
        try { Notification.Builder.class.getMethod("setRequestPromotedOngoing", boolean.class); return true; }
        catch (NoSuchMethodException unavailable) { return false; }
    }

    static String liveUpdateDiagnostics(Context context) {
        if (!supportsPromotedOngoing()) return AppText.tr(context, "Not supported by this Android version");
        boolean requested = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("promoted_opt_in", false);
        String permission = AppText.tr(context, "Not reported by this Android version");
        try {
            NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
            Method method = NotificationManager.class.getMethod("canPostPromotedNotifications");
            permission = Boolean.TRUE.equals(method.invoke(manager)) ? AppText.tr(context, "Allowed") : AppText.tr(context, "Not allowed");
        } catch (ReflectiveOperationException | RuntimeException unavailable) {
            // Older Android releases expose the builder without the permission query.
        }
        return AppText.tr(context, "Switch: ") + (requested ? AppText.tr(context, "On") : AppText.tr(context, "Off"))
                + "\n" + AppText.tr(context, "System permission: ") + permission
                + "\n" + AppText.tr(context, "Last status requested promotion: ") + (lastPromotionRequested ? AppText.tr(context, "Yes") : AppText.tr(context, "No"))
                + "\n" + AppText.tr(context, "Final placement is decided by Android/Samsung. LockStar widgets are separate.");
    }

    private static boolean requestPromotedOngoing(Notification.Builder builder) {
        try {
            Method method = Notification.Builder.class.getMethod("setRequestPromotedOngoing", boolean.class);
            method.invoke(builder, true);
            return true;
        } catch (Exception error) {
            Log.w("AgentBeacon", "Live Update request unavailable (" + error.getClass().getSimpleName() + ")");
            return false;
        }
    }

    static void cancelStatus(Context context) {
        lastPromotionRequested = false;
        lastStatusKey = null; refreshAfter = 0L; postedExpiry = 0L;
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) manager.cancel(STATUS_ID);
    }

    private static int immutableFlag() { return Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0; }
}
