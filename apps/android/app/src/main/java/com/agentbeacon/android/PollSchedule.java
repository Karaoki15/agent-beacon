package com.agentbeacon.android;

/** One polling cadence shared by the visible app and background monitor. */
final class PollSchedule {
    private PollSchedule() {}

    static long delayMs(Snapshot snapshot, long now, boolean visible, int failures) {
        if (failures > 0) return Math.min(30000L, 5000L << Math.min(failures - 1, 3));
        if (visible) return 2000L;
        String state = snapshot == null ? "unknown" : snapshot.displayState(now);
        return "working".equals(state) || "awaiting_user".equals(state) ? 3000L : 5000L;
    }
}
