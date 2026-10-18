package com.agentbeacon.android;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Insets;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public final class DiagnosticsActivity extends LocalizedActivity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView details;
    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            details.setText(UpdateDiagnostics.summary(DiagnosticsActivity.this, SnapshotCache.load(DiagnosticsActivity.this),
                    StatusService.isRunning(), UpdateSettings.batterySaver(DiagnosticsActivity.this))
                    + "\n\n" + AppText.tr(DiagnosticsActivity.this, "Automatic reconnection") + "\n" + AppText.technical(DiagnosticsActivity.this, ConnectionRecovery.diagnostics())
                    + "\n\n" + AppText.tr(DiagnosticsActivity.this, "Android Live Updates (experimental)") + "\n" + StatusNotifier.liveUpdateDiagnostics(DiagnosticsActivity.this));
            handler.postDelayed(this, 1000L);
        }
    };

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this); scroll.setBackgroundColor(Color.rgb(8, 8, 8));
        LinearLayout page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(22 * getResources().getDisplayMetrics().density);
        page.setPadding(padding, padding, padding, padding);
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                page.setPadding(padding, padding + bars.top, padding, padding + bars.bottom);
            } else page.setPadding(padding, padding + insets.getSystemWindowInsetTop(), padding, padding + insets.getSystemWindowInsetBottom());
            return insets;
        });
        TextView heading = new TextView(this); heading.setText(AppText.tr(DiagnosticsActivity.this, "Update diagnostics")); heading.setTextSize(24); heading.setTextColor(Color.WHITE);
        page.addView(heading);
        TextView help = new TextView(this);
        help.setText(AppText.tr(DiagnosticsActivity.this, "Connection response measures the first reply, not the full delay from Codex. Last update includes keep-alives. Counters reset when the app process restarts. Prompts and answers are never included."));
        help.setTextSize(13); help.setTextColor(Color.LTGRAY); help.setPadding(0, padding / 2, 0, padding);
        page.addView(help);
        details = new TextView(this); details.setTextSize(16); details.setTextColor(Color.WHITE); page.addView(details);
        Button back = UiStyle.button(this, "", true); back.setText(AppText.tr(DiagnosticsActivity.this, "Back")); back.setAllCaps(false); back.setOnClickListener(view -> finish()); page.addView(back);
        scroll.addView(page); setContentView(scroll);
    }

    @Override protected void onResume() { super.onResume(); handler.post(refresh); }
    @Override protected void onPause() { handler.removeCallbacks(refresh); super.onPause(); }
}
