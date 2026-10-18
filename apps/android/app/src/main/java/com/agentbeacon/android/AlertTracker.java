package com.agentbeacon.android;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Bounded persisted transition memory; heartbeat and reconnection never imply completion. */
final class AlertTracker {
    static final class Event {
        final String source, sessionId, name, project, state;
        Event(String source, String sessionId, String name, String project, String state) {
            this.source = source; this.sessionId = sessionId; this.name = name; this.project = project; this.state = state;
        }
        String key() { return source + ":" + sessionId; }
    }
    private final LinkedHashMap<String, String> states = new LinkedHashMap<>();
    private String instance = "";
    private long cursor;
    private Snapshot latest;

    @SuppressWarnings("unchecked")
    AlertTracker(String saved) {
        if (saved == null) return;
        try {
            Object decoded = MiniJson.parse(saved.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            if (!(decoded instanceof Map)) return;
            Map<?, ?> data = (Map<?, ?>) decoded;
            Object rawStates = data.get("states"), rawCursor = data.get("cursor"), rawInstance = data.get("instance");
            if (!(rawStates instanceof Map) || ((Map<?, ?>) rawStates).size() > 128
                    || !(rawCursor instanceof Double) || !(rawInstance instanceof String) || ((String) rawInstance).length() > 128) return;
            double number = (Double) rawCursor;
            if (number < 0 || number > 9007199254740991d || number != Math.rint(number)) return;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) rawStates).entrySet()) {
                if (!(entry.getKey() instanceof String) || !((String) entry.getKey()).matches("(?:codex|claude|antigravity):[A-Za-z0-9._-]{1,64}")
                        || !(entry.getValue() instanceof String) || !validState((String) entry.getValue())) return;
            }
            instance = (String) rawInstance; cursor = (long) number;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) rawStates).entrySet()) states.put((String) entry.getKey(), (String) entry.getValue());
        } catch (Exception invalid) { states.clear(); instance = ""; cursor = 0; }
    }

    boolean isCurrent(Snapshot snapshot) {
        if (latest != null && !SnapshotOrder.shouldReplace(latest, snapshot)) return false;
        long lastId = snapshot.history.isEmpty() ? 0 : snapshot.history.get(snapshot.history.size() - 1).id;
        return !snapshot.instanceId.equals(instance) || lastId >= cursor;
    }

    List<Event> accept(Snapshot snapshot, long now) {
        // Activity and service requests can overlap. A late response must not
        // rewind the cursor or restore an already answered question.
        if (!isCurrent(snapshot)) return new ArrayList<>();
        latest = snapshot;
        LinkedHashMap<String, Event> pending = new LinkedHashMap<>();
        boolean sameInstance = snapshot.instanceId.equals(instance);
        if (sameInstance) for (Snapshot.HistoryEvent event : snapshot.history) {
            if (event.id <= cursor || event.source == null || event.sessionId == null || "manual".equals(event.source)) continue;
            if ("closed".equals(event.statusReason)) { remember(event.source + ":" + event.sessionId, "closed"); continue; }
            // Async questions can resume work before the one-second display
            // confirmation. Their unanswered reminder still needs one alert.
            String state = event.reminder ? "awaiting_user" : event.state;
            transition(new Event(event.source, event.sessionId, event.sessionName, event.project, state), pending);
        }
        // A first snapshot establishes ready as a baseline, never a mass "done" alert.
        // Current rows also cover servers with no actor-level history.
        for (Snapshot.SessionRow row : snapshot.sessions) {
            if ("manual".equals(row.source)) continue;
            if (!row.active) { remember(row.source + ":" + row.sessionId, "closed"); continue; }
            if (now < snapshot.receivedElapsedMs || !row.isFresh(now, snapshot.requestStartedElapsedMs)) continue;
            String state = "working".equals(row.actualState) && row.reminderActive
                    && row.reminderRemainingMs(now, snapshot.requestStartedElapsedMs) > 0 ? "awaiting_user" : row.actualState;
            transition(new Event(row.source, row.sessionId, row.sessionName, row.project, state), pending);
        }
        instance = snapshot.instanceId;
        cursor = snapshot.history.isEmpty() ? 0 : snapshot.history.get(snapshot.history.size() - 1).id;
        List<Event> result = new ArrayList<>();
        for (Event event : pending.values()) {
            Snapshot.SessionRow row = find(snapshot, event.key());
            if (row == null || !row.active || !row.isFresh(now, snapshot.requestStartedElapsedMs)) continue;
            boolean unanswered = "awaiting_user".equals(row.actualState) || ("working".equals(row.actualState)
                    && row.reminderActive && row.reminderRemainingMs(now, snapshot.requestStartedElapsedMs) > 0);
            if ("awaiting_user".equals(event.state) && unanswered || "ready".equals(event.state) && "ready".equals(row.actualState))
                result.add(new Event(event.source, event.sessionId, row.sessionName != null ? row.sessionName : event.name,
                        row.project != null ? row.project : event.project, event.state));
        }
        return result;
    }

    private void transition(Event event, Map<String, Event> pending) {
        if (!validState(event.state) || "closed".equals(event.state)) return;
        String previous = states.get(event.key());
        if ("awaiting_user".equals(event.state) && !"awaiting_user".equals(previous)
                || "ready".equals(event.state) && ("working".equals(previous) || "awaiting_user".equals(previous))) pending.put(event.key(), event);
        remember(event.key(), event.state);
    }
    private void remember(String key, String state) {
        states.remove(key); states.put(key, state);
        while (states.size() > 128) states.remove(states.keySet().iterator().next());
    }
    static Snapshot.SessionRow find(Snapshot snapshot, String key) {
        for (Snapshot.SessionRow row : snapshot.sessions) if ((row.source + ":" + row.sessionId).equals(key)) return row;
        return null;
    }
    String save() {
        Map<String, Object> data = new LinkedHashMap<>(); data.put("instance", instance); data.put("cursor", cursor); data.put("states", states);
        return MiniJson.stringify(data);
    }
    private static boolean validState(String state) { return "working".equals(state) || "awaiting_user".equals(state) || "ready".equals(state) || "closed".equals(state); }
}
