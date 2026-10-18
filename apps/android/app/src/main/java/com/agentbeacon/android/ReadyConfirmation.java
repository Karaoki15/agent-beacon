package com.agentbeacon.android;

/** Confirm a brief green transition without extending the previous snapshot's lease. */
final class ReadyConfirmation {
    private Snapshot pending;
    private String credential;

    Snapshot select(Snapshot existing, Snapshot incoming, String fingerprint, long now) {
        if (incoming.statesStabilized) { reset(); return incoming; }
        if (!fingerprint.equals(credential)) { reset(); credential = fingerprint; }
        if (pending != null && !SnapshotOrder.shouldReplace(pending, incoming)) return existing;
        if (!"ready".equals(incoming.displayState(now)) || existing == null || !existing.isFresh(now)
                || !existing.instanceId.equals(incoming.instanceId)
                || "ready".equals(existing.displayState(now))) {
            pending = null;
            return incoming;
        }
        if (pending == null || !pending.instanceId.equals(incoming.instanceId)) {
            pending = incoming;
            return existing;
        }
        // Require another request, rather than two completions of one request.
        if (incoming.requestStartedElapsedMs - pending.requestStartedElapsedMs < 1000L) return existing;
        pending = null;
        return incoming;
    }

    void reset() { pending = null; credential = null; }
}
