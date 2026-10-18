package com.agentbeacon.android;

import android.content.Context;

final class UpdateSettings {
    static boolean batterySaver(Context context) {
        return context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).getBoolean("battery_saver", false);
    }

    static void setBatterySaver(Context context, boolean enabled) {
        context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).edit().putBoolean("battery_saver", enabled).apply();
    }

    static long retryDelay(int failures, boolean saver) {
        return Math.min(saver ? 120000L : 30000L, 5000L << Math.min(Math.max(0, failures - 1), 5));
    }

    static long reconnectDelay(boolean saver) { return saver ? 1500L : 100L; }
    private UpdateSettings() {}
}
