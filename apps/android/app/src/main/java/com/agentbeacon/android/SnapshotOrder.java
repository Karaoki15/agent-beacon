package com.agentbeacon.android;

/** Resolves overlapping activity/service polls without letting an old server generation return. */
final class SnapshotOrder {
    private SnapshotOrder() {}

    static boolean shouldReplace(Snapshot current, Snapshot incoming) {
        if (current == null) return true;
        if (!current.instanceId.equals(incoming.instanceId)) {
            return incoming.requestStartedElapsedMs > current.requestStartedElapsedMs;
        }
        if (incoming.revision != current.revision) return incoming.revision > current.revision;
        if (incoming.requestStartedElapsedMs != current.requestStartedElapsedMs) return incoming.requestStartedElapsedMs > current.requestStartedElapsedMs;
        return incoming.receivedElapsedMs > current.receivedElapsedMs;
    }
}
