package com.agentbeacon.android;

import java.nio.charset.StandardCharsets;

/** Dependency-free checks for protocol parsing; run from apps/android/README.md. */
public final class ProtocolSmokeTest {
    private static int checks;

    public static void main(String[] args) throws Exception {
        String snapshot = "{\"schema_version\":1,\"instance_id\":\"instance-a\",\"revision\":7,"
                + "\"ttl_seconds\":30,\"valid_for_seconds\":2.5,\"available\":true,\"state\":\"awaiting_user\",\"task_state\":\"awaiting_user\",\"reason\":null,\"partial\":false,\"server_time\":\"2026-10-16T12:00:00.000Z\","
                + "\"sessions\":[{\"source\":\"codex\",\"session_id\":\"session-1\",\"turn_id\":\"turn-1\",\"sequence\":1,\"updated_at\":\"2026-10-16T12:00:00.000Z\",\"available\":true,\"active\":true,\"state\":\"working\",\"display_state\":\"awaiting_user\",\"project_name\":\"beacon\"}],\"sources\":[{\"source\":\"codex\",\"healthy\":true,\"inventory_complete\":true}]}";
        Snapshot parsed = Snapshot.parse(snapshot.getBytes(StandardCharsets.UTF_8), 900L, 1000L);
        check("awaiting_user".equals(parsed.effectiveState(2000L)), "fresh display state preserved");
        check("unknown".equals(parsed.effectiveState(3500L)), "expired state becomes unknown");
        check("beacon".equals(parsed.sessions.get(0).title()), "safe project basename visible");
        String legacySnapshotJson = snapshot.replace(",\"task_state\":\"awaiting_user\"", "")
                .replace(",\"display_state\":\"awaiting_user\"", "");
        Snapshot legacySnapshot = Snapshot.parse(legacySnapshotJson.getBytes(StandardCharsets.UTF_8), 900L, 1000L);
        check("awaiting_user".equals(legacySnapshot.effectiveState(1100L)) && "working".equals(legacySnapshot.sessions.get(0).state),
                "snapshots from older servers remain readable without additive projection fields");

        String reminderOnlyJson = "{\"schema_version\":1,\"instance_id\":\"instance-a\",\"revision\":10,"
                + "\"ttl_seconds\":30,\"valid_for_seconds\":5,\"available\":true,\"state\":\"awaiting_user\",\"task_state\":\"working\",\"reason\":null,\"partial\":false,\"server_time\":\"2026-10-16T12:00:00.000Z\","
                + "\"sessions\":[{\"source\":\"codex\",\"session_id\":\"session-1\",\"sequence\":1,\"updated_at\":\"2026-10-16T12:00:00.000Z\",\"available\":true,\"active\":true,\"state\":\"working\",\"display_state\":\"awaiting_user\",\"question_reminder_active\":true,\"question_reminder_remaining_seconds\":1.5},{\"source\":\"claude\",\"session_id\":\"session-2\",\"sequence\":1,\"updated_at\":\"2026-10-16T12:00:00.000Z\",\"available\":true,\"active\":true,\"state\":\"working\",\"display_state\":\"awaiting_user\",\"question_reminder_active\":true,\"question_reminder_remaining_seconds\":3.0}],\"sources\":[{\"source\":\"codex\",\"healthy\":true,\"inventory_complete\":true}]}";
        Snapshot reminderOnly = Snapshot.parse(reminderOnlyJson.getBytes(StandardCharsets.UTF_8), 900L, 1000L);
        check(reminderOnly.isQuestionReminderOnly(1100L), "working task with a fresh question reminder is not a user wait");
        check("awaiting_user".equals(reminderOnly.displayState(1100L)), "active reminder retains the yellow display state");
        check(reminderOnly.questionReminderRemainingMs(1400L) == 2500L, "aggregate reminder deadline follows the last active reminder");
        check(!reminderOnly.isQuestionReminderOnly(4100L) && "working".equals(reminderOnly.displayState(4100L)),
                "after every reminder expires, the confirmed working state is displayed");
        String actualWaitingJson = reminderOnlyJson.replace("\"revision\":10", "\"revision\":11")
                .replace("\"state\":\"working\",\"display_state\":\"awaiting_user\",\"question_reminder_active\":true,\"question_reminder_remaining_seconds\":1.5",
                        "\"state\":\"awaiting_user\",\"display_state\":\"awaiting_user\",\"question_reminder_active\":false,\"question_reminder_remaining_seconds\":0.0")
                .replace("\"sessions\":[{", "\"sessions\":[{\"source\":\"codex\",\"session_id\":\"session-waiting\",\"sequence\":2,\"updated_at\":\"2026-10-16T12:00:00.000Z\",\"available\":true,\"active\":true,\"state\":\"awaiting_user\",\"display_state\":\"awaiting_user\"},{");
        Snapshot actualWaiting = Snapshot.parse(actualWaitingJson.getBytes(StandardCharsets.UTF_8), 900L, 1000L);
        check(!actualWaiting.isQuestionReminderOnly(1100L), "available actual user wait takes priority over reminder state");
        check("awaiting_user".equals(actualWaiting.displayState(1100L)), "actual user wait stays yellow while another session has a reminder");
        check(!reminderOnly.isQuestionReminderOnly(7000L) && "unknown".equals(reminderOnly.effectiveState(7000L)),
                "stale reminder data remains unknown");

        String contradictoryReady = "{\"schema_version\":1,\"instance_id\":\"instance-a\",\"revision\":8,"
                + "\"ttl_seconds\":30,\"valid_for_seconds\":10,\"available\":true,\"state\":\"ready\",\"task_state\":\"ready\",\"reason\":null,\"partial\":true,\"server_time\":\"2026-10-16T12:00:00.000Z\",\"sessions\":[],\"sources\":[]}";
        Snapshot closed = Snapshot.parse(contradictoryReady.getBytes(StandardCharsets.UTF_8), 1000L, 1000L);
        check("unknown".equals(closed.effectiveState(1100L)), "partial ready is failed closed");
        String aggregateReady = "{\"schema_version\":1,\"instance_id\":\"instance-a\",\"revision\":9,"
                + "\"ttl_seconds\":30,\"valid_for_seconds\":10,\"available\":true,\"state\":\"ready\",\"task_state\":\"ready\",\"reason\":null,\"partial\":false,\"server_time\":\"2026-10-16T12:00:00.000Z\","
                + "\"sessions\":[{\"source\":\"codex\",\"session_id\":\"closed-session\",\"sequence\":1,\"updated_at\":\"2026-10-16T12:00:00.000Z\",\"available\":false,\"active\":false,\"state\":null,\"display_state\":null}],"
                + "\"sources\":[{\"source\":\"codex\",\"coverage\":\"observed_sessions\",\"healthy\":true,\"inventory_complete\":true},{\"source\":\"claude\",\"coverage\":\"observed_sessions\",\"healthy\":false,\"inventory_complete\":false}]}";
        Snapshot authoritativeReady = Snapshot.parse(aggregateReady.getBytes(StandardCharsets.UTF_8), 1000L, 1000L);
        check("ready".equals(authoritativeReady.effectiveState(1100L)), "server aggregate ready survives an empty unhealthy observed-only source and a closed unknown row");
        Snapshot oldInstance = Snapshot.parse(snapshot.replace("instance-a", "instance-old").getBytes(StandardCharsets.UTF_8), 900L, 1000L);
        Snapshot newInstance = Snapshot.parse(snapshot.replace("instance-a", "instance-new").replace("\"revision\":7", "\"revision\":1").getBytes(StandardCharsets.UTF_8), 1100L, 1200L);
        Snapshot delayedOldReply = Snapshot.parse(snapshot.replace("instance-a", "instance-old").replace("\"revision\":7", "\"revision\":99").getBytes(StandardCharsets.UTF_8), 950L, 1300L);
        check(SnapshotOrder.shouldReplace(oldInstance, newInstance), "new server generation accepted despite revision reset");
        check(!SnapshotOrder.shouldReplace(newInstance, delayedOldReply), "delayed old-generation response rejected");
        check(!SnapshotOrder.shouldReplace(newInstance, parsed), "older request cannot replace newer generation");

        PollGenerationGate gate = new PollGenerationGate();
        int firstGeneration = gate.current();
        check(gate.begin(firstGeneration), "first poll starts");
        check(!gate.begin(firstGeneration), "slow poll cannot queue a second in-flight request");
        gate.invalidate(); // ACTION_STOP invalidates the current poll.
        int restartedGeneration = gate.invalidate(); // A quick ACTION_START reuses the service.
        check(!gate.complete(firstGeneration), "queued stop-era reply releases the gate but is rejected");
        check(gate.begin(restartedGeneration), "restarted lifecycle poll starts after old request completes");
        check(gate.complete(restartedGeneration), "current lifecycle reply is accepted");

        expectJsonError("{\"x\":1,\"x\":2}");
        expectJsonError("{\"x\":NaN}");
        expectSnapshotError("{\"schema_version\":2}");

        String code = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq"; // 43 base64url characters
        PairingInput next = PairingInput.parse("agentbeacon://pair?host=192.168.1.10&port=8765#code=" + code);
        check(!next.legacy && "192.168.1.10".equals(next.host) && next.port == 8765, "new private pairing link accepted");
        PairingInput old = PairingInput.parse("http://10.10.2.3:8765/#token=abcdefghijklmnopqrstuvwxyz0123456789");
        check(old.legacy && old.port == 8765, "legacy root link accepted");
        expectPairingError("agentbeacon://pair?host=8.8.8.8&port=8765#code=" + code);
        expectPairingError("agentbeacon://pair?host=agent.local&port=8765#code=" + code);
        expectPairingError("http://192.168.1.10:8765/status#token=abcdefghijklmnopqrstuvwxyz0123456789");
        System.out.println("Protocol smoke checks passed: " + checks);
    }

    private static void expectJsonError(String json) throws Exception {
        try { MiniJson.parse(json.getBytes(StandardCharsets.UTF_8)); throw new AssertionError("Expected strict JSON rejection"); }
        catch (MiniJson.JsonException expected) { checks++; }
    }
    private static void expectSnapshotError(String json) throws Exception {
        try { Snapshot.parse(json.getBytes(StandardCharsets.UTF_8), 9L, 10L); throw new AssertionError("Expected snapshot rejection"); }
        catch (MiniJson.JsonException expected) { checks++; }
    }
    private static void expectPairingError(String input) throws Exception {
        try { PairingInput.parse(input); throw new AssertionError("Expected pairing rejection"); }
        catch (IllegalArgumentException expected) { checks++; }
    }
    private static void check(boolean result, String message) {
        if (!result) throw new AssertionError(message);
        checks++;
    }
}
