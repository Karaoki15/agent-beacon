package com.agentbeacon.android;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.time.Instant;

final class Snapshot {
    final String instanceId;
    final long revision;
    final double validForSeconds;
    final boolean available;
    final boolean statesStabilized;
    final String state;
    final String taskState;
    final List<SessionRow> sessions;
    final List<HistoryEvent> history;
    final Map<String, Double> idleSources;
    final long requestStartedElapsedMs;
    final long receivedElapsedMs;
    private final String rawJson;

    private Snapshot(String instanceId, long revision, double validForSeconds, boolean available,
                     String state, String taskState, List<SessionRow> sessions, List<HistoryEvent> history, Map<String, Double> idleSources, long requestStartedElapsedMs, long receivedElapsedMs, String rawJson, boolean statesStabilized) {
        this.statesStabilized = statesStabilized;
        this.instanceId = instanceId; this.revision = revision; this.validForSeconds = validForSeconds;
        this.available = available; this.state = state; this.taskState = taskState; this.sessions = sessions;
        this.requestStartedElapsedMs = requestStartedElapsedMs;
        this.receivedElapsedMs = receivedElapsedMs;
        this.rawJson = rawJson;
        this.history = history;
        this.idleSources = idleSources;
    }

    @SuppressWarnings("unchecked")
    static Snapshot parseEvent(byte[] json, long streamStarted, long received) throws MiniJson.JsonException {
        Object value = MiniJson.parse(json);
        if (!(value instanceof Map)) throw new MiniJson.JsonException("Snapshot object required");
        Object elapsed = ((Map<String, Object>) value).get("stream_elapsed_ms");
        long serverWait = elapsed == null ? 0L : integer(elapsed, "stream_elapsed_ms");
        if (serverWait < 0 || serverWait > 10000L || serverWait > received - streamStarted) {
            throw new MiniJson.JsonException("Invalid stream timing");
        }
        // Subtract the server's measured waiting time, retaining request and
        // frame transit time as a conservative charge against freshness.
        return parse(json, streamStarted + serverWait, received);
    }

