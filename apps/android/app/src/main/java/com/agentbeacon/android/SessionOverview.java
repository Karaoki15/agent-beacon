package com.agentbeacon.android;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Human-facing project/provider summaries; unavailable chats still block ready. */
final class SessionOverview {
    static final class Project {
        final String title, source;
        String state = "ready", explanation = "";
        int count, missing, working, waiting, ready, reminders;
        Project(String title, String source) { this.title = title; this.source = source; }
        String detail() {
            String countText = count + (count == 1 ? " tracked chat" : " tracked chats");
            if (missing == count) return countText + " · " + explanation;
            if (missing > 0) return countText + " · " + missing + " not reporting";
            if (reminders > 0 && waiting == reminders) return countText + " · Question reminder";
            return countText;
        }
    }

    static List<Project> projects(Snapshot snapshot, long now) {
        Map<String, Project> grouped = new LinkedHashMap<>();
        if (snapshot == null) return new ArrayList<>();
        for (Snapshot.SessionRow row : snapshot.sessions) {
            if (!row.active) continue;
            String title = row.project != null ? row.project : providerName(row.source) + " chat";
            String key = row.source + ":" + title;
            Project project = grouped.get(key);
            if (project == null) { project = new Project(title, row.source); grouped.put(key, project); }
            add(project, snapshot, row, now);
        }
        List<Project> result = new ArrayList<>(grouped.values());
        result.sort((left, right) -> Integer.compare(priority(right.state), priority(left.state)));
        return result;
    }

    static Project provider(Snapshot snapshot, String source, long now) {
        Project result = new Project(providerName(source), source);
        if (snapshot != null) for (Snapshot.SessionRow row : snapshot.sessions) if (row.active && source.equals(row.source)) add(result, snapshot, row, now);
        if (result.count == 0) { result.state = "unknown"; result.explanation = "No tracked chats"; }
        if (result.count == 0 && snapshot != null && snapshot.idleSources.containsKey(source)
                && now >= snapshot.receivedElapsedMs && now - snapshot.requestStartedElapsedMs < snapshot.idleSources.get(source) * 1000) {
            result.state = "ready"; result.explanation = "No current work";
        }
        return result;
    }

    static final class Chat {
        final String title, project, source, state, detail, id;
        final boolean untitled, projectMissing;
        Chat(String title, String project, String source, String state, String detail, String id, boolean untitled, boolean projectMissing) {
            this.title = title; this.project = project; this.source = source; this.state = state; this.detail = detail; this.id = id; this.untitled = untitled; this.projectMissing = projectMissing;
        }
    }

    static List<Chat> chats(Snapshot snapshot, long now) {
        List<Chat> result = new ArrayList<>();
        if (snapshot == null) return result;
        for (Snapshot.SessionRow row : snapshot.sessions) {
            if (!row.active) continue;
            Project summary = new Project("", row.source); add(summary, snapshot, row, now);
            String title = chatName(row.sessionName);
            String detail = "unknown".equals(summary.state) ? reason(row.statusReason)
                    : summary.reminders > 0 ? "Question reminder · agent is working"
                    : "awaiting_first_request".equals(row.statusReason) ? "Ready for the first request"
                    : "ready".equals(summary.state) ? "Recently finished" : "";
            result.add(new Chat(title, projectName(row.project), row.source, summary.state, detail, row.sessionId, row.sessionName == null || row.sessionName.trim().isEmpty(), row.project == null || "new-chat".equalsIgnoreCase(row.project)));
        }
        result.sort((left, right) -> {
            int order = Integer.compare(priority(right.state), priority(left.state));
            return order != 0 ? order : (left.source + left.id).compareTo(right.source + right.id);
        });
        return result;
    }

    static String chatName(String name) {
        return name != null && !name.trim().isEmpty() ? name : "Untitled chat";
    }

    static String projectName(String project) {
        return project == null || "new-chat".equalsIgnoreCase(project) ? "Project not provided" : project;
    }

    static String shortId(String value) {
        if (value == null) return "";
        String cleaned = value.replaceAll("[^A-Za-z0-9]", "");
        return cleaned.substring(Math.max(0, cleaned.length() - 12)).toUpperCase(java.util.Locale.ROOT);
    }

    static int taskCount(Snapshot snapshot, long now) {
        int count = 0;
        for (Chat chat : chats(snapshot, now)) if ("working".equals(chat.state) || "awaiting_user".equals(chat.state)) count++;
        return count;
    }

    private static void add(Project project, Snapshot snapshot, Snapshot.SessionRow row, long now) {
        project.count++;
        String state = "unknown";
        if (now >= snapshot.receivedElapsedMs && row.isFresh(now, snapshot.requestStartedElapsedMs)) {
            state = row.actualState;
            if ("working".equals(state) && row.reminderActive && row.reminderRemainingMs(now, snapshot.requestStartedElapsedMs) > 0) {
                state = "awaiting_user"; project.reminders++;
            }
        }
        if ("working".equals(state)) project.working++;
        else if ("awaiting_user".equals(state)) project.waiting++;
        else if ("ready".equals(state)) project.ready++;
        else { state = "unknown"; project.missing++; project.explanation = reason(row.statusReason); }
        if (priority(state) > priority(project.state)) project.state = state;
    }

    static String providerName(String source) {
        return "codex".equals(source) ? "Codex" : "claude".equals(source) ? "Claude Code" : "antigravity".equals(source) ? "Antigravity" : "Demo";
    }

    static String reason(String reason) {
        if ("awaiting_activity".equals(reason)) return "Waiting for the first request";
        if ("observer_restarted".equals(reason)) return "Waiting to reconnect after a restart";
        if ("closed".equals(reason)) return "Session monitoring ended";
        if ("observer_offline".equals(reason)) return "Agent connection is unavailable";
        if ("tracking_lost".equals(reason)) return "Claude did not supply a request ID; waiting for this chat's next event";
        if ("hook_gap".equals(reason)) return "An event was missed; waiting for this chat's next signal";
        if ("invalid_hook".equals(reason)) return "Invalid event received; waiting for this chat's next signal";
        if ("observer_gap".equals(reason)) return "Connection was interrupted; waiting for this chat's next event";
        if ("process_unavailable".equals(reason)) return "Agent process unavailable; waiting to reconnect";
        if ("observer_stale".equals(reason) || "signal_expired".equals(reason)) return "No recent update";
        return "Waiting for a confirmed update";
    }

    static int activeCount(Snapshot snapshot) {
        int count = 0;
        if (snapshot != null) for (Snapshot.SessionRow row : snapshot.sessions) if (row.active) count++;
        return count;
    }

    static int priority(String state) { return "awaiting_user".equals(state) ? 4 : "working".equals(state) ? 3 : "unknown".equals(state) ? 2 : 1; }
    private SessionOverview() {}
}
