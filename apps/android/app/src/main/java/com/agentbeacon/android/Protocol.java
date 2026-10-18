package com.agentbeacon.android;

final class Protocol {
    static final int MAX_RESPONSE_BYTES = 128 * 1024;
    static final int CONNECT_TIMEOUT_MS = 5000;
    static final int READ_TIMEOUT_MS = 5000;
    static final long REQUEST_DEADLINE_MS = 15000L;
    static final long POLL_INTERVAL_MS = 5000L;
    private Protocol() {}
}
