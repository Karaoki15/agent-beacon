package com.agentbeacon.android;

import android.app.Activity;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.graphics.Insets;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;

public final class HistoryActivity extends LocalizedActivity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private LinearLayout events;
    private String renderedKey;
    private final Runnable refresh = new Runnable() {
        @Override public void run() { render(); handler.postDelayed(this, 1000L); }
    };

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this); scroll.setBackgroundColor(0xFF080808);
        LinearLayout page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL);
        int padding = dp(22); page.setPadding(padding, padding, padding, padding);
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                page.setPadding(padding, padding + bars.top, padding, padding + bars.bottom);
            } else page.setPadding(padding, padding + insets.getSystemWindowInsetTop(), padding, padding + insets.getSystemWindowInsetBottom());
            return insets;
        });
        page.addView(text(AppText.tr(HistoryActivity.this, "Recent activity"), 24, Color.WHITE));
        TextView help = text(AppText.tr(HistoryActivity.this, "The last 30 state changes on your computer, newest first. This history starts again when the Beacon server restarts. Prompts and answers are never included."), 13, 0xFFA4A8B8);
        help.setPadding(0, dp(10), 0, dp(20)); page.addView(help);
        events = new LinearLayout(this); events.setOrientation(LinearLayout.VERTICAL); page.addView(events);
        Button back = UiStyle.button(this, "", true); back.setText(AppText.tr(HistoryActivity.this, "Back")); back.setAllCaps(false); back.setOnClickListener(view -> finish()); page.addView(back);
        scroll.addView(page); setContentView(scroll);
    }

    private void render() {
        Snapshot snapshot = SnapshotCache.load(this);
        StringBuilder signature = new StringBuilder(snapshot == null ? "empty" : snapshot.instanceId);
        if (snapshot != null) for (Snapshot.HistoryEvent event : snapshot.history) signature.append(':').append(event.id).append(':').append(event.sessionName);
        String key = signature.toString();
        if (key.equals(renderedKey)) return;
        renderedKey = key; events.removeAllViews();
        if (snapshot == null || snapshot.history.isEmpty()) {
            events.addView(text(AppText.tr(HistoryActivity.this, "No recent changes yet. Connect your computer and start a request."), 15, 0xFFA4A8B8)); return;
        }
        DateTimeFormatter time = DateTimeFormatter.ofPattern("HH:mm:ss").withZone(ZoneId.systemDefault());
        for (int index = snapshot.history.size() - 1; index >= 0; index--) {
            Snapshot.HistoryEvent event = snapshot.history.get(index);
            LinearLayout row = new LinearLayout(this); row.setOrientation(LinearLayout.VERTICAL); row.setPadding(0, dp(10), 0, dp(14));
            String label = "closed".equals(event.statusReason) ? AppText.tr(HistoryActivity.this, "Session monitoring ended") : event.reminder ? AppText.tr(HistoryActivity.this, "Question reminder") : "unknown".equals(event.state) ? AppText.tr(HistoryActivity.this, "State not confirmed") : AppText.state(this, event.state);
            row.addView(text(time.format(Instant.parse(event.at)) + "  ·  " + label, 16, MainActivity.stateColor(event.state)));
            if (event.source != null) {
                row.addView(text(SessionOverview.providerName(event.source) + " · " + AppText.chat(this, event.sessionName), 15, Color.WHITE));
                row.addView(text(AppText.project(this, event.project), 12, 0xFFA4A8B8));
                if ("closed".equals(event.statusReason)) row.addView(text(AppText.tr(HistoryActivity.this, "The saved chat remains available"), 12, 0xFFA4A8B8));
                if ("unknown".equals(event.state) && !"closed".equals(event.statusReason)) row.addView(text(AppText.tr(this, SessionOverview.reason(event.statusReason)), 12, 0xFFA4A8B8));
            } else row.addView(text(AppText.tr(HistoryActivity.this, "Computer · ") + AppText.chatCount(this, event.chatCount) + (event.partial ? AppText.tr(HistoryActivity.this, " · Some chats unavailable") : ""), 12, 0xFFA4A8B8));
            events.addView(row);
        }
    }

    private TextView text(String value, int size, int color) { TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color); return view; }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    @Override protected void onResume() { super.onResume(); handler.post(refresh); }
    @Override protected void onPause() { handler.removeCallbacks(refresh); super.onPause(); }
}
