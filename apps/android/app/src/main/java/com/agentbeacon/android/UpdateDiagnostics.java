package com.agentbeacon.android;

import android.os.SystemClock;

/** Process-local counters only: no credentials, prompts, session names, or answers. */
final class UpdateDiagnostics {
    private static long lastReceived, lastChange, responseMs, receivedCount;
    private static int failures;
    private static String transport = "Not connected", state = "unknown", error = "None";

    static synchronized void received(Snapshot snapshot, String mode, long connectionResponseMs) {
        long now = SystemClock.elapsedRealtime();
        StatusPresentation.received(snapshot);
        String nextState = snapshot.displayState(now);
        if (!nextState.equals(state) || lastChange == 0) lastChange = now;
        state = nextState; transport = mode; responseMs = connectionResponseMs;
        lastReceived = now; receivedCount++; failures = 0; error = "None";
    }

    static synchronized void failed(Exception failure) {
        StatusPresentation.retrying();
        failures++;
        error = failure instanceof BeaconClient.HttpFailure ? "HTTP " + ((BeaconClient.HttpFailure) failure).statusCode
                : failure instanceof java.net.SocketTimeoutException ? "Network timeout" : "Connection interrupted";
        transport = "Reconnecting";
    }

    static synchronized String summary(android.content.Context context, Snapshot snapshot, boolean running, boolean batterySaver) {
        long now = SystemClock.elapsedRealtime();
        long remaining = snapshot == null ? 0 : (long) Math.max(0, snapshot.validForSeconds * 1000 - (now - snapshot.requestStartedElapsedMs));
        return AppText.tr(context, "Connection") + "\n" + AppText.tr(context, transport)
                + "\n\n" + AppText.tr(context, "Background monitor") + "\n" + AppText.tr(context, running ? "Running" : "Stopped")
                + "\n\n" + AppText.tr(context, "Connection response") + "\n" + (lastReceived == 0 ? AppText.tr(context, "No data yet") : responseMs + AppText.tr(context, " ms"))
                + "\n\n" + AppText.tr(context, "Last update") + "\n" + age(context, lastReceived, now)
                + "\n\n" + AppText.tr(context, "Last state change received") + "\n" + age(context, lastChange, now)
                + "\n\n" + AppText.tr(context, "Fresh status remaining") + "\n" + ((remaining + 999) / 1000) + AppText.tr(context, " s")
                + "\n\n" + AppText.tr(context, "Snapshots received") + "\n" + receivedCount
                + "\n\n" + AppText.tr(context, "Consecutive connection errors") + "\n" + failures
                + "\n\n" + AppText.tr(context, "Last connection error") + "\n" + AppText.tr(context, error)
                + "\n\n" + AppText.tr(context, "Battery saver") + "\n" + AppText.tr(context, batterySaver ? "On" : "Off");
    }

    private static String age(android.content.Context context, long timestamp, long now) {
        return timestamp == 0 ? AppText.tr(context, "No data yet") : Math.max(0, (now - timestamp) / 1000) + AppText.tr(context, " s ago");
    }
    private UpdateDiagnostics() {}
}
