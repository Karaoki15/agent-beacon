package com.agentbeacon.android;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.Build;
import android.os.SystemClock;
import android.view.View;
import android.widget.RemoteViews;
import java.util.HashMap;
import java.util.Map;

public final class WideBeaconWidget extends AppWidgetProvider {
    private static final Map<Integer, String> PRESENTATIONS = new HashMap<>();

    static void refresh(Context context) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        int[] ids = manager.getAppWidgetIds(new ComponentName(context, WideBeaconWidget.class));
        if (ids.length > 0) new WideBeaconWidget().update(context, manager, ids, false);
    }

    static void refresh(Context context, int id) {
        if (WideWidgetSettings.isWideWidget(context, id)) new WideBeaconWidget().update(
                context, AppWidgetManager.getInstance(context), new int[] {id}, true);
    }

    @Override public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        update(context, manager, ids, true);
    }

    private void update(Context context, AppWidgetManager manager, int[] ids, boolean force) {
        Snapshot snapshot = SnapshotCache.load(context);
        long now = SystemClock.elapsedRealtime();
        boolean connected = ConnectionStore.load(context) != null, monitoring = StatusService.isRunning();
        boolean liveSurface = monitoring || MainActivity.isVisible();
        StatusPresentation recovery = StatusPresentation.select(context, snapshot, liveSurface, now);
        for (int id : ids) {
            WideWidgetOptions options = WideWidgetSettings.load(context, id);
            Frame frame = frame(context, id, options, snapshot, recovery, connected, liveSurface, now);
            if (!force && frame.key.equals(PRESENTATIONS.get(id))) continue;
            manager.updateAppWidget(id, frame.views);
            PRESENTATIONS.put(id, frame.key);
        }
    }

    static RemoteViews preview(Context context, WideWidgetOptions options) {
        Snapshot snapshot = SnapshotCache.load(context);
        long now = SystemClock.elapsedRealtime();
        boolean connected = ConnectionStore.load(context) != null, monitoring = StatusService.isRunning();
        boolean liveSurface = monitoring || MainActivity.isVisible();
        return frame(context, AppWidgetManager.INVALID_APPWIDGET_ID, options, snapshot,
                StatusPresentation.select(context, snapshot, liveSurface, now), connected, liveSurface, now).views;
    }

    private static Frame frame(Context context, int id, WideWidgetOptions options, Snapshot snapshot,
                               StatusPresentation recovery, boolean connected, boolean monitoring, long now) {
        WideWidgetModel model = new WideWidgetModel(snapshot, options, now);
        boolean reconnecting = false;
        // Reuse the existing bounded recovery window, but project its evidence
        // onto this widget's providers instead of copying the global color.
        if (connected && monitoring && "unknown".equals(model.state) && recovery.reconnecting) {
            Snapshot last = SnapshotCache.loadLastKnown(context);
            if (last != null) {
                long beforeExpiry = last.requestStartedElapsedMs + (long) Math.ceil(last.validForSeconds * 1000) - 1L;
                WideWidgetModel held = new WideWidgetModel(last, options, Math.min(now, beforeExpiry));
                if (!"unknown".equals(held.state)) { model = held; reconnecting = true; }
            }
        }
        String state = !connected || !monitoring ? "unknown" : model.state;
        String label = !connected ? "Connect" : !monitoring ? "Paused" : reconnecting ? "Reconnecting" : shortState(state);
        String countText = "BEACON";
        float fontScale = context.getResources().getConfiguration().fontScale;
        int height = id == AppWidgetManager.INVALID_APPWIDGET_ID ? (int) Math.ceil(104 * fontScale)
                : AppWidgetManager.getInstance(context).getAppWidgetOptions(id).getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 72);
        int width = id == AppWidgetManager.INVALID_APPWIDGET_ID ? 260
                : AppWidgetManager.getInstance(context).getAppWidgetOptions(id).getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 140);
        WidgetLayoutPolicy layout = new WidgetLayoutPolicy(width, height, options.sources.size(), fontScale);
        boolean showHeaderCount = layout.header && width >= 220 * fontScale;
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_wide_beacon);
        views.setViewVisibility(R.id.wide_widget_header, layout.header ? View.VISIBLE : View.GONE);
        views.setViewVisibility(R.id.wide_widget_resize, layout.hint ? View.VISIBLE : View.GONE);
        views.setViewVisibility(R.id.wide_widget_count, showHeaderCount ? View.VISIBLE : View.GONE);
        views.setImageViewResource(R.id.wide_widget_dot, icon(state));
        views.setInt(R.id.wide_widget_dot, "setImageAlpha", reconnecting ? 150 : 255);
        views.setTextViewText(R.id.wide_widget_state, label);
        views.setTextViewText(R.id.wide_widget_count, countText);
        StringBuilder key = new StringBuilder(options.encode()).append(':').append(options.showCounts)
                .append(':').append(state).append(':').append(label).append(':').append(countText).append(':').append(layout.rows).append(':').append(layout.header).append(':').append(layout.details).append(':').append(layout.rowHeight).append(':').append(showHeaderCount);
        StringBuilder description = new StringBuilder("Agent Beacon: ").append(label);
        int[] rowIds = {R.id.wide_first_row, R.id.wide_second_row, R.id.wide_third_row};
        int[] nameIds = {R.id.wide_codex_name, R.id.wide_claude_name, R.id.wide_third_name};
        int[] dotIds = {R.id.wide_codex_dot, R.id.wide_claude_dot, R.id.wide_third_dot};
        int[] statusIds = {R.id.wide_codex_state, R.id.wide_claude_state, R.id.wide_third_state};
        for (int index = 0; index < 3; index++) {
            int rowId = rowIds[index];
            views.setViewVisibility(rowId, index < model.rows.size() && layout.rows ? View.VISIBLE : View.GONE);
            if (index >= model.rows.size()) continue;
            SessionOverview.Project row = model.rows.get(index);
            String rowState = connected && monitoring && !reconnecting ? row.state : "unknown";
            String rowLabel = WideWidgetModel.rowLabel(row, connected && monitoring, reconnecting, options.showCounts);
            int rowPixels = Math.round(layout.rowHeight * context.getResources().getDisplayMetrics().density);
            views.setInt(rowId, "setMinimumHeight", rowPixels);
            views.setViewVisibility(statusIds[index], layout.details ? View.VISIBLE : View.GONE);
            views.setTextViewText(nameIds[index], row.title);
            views.setImageViewResource(dotIds[index], icon(rowState));
            int statusId = statusIds[index];
            views.setTextViewText(statusId, rowLabel);
            views.setTextColor(statusId, MainActivity.stateColor(rowState));
            key.append(':').append(row.source).append(':').append(rowState).append(':').append(rowLabel);
            description.append(". ").append(row.title).append(": ").append(rowLabel);
        }
        views.setContentDescription(R.id.wide_widget_root, description.toString());
        if (id != AppWidgetManager.INVALID_APPWIDGET_ID) {
            Intent open = new Intent(context, MainActivity.class).setAction("com.agentbeacon.android.OPEN_WIDE_WIDGET." + id);
            if (!connected || !monitoring || "unknown".equals(state)) open.putExtra(MainActivity.EXTRA_RECONNECT, true);
            views.setOnClickPendingIntent(R.id.wide_widget_root, PendingIntent.getActivity(context, id,
                    open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        }
        return new Frame(views, key.toString());
    }

    @Override public void onAppWidgetOptionsChanged(Context context, AppWidgetManager manager, int id, Bundle options) {
        onUpdate(context, manager, new int[] {id});
    }

    @Override public void onDeleted(Context context, int[] ids) {
        WideWidgetSettings.remove(context, ids);
        for (int id : ids) PRESENTATIONS.remove(id);
    }

    @Override public void onRestored(Context context, int[] oldIds, int[] newIds) {
        WideWidgetSettings.restore(context, oldIds, newIds);
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        for (int id : oldIds) PRESENTATIONS.remove(id);
        if (Build.VERSION.SDK_INT >= 30) for (int id : newIds) {
            Bundle options = new Bundle(); options.putBoolean(AppWidgetManager.OPTION_APPWIDGET_RESTORE_COMPLETED, true);
            manager.updateAppWidgetOptions(id, options);
        }
        onUpdate(context, manager, newIds);
    }

    private static String shortState(String state) {
        return "working".equals(state) ? "Working" : "awaiting_user".equals(state) ? "Needs you" : "ready".equals(state) ? "Ready" : "No update";
    }
    private static int icon(String state) {
        return "working".equals(state) ? R.drawable.dot_working : "awaiting_user".equals(state) ? R.drawable.dot_waiting
                : "ready".equals(state) ? R.drawable.dot_ready : R.drawable.dot_unknown;
    }
    private static int orb(String state) {
        return "working".equals(state) ? R.drawable.orb_working : "awaiting_user".equals(state) ? R.drawable.orb_waiting
                : "ready".equals(state) ? R.drawable.orb_ready : R.drawable.orb_unknown;
    }
    private static final class Frame {
        final RemoteViews views;
        final String key;
        Frame(RemoteViews views, String key) { this.views = views; this.key = key; }
    }
}