    @SuppressWarnings("unchecked")
    static Snapshot parse(byte[] json, long requestStartedElapsedMs, long receivedElapsedMs) throws MiniJson.JsonException {
        Object value = MiniJson.parse(json);
        if (!(value instanceof Map)) throw new MiniJson.JsonException("Snapshot object required");
        Map<String, Object> root = (Map<String, Object>) value;
        if (integer(root.get("schema_version"), "schema_version") != 1) throw new MiniJson.JsonException("Unsupported schema");
        if (root.containsKey("states_stabilized") && !(root.get("states_stabilized") instanceof Boolean)) throw new MiniJson.JsonException("Invalid stabilization flag");
        String instance = text(root.get("instance_id"), "instance_id", 128);
        long revision = integer(root.get("revision"), "revision");
        double valid = decimal(root.get("valid_for_seconds"), "valid_for_seconds");
        if (revision < 0 || valid < 0 || valid > 86400) throw new MiniJson.JsonException("Snapshot range");
        double ttl = decimal(root.get("ttl_seconds"), "ttl_seconds");
        if (ttl <= 0 || ttl > 86400) throw new MiniJson.JsonException("ttl_seconds range");
        validateTimestamp(root.get("server_time"), "server_time");
        Object taskState = root.get("task_state");
        if (taskState != null && (!(taskState instanceof String) || !isSessionState((String) taskState))) throw new MiniJson.JsonException("Invalid task_state");
        Object reason = root.get("reason");
        if (reason != null && (!(reason instanceof String) || ((String) reason).length() > 256)) throw new MiniJson.JsonException("Invalid reason");
        if (!(root.get("partial") instanceof Boolean)) throw new MiniJson.JsonException("partial required");
        Object availableValue = root.get("available");
        if (!(availableValue instanceof Boolean)) throw new MiniJson.JsonException("available required");
        boolean available = (Boolean) availableValue;
        Object stateValue = root.get("state");
        String state = stateValue == null ? null : text(stateValue, "state", 32);
        if (state != null && !isApiState(state)) throw new MiniJson.JsonException("Unknown aggregate state");
        if (available && state == null) throw new MiniJson.JsonException("Available snapshot needs a state");
        if (!available && (state != null || valid != 0)) throw new MiniJson.JsonException("Unavailable snapshot must have a null state and zero freshness");
        boolean invalidReady = "ready".equals(state) && Boolean.TRUE.equals(root.get("partial"));
        Object sessionValue = root.get("sessions");
        if (!(sessionValue instanceof List) || ((List<?>) sessionValue).size() > 128) throw new MiniJson.JsonException("sessions required");
        List<SessionRow> sessions = new ArrayList<SessionRow>();
        for (Object item : (List<?>) sessionValue) {
            if (!(item instanceof Map)) throw new MiniJson.JsonException("Invalid session");
            Map<String, Object> row = (Map<String, Object>) item;
            String source = text(row.get("source"), "source", 32);
            if (!"manual".equals(source) && !"codex".equals(source) && !"claude".equals(source) && !"antigravity".equals(source)) throw new MiniJson.JsonException("Unknown session source");
            Object rowAvailableValue = row.get("available");
            if (!(rowAvailableValue instanceof Boolean)) throw new MiniJson.JsonException("Session availability required");
            boolean rowAvailable = (Boolean) rowAvailableValue;
            String sessionId = text(row.get("session_id"), "session_id", 64);
            long sequence = integer(row.get("sequence"), "session sequence");
            if (sequence < 1) throw new MiniJson.JsonException("Invalid session sequence");
            validateTimestamp(row.get("updated_at"), "updated_at");
            Object projection = row.containsKey("display_state") ? row.get("display_state") : row.get("state");
            String rowState;
            if (projection == null && !rowAvailable) rowState = "unknown";
            else {
                rowState = text(projection, "session state", 32);
                if (!isSessionState(rowState)) throw new MiniJson.JsonException("Unknown session state");
            }
            if (!"manual".equals(source) && !(row.get("active") instanceof Boolean)) throw new MiniJson.JsonException("Observed session activity required");
            if ("manual".equals(source) && !(row.get("agent_id") instanceof String && safeIdentifier((String) row.get("agent_id")) != null)) throw new MiniJson.JsonException("Invalid manual agent_id");
            if (row.containsKey("turn_id") && row.get("turn_id") != null) text(row.get("turn_id"), "turn_id", 128);
            if (row.containsKey("question_reminder_active") && !(row.get("question_reminder_active") instanceof Boolean)) throw new MiniJson.JsonException("Invalid reminder state");
            if (row.containsKey("question_reminder_remaining_seconds")) {
                double remaining = decimal(row.get("question_reminder_remaining_seconds"), "reminder remaining");
                if (remaining < 0 || remaining > 86400) throw new MiniJson.JsonException("Reminder range");
            }
            String actualState = row.get("state") instanceof String && isSessionState((String) row.get("state")) ? (String) row.get("state") : rowState;
            boolean reminderActive = Boolean.TRUE.equals(row.get("question_reminder_active"));
            double reminderRemaining = row.get("question_reminder_remaining_seconds") instanceof Double
                    ? (Double) row.get("question_reminder_remaining_seconds") : 0.0;
            String project = row.get("project_name") instanceof String ? safeProject((String) row.get("project_name")) : null;
            String agent = row.get("agent_id") instanceof String ? safeIdentifier((String) row.get("agent_id")) : null;
            boolean active = !Boolean.FALSE.equals(row.get("active"));
            String statusReason = row.get("status_reason") instanceof String ? (String) row.get("status_reason") : null;
            double rowValid = row.containsKey("valid_for_seconds") ? decimal(row.get("valid_for_seconds"), "session freshness") : valid;
            if (rowValid < 0 || rowValid > 86400 || (!rowAvailable && rowValid > 0 && row.containsKey("valid_for_seconds"))) throw new MiniJson.JsonException("Invalid session freshness");
            String sessionName = null;
            if (row.containsKey("session_name") && row.get("session_name") != null) {
                Object name = row.get("session_name");
                if (!(name instanceof String) || ((String) name).length() > 320) throw new MiniJson.JsonException("Invalid session name");
                sessionName = safeName((String) name);
            }
            sessions.add(new SessionRow(source, project, agent, rowState, rowAvailable, actualState, reminderActive, reminderRemaining, active, statusReason, sessionId, sessionName, rowValid));
        }
        Map<String, Double> idleSources = new java.util.HashMap<>();
        Object sourceValue = root.get("sources");
        if (sourceValue instanceof List) {
            if (((List<?>) sourceValue).size() > 16) throw new MiniJson.JsonException("Too many sources");
            for (Object sourceItem : (List<?>) sourceValue) {
                if (!(sourceItem instanceof Map)) throw new MiniJson.JsonException("Invalid source row");
                Map<String, Object> source = (Map<String, Object>) sourceItem;
                String sourceName = text(source.get("source"), "source name", 32);
                if (!"codex".equals(sourceName) && !"claude".equals(sourceName) && !"antigravity".equals(sourceName)) throw new MiniJson.JsonException("Unknown provider source");
                Object coverage = source.get("coverage");
                if (coverage != null && (!(coverage instanceof String) || (!"all_sessions".equals(coverage) && !"observed_sessions".equals(coverage)))) throw new MiniJson.JsonException("Invalid source coverage");
                if (!(source.get("healthy") instanceof Boolean) || !(source.get("inventory_complete") instanceof Boolean)) throw new MiniJson.JsonException("Invalid source health");
                if (source.containsKey("idle_ready") && !(source.get("idle_ready") instanceof Boolean)) throw new MiniJson.JsonException("Invalid idle source");
                double sourceValid = source.containsKey("valid_for_seconds") ? decimal(source.get("valid_for_seconds"), "source freshness") : valid;
                if (sourceValid < 0 || sourceValid > 86400) throw new MiniJson.JsonException("Invalid source freshness");
                if (Boolean.TRUE.equals(source.get("idle_ready")) && Boolean.TRUE.equals(source.get("healthy"))
                        && Boolean.TRUE.equals(source.get("inventory_complete"))) idleSources.put(sourceName, sourceValid);
            }
        } else throw new MiniJson.JsonException("sources list required");
        List<HistoryEvent> history = new ArrayList<HistoryEvent>();
        if (root.containsKey("history")) {
            if (!(root.get("history") instanceof List) || ((List<?>) root.get("history")).size() > 30) throw new MiniJson.JsonException("Invalid history");
            long previousId = 0;
            for (Object eventValue : (List<?>) root.get("history")) {
                if (!(eventValue instanceof Map)) throw new MiniJson.JsonException("Invalid history entry");
                Map<String, Object> event = (Map<String, Object>) eventValue;
                long id = integer(event.get("id"), "history id"), count = integer(event.get("chat_count"), "history count");
                String eventState = text(event.get("state"), "history state", 32);
                if (id <= previousId || count < 0 || count > 128 || !isSessionState(eventState)
                        || !(event.get("reminder") instanceof Boolean) || !(event.get("partial") instanceof Boolean)) throw new MiniJson.JsonException("Invalid history metadata");
                validateTimestamp(event.get("at"), "history time");
                String project = event.get("project_name") instanceof String ? safeProject((String) event.get("project_name")) : null;
                String actor = event.containsKey("source") ? text(event.get("source"), "history source", 16) : null;
                if (actor != null && !"codex".equals(actor) && !"claude".equals(actor) && !"manual".equals(actor) && !"antigravity".equals(actor)) throw new MiniJson.JsonException("Invalid history source");
                String chatId = event.containsKey("session_id") ? text(event.get("session_id"), "history session", 64) : null;
                if (chatId != null && safeIdentifier(chatId) == null) throw new MiniJson.JsonException("Invalid history session");
                String chatName = event.get("session_name") != null ? safeName(text(event.get("session_name"), "history name", 320)) : null;
                String statusReason = event.get("status_reason") != null ? text(event.get("status_reason"), "history reason", 64) : null;
                history.add(new HistoryEvent(id, (String) event.get("at"), eventState, (Boolean) event.get("reminder"), (Boolean) event.get("partial"), (int) count, project, actor, chatId, chatName, statusReason));
                previousId = id;
            }
        }
        // Never render a server's contradictory ready claim as green.
        String rawJson = new String(json, java.nio.charset.StandardCharsets.UTF_8);
        String parsedTaskState = taskState instanceof String ? (String) taskState : null;
        if (invalidReady) return new Snapshot(instance, revision, 0, false, null, parsedTaskState, sessions, history, idleSources, requestStartedElapsedMs, receivedElapsedMs, rawJson, Boolean.TRUE.equals(root.get("states_stabilized")));
        return new Snapshot(instance, revision, valid, available, state, parsedTaskState, sessions, history, idleSources, requestStartedElapsedMs, receivedElapsedMs, rawJson, Boolean.TRUE.equals(root.get("states_stabilized")));
    }

