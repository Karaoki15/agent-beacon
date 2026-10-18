package com.agentbeacon.android;

import android.content.Context;
import android.os.Handler;
import android.os.SystemClock;
import java.util.function.BooleanSupplier;

/** Apply all surfaces together; debounce green without waiting for another heartbeat. */
final class SnapshotDelivery {
    private final Handler handler;
    private Runnable readyCommit;

    SnapshotDelivery(Handler handler) { this.handler = handler; }

    void receive(Context context, Snapshot incoming, Credentials credentials, BooleanSupplier current, Runnable published) {
        cancel();
        if (!current.getAsBoolean()) return;
        Snapshot selected = SnapshotCache.saveIfNewer(context, incoming, credentials);
        if (selected != null) published.run();
        if (selected != incoming && "ready".equals(incoming.displayState(SystemClock.elapsedRealtime()))) {
            readyCommit = () -> {
                if (current.getAsBoolean() && incoming.isFresh(SystemClock.elapsedRealtime())
                        && SnapshotCache.saveConfirmed(context, incoming, credentials) != null) published.run();
            };
            handler.postDelayed(readyCommit, 1100L);
        }
    }

    void cancel() { if (readyCommit != null) handler.removeCallbacks(readyCommit); readyCommit = null; }
}
