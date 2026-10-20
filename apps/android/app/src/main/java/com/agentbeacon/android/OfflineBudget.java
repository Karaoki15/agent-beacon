package com.agentbeacon.android;

/** Time without a fresh, usable status snapshot. */
final class OfflineBudget {
    static final long TIMEOUT_MS = 30L * 60L * 1000L;
    private long lastContact;
    OfflineBudget(long now, long started, long received) {
        lastContact = started >= 0 && started <= now ? started : now;
        if (received >= lastContact && received <= now) lastContact = received;
    }
    void usable(long now) { if (now >= lastContact) lastContact = now; }
    long remaining(long now) { return Math.max(0, TIMEOUT_MS - Math.max(0, now - lastContact)); }
    boolean expired(long now) { return remaining(now) == 0; }
}