    String effectiveState(long nowElapsedMs) {
        if (!isFresh(nowElapsedMs) || !available || state == null) return "unknown";
        return state;
    }

    String displayState(long nowElapsedMs) {
        String effective = effectiveState(nowElapsedMs);
        if ("unknown".equals(effective)) return effective;
        for (SessionRow row : sessions) {
            if (row.available && "awaiting_user".equals(row.actualState)) return "awaiting_user";
        }
        if (isQuestionReminderOnly(nowElapsedMs)) return "awaiting_user";
        if ("awaiting_user".equals(effective) && "working".equals(taskState)) return "working";
        return effective;
    }

    boolean isQuestionReminderOnly(long nowElapsedMs) {
        if (!isFresh(nowElapsedMs) || !available || !"awaiting_user".equals(state) || !"working".equals(taskState)) return false;
        boolean reminder = false;
        for (SessionRow row : sessions) {
            if (row.available && "awaiting_user".equals(row.actualState)) return false;
            if (row.available && "working".equals(row.actualState) && row.reminderActive
                    && row.reminderRemainingMs(nowElapsedMs, requestStartedElapsedMs) > 0) reminder = true;
        }
        return reminder;
    }

    long questionReminderRemainingMs(long nowElapsedMs) {
        long maximum = 0;
        for (SessionRow row : sessions) {
            if (row.available && "working".equals(row.actualState) && row.reminderActive && row.reminderRemainingSeconds > 0) {
                long remaining = row.reminderRemainingMs(nowElapsedMs, requestStartedElapsedMs);
                maximum = Math.max(maximum, remaining);
            }
        }
        return maximum;
    }

