package com.agentbeacon.android;

import android.content.Context;
import android.content.res.Resources;

final class AppText {
    private static Resources resources;
    private static String language;
    static synchronized String tr(Context context, String english) {
        int id = TextCatalog.resource(english);
        if (id == 0) return english;
        return resources(context).getString(id);
    }
    private static synchronized Resources resources(Context context) {
        String selected = AppLanguage.language(context);
        if (resources == null || !selected.equals(language)) {
            resources = AppLanguage.wrap(context).getResources(); language = selected;
        }
        return resources;
    }
    static String chatCount(Context context, int count) { return resources(context).getQuantityString(R.plurals.tracked_chats, count, count); }
    static String state(Context context, String state) { return tr(context, MainActivity.stateLabel(state)); }
    static String reminder(Context context, long remainingMs) {
        long seconds = (Math.max(0, remainingMs) + 999L) / 1000L;
        return tr(context, "Agent is working · previous request reminder · remaining ")
                + String.format(java.util.Locale.ROOT, "%02d:%02d", seconds / 60, seconds % 60);
    }
    static String chat(Context context, String name) {
        return name == null || name.trim().isEmpty() ? tr(context, "Untitled chat") : name;
    }
    static String project(Context context, String project) {
        return project == null || "new-chat".equalsIgnoreCase(project) ? tr(context, "Project not provided") : project;
    }
    static String technical(Context context, String message) {
        String prefix = "Paired computer verified at ";
        if (message.startsWith(prefix)) return tr(context, prefix) + message.substring(prefix.length());
        return tr(context, message);
    }
    private AppText() {}
}
