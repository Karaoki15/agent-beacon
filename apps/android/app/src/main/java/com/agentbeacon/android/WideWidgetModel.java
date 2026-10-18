package com.agentbeacon.android;

import java.util.ArrayList;
import java.util.List;

/** Aggregate only this widget's providers using their own freshness leases. */
final class WideWidgetModel {
    final List<SessionOverview.Project> rows = new ArrayList<>();
    String state = "unknown";
    int chats, tasks;

    WideWidgetModel(Snapshot snapshot, WideWidgetOptions options, long now) {
        boolean hasEvidence = false;
        for (String source : options.sources) {
            SessionOverview.Project row = SessionOverview.provider(snapshot, source, now);
            rows.add(row);
            chats += row.count;
            tasks += row.working + row.waiting;
            // An unconfigured provider with no chats doesn't block another
            // selected provider. An enrolled unknown chat still blocks green.
            if (row.count > 0 || "ready".equals(row.state)) {
                if (!hasEvidence || SessionOverview.priority(row.state) > SessionOverview.priority(state)) state = row.state;
                hasEvidence = true;
            }
        }
    }

    static String rowLabel(SessionOverview.Project row, boolean active, boolean reconnecting, boolean counts) {
        if (!active) return "Paused";
        if (reconnecting) return "Syncing";
        String label = row.waiting > 0 ? "Needs you" : row.working > 0 ? "Working"
                : row.missing > 0 ? "No update" : "ready".equals(row.state) ? "Ready" : "No chats";
        return counts && row.count > 1 ? label + " · " + row.count : label;
    }
}