    boolean isFresh(long nowElapsedMs) {
        if (validForSeconds <= 0 || nowElapsedMs < receivedElapsedMs || nowElapsedMs < requestStartedElapsedMs) return false;
        // Start the lease when the request begins so network latency can only shorten it.
        double age = (nowElapsedMs - requestStartedElapsedMs) / 1000.0;
        return age < validForSeconds;
    }

    @SuppressWarnings("unchecked")
    String toJson() {
        return rawJson;
    }

    static Snapshot fromCache(String json, long requestStartedElapsedMs, long receivedElapsedMs) throws MiniJson.JsonException {
        return parse(json.getBytes(java.nio.charset.StandardCharsets.UTF_8), requestStartedElapsedMs, receivedElapsedMs);
    }

    private static boolean isApiState(String value) { return "working".equals(value) || "awaiting_user".equals(value) || "ready".equals(value); }
    private static boolean isSessionState(String value) { return isApiState(value) || "unknown".equals(value); }
    private static void validateTimestamp(Object value, String field) throws MiniJson.JsonException {
        String timestamp = text(value, field, 64);
        if (!timestamp.endsWith("Z")) throw new MiniJson.JsonException(field + " must be UTC");
        try { Instant.parse(timestamp); } catch (Exception error) { throw new MiniJson.JsonException("Invalid " + field); }
    }
    private static long integer(Object value, String field) throws MiniJson.JsonException {
        if (!(value instanceof Double)) throw new MiniJson.JsonException(field + " number required");
        double number = (Double) value;
        if (!Double.isFinite(number) || number != Math.rint(number) || number > 9007199254740991d || number < -9007199254740991d) throw new MiniJson.JsonException(field + " integer required");
        return (long) number;
    }
    private static double decimal(Object value, String field) throws MiniJson.JsonException {
        if (!(value instanceof Double)) throw new MiniJson.JsonException(field + " number required");
        double number = (Double) value;
        if (!Double.isFinite(number)) throw new MiniJson.JsonException(field + " finite number required");
        return number;
    }
    private static String text(Object value, String field, int max) throws MiniJson.JsonException {
        if (!(value instanceof String) || ((String) value).isEmpty() || ((String) value).length() > max) throw new MiniJson.JsonException(field + " text required");
        return (String) value;
    }
    private static String safeProject(String value) {
        if (value.length() < 1 || value.length() > 160) return null;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i); int type = Character.getType(c);
            if (c == '/' || c == '\\' || c == ':' || Character.isISOControl(c) || type == Character.FORMAT) return null;
        }
        return value;
    }
    private static String safeIdentifier(String value) {
        if (value.length() < 1 || value.length() > 64) return null;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return null;
        }
        return value;
    }

    private static String safeName(String value) {
        if (value.isEmpty()) return null;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (Character.isISOControl(c) || Character.getType(c) == Character.FORMAT) return null;
        }
        return value;
    }

    static final class SessionRow {
        final String source, project, agent, state, statusReason, sessionId, sessionName;
        final boolean available, reminderActive, active;
        final String actualState;
        final double reminderRemainingSeconds, validForSeconds;
        SessionRow(String source, String project, String agent, String state, boolean available, String actualState, boolean reminderActive, double reminderRemainingSeconds, boolean active, String statusReason, String sessionId, String sessionName, double validForSeconds) {
            this.source = source; this.project = project; this.agent = agent; this.state = state;
            this.available = available; this.actualState = actualState; this.reminderActive = reminderActive; this.reminderRemainingSeconds = reminderRemainingSeconds;
            this.active = active; this.statusReason = statusReason;
            this.sessionId = sessionId; this.sessionName = sessionName;
            this.validForSeconds = validForSeconds;
        }
        boolean isFresh(long now, long requestStarted) { return available && now >= requestStarted && now - requestStarted < validForSeconds * 1000; }
        long reminderRemainingMs(long nowElapsedMs, long requestStartedElapsedMs) {
            long elapsed = Math.max(0, nowElapsedMs - requestStartedElapsedMs);
            return Math.max(0, (long) (reminderRemainingSeconds * 1000.0) - elapsed);
        }
        String title() { return project != null ? project : (agent != null ? agent : source + " session"); }
    }

    static final class HistoryEvent {
        final long id;
        final String at, state, project, source, sessionId, sessionName, statusReason;
        final boolean reminder, partial;
        final int chatCount;
        HistoryEvent(long id, String at, String state, boolean reminder, boolean partial, int chatCount, String project, String source, String sessionId, String sessionName, String statusReason) {
            this.id = id; this.at = at; this.state = state; this.reminder = reminder; this.partial = partial; this.chatCount = chatCount; this.project = project;
            this.source = source; this.sessionId = sessionId; this.sessionName = sessionName; this.statusReason = statusReason;
        }
    }
}
