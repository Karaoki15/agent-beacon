package com.agentbeacon.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.provider.Settings;

final class MonitorState {
    static OfflineBudget begin(Context context, boolean explicit) {
        SharedPreferences prefs = context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE);
        long now = SystemClock.elapsedRealtime(), started = prefs.getLong("monitor_started", -1);
        int boot;
        try { boot = Settings.Global.getInt(context.getContentResolver(), Settings.Global.BOOT_COUNT, -1); }
        catch (RuntimeException unavailable) { boot = -1; }
        boolean newOpportunity = explicit || boot < 0 || prefs.getInt("monitor_started_boot", -2) != boot || started < 0 || started > now;
        if (newOpportunity) {
            started = now;
            prefs.edit().putLong("monitor_started", started).putInt("monitor_started_boot", boot).apply();
        }
        Snapshot cached = SnapshotCache.load(context);
        long lastUsable = prefs.getLong("monitor_last_usable", -1);
        if (boot < 0 || prefs.getInt("monitor_last_usable_boot", -2) != boot || lastUsable < 0 || lastUsable > now) lastUsable = -1;
        if (cached != null && cached.isUsefulForOfflineBudget(now) && cached.receivedElapsedMs > lastUsable) {
            lastUsable = cached.receivedElapsedMs;
            prefs.edit().putLong("monitor_last_usable", lastUsable).putInt("monitor_last_usable_boot", boot).apply();
        }
        if (newOpportunity) {
            lastUsable = now;
            prefs.edit().putLong("monitor_last_usable", now).putInt("monitor_last_usable_boot", boot).apply();
        }
        return new OfflineBudget(now, lastUsable >= 0 ? lastUsable : started, -1);
    }
    static void usable(Context context, long now) {
        int boot;
        try { boot = Settings.Global.getInt(context.getContentResolver(), Settings.Global.BOOT_COUNT, -1); }
        catch (RuntimeException unavailable) { boot = -1; }
        context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).edit()
                .putLong("monitor_last_usable", now).putInt("monitor_last_usable_boot", boot).apply();
    }
    static void newPairing(Context context) {
        long now = SystemClock.elapsedRealtime();
        int boot;
        try { boot = Settings.Global.getInt(context.getContentResolver(), Settings.Global.BOOT_COUNT, -1); }
        catch (RuntimeException unavailable) { boot = -1; }
        context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).edit()
                .putLong("monitor_started", now).putInt("monitor_started_boot", boot)
                .remove("monitor_last_usable").putInt("monitor_last_usable_boot", boot).apply();
    }
    static void pause(Context context, String reason) {
        context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).edit()
                .putBoolean("monitor_enabled", false).putString("monitor_pause_reason", reason).apply();
    }
    static boolean offlinePaused(Context context) {
        return "offline".equals(context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).getString("monitor_pause_reason", ""));
    }
    static boolean manuallyPaused(Context context) {
        return "manual".equals(context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).getString("monitor_pause_reason", ""));
    }
    static void resumed(Context context) {
        context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).edit()
                .putBoolean("monitor_enabled", true).remove("monitor_pause_reason").apply();
    }
    private MonitorState() {}
}
