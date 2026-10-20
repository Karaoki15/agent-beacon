package com.agentbeacon.android;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;
import android.os.Bundle;
import android.view.View;
import android.widget.RemoteViews;

public final class BeaconWidget extends AppWidgetProvider {
    private static String lastPresentation;
    static void refresh(Context context) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        int[] ids = manager.getAppWidgetIds(new ComponentName(context, BeaconWidget.class));
        if (ids.length > 0) new BeaconWidget().update(context, manager, ids, false);
        WideBeaconWidget.refresh(context);
    }

    @Override public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        update(context, manager, ids, true);
    }

    @Override public void onAppWidgetOptionsChanged(Context context, AppWidgetManager manager, int id, Bundle options) {
        update(context, manager, new int[] {id}, true);
    }

    private void update(Context context, AppWidgetManager manager, int[] ids, boolean force) {
        Snapshot cached = SnapshotCache.load(context);
        long now = SystemClock.elapsedRealtime();
        boolean monitoring = StatusService.isRunning();
        boolean currentForeground = MainActivity.isVisible();
        boolean liveSurface = monitoring || currentForeground;
        boolean reminderOnly = liveSurface && cached != null && cached.isQuestionReminderOnly(now);
        boolean connected = ConnectionStore.load(context) != null;
        StatusPresentation presentation = StatusPresentation.select(context, cached, liveSurface, now);
        String state = !connected || !liveSurface ? "unknown" : presentation.state;
        int label = labelFor(state);
        String stateText = !connected ? "Offline"
                : !liveSurface ? "Paused"
                : presentation.reconnecting ? shortLabel(state) + "…"
                : cached == null ? "Unknown"
                : !cached.isFresh(now) ? "Stale"
                : !cached.available || "unknown".equals(state) ? "Unknown"
                : reminderOnly ? "Waiting"
                : shortLabel(state);
        String presentationKey = java.util.Arrays.toString(ids) + ":" + stateText + ":" + state;
        if (!force && presentationKey.equals(lastPresentation)) return;
        for (int id : ids) {
            RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_beacon);
            views.setTextViewText(R.id.widget_state, stateText);
            views.setImageViewResource(R.id.widget_dot, label);
            boolean roomy = manager.getAppWidgetOptions(id).getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 64)
                    >= 88 * Math.max(1f, context.getResources().getConfiguration().fontScale);
            views.setViewVisibility(R.id.widget_brand, roomy ? View.VISIBLE : View.GONE);
            views.setViewVisibility(R.id.widget_hint, roomy ? View.VISIBLE : View.GONE);
            views.setTextViewText(R.id.widget_hint, !connected || !liveSurface || "unknown".equals(state) ? "Connect" : "Live");
            views.setInt(R.id.widget_dot, "setImageAlpha", presentation.reconnecting ? 150 : 255);
            views.setContentDescription(R.id.widget_root, "Agent Beacon: " + (presentation.reconnecting ? "Last known, reconnecting: " : "") + stateText);
            Intent open = new Intent(context, MainActivity.class).setAction("com.agentbeacon.android.OPEN_WIDGET." + id);
            if (!connected || !liveSurface || "unknown".equals(state)) open.putExtra(MainActivity.EXTRA_RECONNECT, true);
            views.setOnClickPendingIntent(R.id.widget_root, PendingIntent.getActivity(context, id, open, PendingIntent.FLAG_UPDATE_CURRENT | (android.os.Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0)));
            manager.updateAppWidget(id, views);
        }
        lastPresentation = presentationKey;
    }

    private static int labelFor(String state) {
        if ("working".equals(state)) return R.drawable.orb_working;
        if ("awaiting_user".equals(state)) return R.drawable.orb_waiting;
        if ("ready".equals(state)) return R.drawable.orb_ready;
        return R.drawable.orb_unknown;
    }

    private static String shortLabel(String state) {
        if ("working".equals(state)) return "Working";
        if ("awaiting_user".equals(state)) return "Waiting";
        if ("ready".equals(state)) return "Ready";
        return "Unknown";
    }
}
