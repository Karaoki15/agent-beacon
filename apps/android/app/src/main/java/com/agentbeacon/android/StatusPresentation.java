package com.agentbeacon.android;

import android.content.Context;
import android.os.SystemClock;

/** A bounded, explicitly labelled last-known view; never renews snapshot freshness. */
final class StatusPresentation {
    static final long RECOVERY_MS = 8000L;
    private static long recoveryStarted;
    final String state;
    final boolean reconnecting;
    final long remainingMs;

    private StatusPresentation(String state, boolean reconnecting, long remainingMs) {
        this.state = state; this.reconnecting = reconnecting; this.remainingMs = remainingMs;
    }

    static void retrying() { if (recoveryStarted == 0) recoveryStarted = SystemClock.elapsedRealtime(); }
    static void reset() { recoveryStarted = 0; }

    static void received(Snapshot snapshot) {
        if (!"unknown".equals(snapshot.displayState(SystemClock.elapsedRealtime()))) recoveryStarted = 0;
        else retrying();
    }

    static StatusPresentation select(Context context, Snapshot current, boolean active, long now) {
        if (current != null && !"unknown".equals(current.displayState(now))) {
            long remaining = (long) Math.max(0, current.validForSeconds * 1000 - (now - current.requestStartedElapsedMs));
            return new StatusPresentation(current.displayState(now), false, remaining);
        }
        if (active) {
            Snapshot last = SnapshotCache.loadLastKnown(context);
            if (last != null && last.available && last.validForSeconds > 0) {
                retrying();
                long expires = last.requestStartedElapsedMs + (long) Math.ceil(last.validForSeconds * 1000);
                long remaining = Math.min(expires + RECOVERY_MS, recoveryStarted + RECOVERY_MS) - now;
                if (remaining > 0) {
                    String state = last.displayState(Math.min(now, expires - 1L));
                    if (!"unknown".equals(state)) return new StatusPresentation(state, true, remaining);
                }
            }
        }
        return new StatusPresentation("unknown", false, 0);
    }
}
