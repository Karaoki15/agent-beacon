package com.agentbeacon.android;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Small validated presentation profile, independent of Android storage. */
final class WideWidgetOptions {
    final List<String> sources;
    final boolean showCounts;

    WideWidgetOptions(String orderedSources, boolean showCounts) {
        List<String> selected = new ArrayList<>();
        if (orderedSources != null && orderedSources.length() <= 64) for (String source : orderedSources.split(",")) {
            if (("codex".equals(source) || "claude".equals(source) || "antigravity".equals(source)) && !selected.contains(source)) selected.add(source);
        }
        // Existing widgets and invalid saved profiles keep the familiar default.
        if (selected.isEmpty()) { selected.add("codex"); selected.add("claude"); }
        this.sources = Collections.unmodifiableList(selected);
        this.showCounts = showCounts;
    }

    String encode() { return String.join(",", sources); }
    String description() {
        List<String> names = new ArrayList<>();
        for (String source : sources) names.add(SessionOverview.providerName(source));
        return String.join(" + ", names);
    }
}
