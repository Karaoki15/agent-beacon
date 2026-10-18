package com.agentbeacon.android;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProviderInfo;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import java.util.ArrayList;
import java.util.List;

final class WideWidgetSettings {
    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("beacon_wide_widgets", Context.MODE_PRIVATE);
    }
    private static String key(int id, String field) { return "widget_" + id + "_" + field; }
    static WideWidgetOptions load(Context context, int id) {
        SharedPreferences prefs = prefs(context);
        return new WideWidgetOptions(prefs.getString(key(id, "sources"), null), prefs.getBoolean(key(id, "counts"), true));
    }
    static boolean isWideWidget(Context context, int id) {
        if (id == AppWidgetManager.INVALID_APPWIDGET_ID) return false;
        AppWidgetProviderInfo info = AppWidgetManager.getInstance(context).getAppWidgetInfo(id);
        return info != null && new ComponentName(context, WideBeaconWidget.class).equals(info.provider);
    }
    static boolean save(Context context, int id, WideWidgetOptions options) {
        if (!isWideWidget(context, id)) return false;
        return prefs(context).edit().putString(key(id, "sources"), options.encode())
                .putBoolean(key(id, "counts"), options.showCounts).commit();
    }
    static void remove(Context context, int[] ids) {
        SharedPreferences.Editor editor = prefs(context).edit();
        for (int id : ids) editor.remove(key(id, "sources")).remove(key(id, "counts"));
        editor.apply();
    }
    static void restore(Context context, int[] oldIds, int[] newIds) {
        if (oldIds.length != newIds.length) return;
        List<WideWidgetOptions> copies = new ArrayList<>();
        for (int id : oldIds) copies.add(load(context, id));
        SharedPreferences.Editor editor = prefs(context).edit();
        for (int id : oldIds) editor.remove(key(id, "sources")).remove(key(id, "counts"));
        for (int index = 0; index < newIds.length; index++) {
            WideWidgetOptions options = copies.get(index);
            editor.putString(key(newIds[index], "sources"), options.encode()).putBoolean(key(newIds[index], "counts"), options.showCounts);
        }
        editor.apply();
    }
    private WideWidgetSettings() {}
}
