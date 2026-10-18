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
        if (explicit || boot < 0 || prefs.getInt("monitor_started_boot", -2) != boot || started < 0 || started > now) {
            started = now;
            prefs.edit().putLong("monitor_started", started).putInt("monitor_started_boot", boot).apply();
        }
        Snapshot cached = SnapshotCache.load(context);
        return new OfflineBudget(now, started, cached == null ? -1 : cached.receivedElapsedMs);
    }
    static void pause(Context context, String reason) {
        context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).edit()
                .putBoolean("monitor_enabled", false).putString("monitor_pause_reason", reason).apply();
    }
    static boolean offlinePaused(Context context) {
        return "offline".equals(context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).getString("monitor_pause_reason", ""));
    }
    static void resumed(Context context) {
        context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).edit()
                .putBoolean("monitor_enabled", true).remove("monitor_pause_reason").apply();
    }
    private MonitorState() {}
}
