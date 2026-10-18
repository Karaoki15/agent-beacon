package com.agentbeacon.android;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.ComponentName;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/** Launcher configuration and an in-app list of independently configured copies. */
public final class WideWidgetConfigActivity extends LocalizedActivity {
    private static final int BG = 0xFF080808, MUTED = 0xFFAAAAAA;
    private int widgetId = AppWidgetManager.INVALID_APPWIDGET_ID;
    private CheckBox counts;
    private final java.util.Map<String, CheckBox> agents = new java.util.LinkedHashMap<>();
    private final java.util.List<String> sourceOrder = new java.util.ArrayList<>();
    private LinearLayout order;
    private TextView validation;
    private FrameLayout preview;
    private Button save;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        setResult(RESULT_CANCELED);
        widgetId = getIntent().getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        if (widgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            if (AppWidgetManager.ACTION_APPWIDGET_CONFIGURE.equals(getIntent().getAction())) { finish(); return; }
            showList();
        } else if (!WideWidgetSettings.isWideWidget(this, widgetId)) {
            unavailable();
        } else showEditor(state);
    }

    private LinearLayout page(String title, String help) {
        ScrollView scroll = new ScrollView(this); scroll.setBackgroundColor(BG); scroll.setFillViewport(true);
        LinearLayout page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL);
        int padding = dp(22); page.setPadding(padding, padding, padding, padding);
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                page.setPadding(padding, padding + bars.top, padding, padding + bars.bottom);
            } else page.setPadding(padding, padding + insets.getSystemWindowInsetTop(), padding, padding + insets.getSystemWindowInsetBottom());
            return insets;
        });
        page.addView(text(title, 26, Color.WHITE));
        TextView intro = text(help, 14, MUTED); intro.setPadding(0, dp(10), 0, dp(20)); page.addView(intro);
        scroll.addView(page); setContentView(scroll); return page;
    }

    private void showList() {
        LinearLayout page = page(AppText.tr(WideWidgetConfigActivity.this, "Widget settings"), AppText.tr(WideWidgetConfigActivity.this, "Each Agent overview widget has its own agents, row order and chat counts."));
        int[] ids = AppWidgetManager.getInstance(this).getAppWidgetIds(new ComponentName(this, WideBeaconWidget.class));
        java.util.Arrays.sort(ids);
        if (ids.length == 0) {
            TextView empty = text(AppText.tr(WideWidgetConfigActivity.this, "Add Agent overview from your home screen's widget picker, then return here to customize it."), 16, Color.WHITE);
            empty.setPadding(0, dp(8), 0, dp(20)); page.addView(empty);
        }
        for (int index = 0; index < ids.length; index++) {
            final int id = ids[index];
            WideWidgetOptions options = WideWidgetSettings.load(this, id);
            Button widget = button(AppText.tr(WideWidgetConfigActivity.this, "Widget ") + (index + 1) + "\n" + options.description()
                    + (options.showCounts ? AppText.tr(WideWidgetConfigActivity.this, " · Chat counts on") : AppText.tr(WideWidgetConfigActivity.this, " · Status only")));
            widget.setGravity(Gravity.START | Gravity.CENTER_VERTICAL); widget.setMinHeight(dp(76));
            page.addView(widget, spaced());
            widget.setOnClickListener(view -> startActivity(new Intent(this, WideWidgetConfigActivity.class)
                    .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id)));
        }
        page.addView(text(AppText.tr(WideWidgetConfigActivity.this, "You can also touch and hold a widget and choose Edit when your home-screen launcher offers it."), 13, MUTED));
        Button done = button(AppText.tr(WideWidgetConfigActivity.this, "Done")); page.addView(done, spaced()); done.setOnClickListener(view -> finish());
    }

    private void showEditor(Bundle state) {
        WideWidgetOptions options = WideWidgetSettings.load(this, widgetId);
        WideWidgetOptions ordered = state != null ? new WideWidgetOptions(state.getString("order", options.encode()), options.showCounts) : options;
        sourceOrder.addAll(ordered.sources);
        for (String source : new String[] {"codex", "claude", "antigravity"}) if (!sourceOrder.contains(source)) sourceOrder.add(source);
        LinearLayout page = page(AppText.tr(WideWidgetConfigActivity.this, "Customize widget"), AppText.tr(WideWidgetConfigActivity.this, "Changes apply to this Agent overview widget. Its main color follows only the agents you choose."));
        LinearLayout agents = card(page);
        agents.addView(text(AppText.tr(WideWidgetConfigActivity.this, "AGENTS"), 12, MUTED));
        for (String source : new String[] {"codex", "claude", "antigravity"}) {
            String label = SessionOverview.providerName(source) + ("antigravity".equals(source) ? AppText.tr(WideWidgetConfigActivity.this, " (experimental)") : "");
            CheckBox selected = choice(label, state != null ? state.getBoolean(source, options.sources.contains(source)) : options.sources.contains(source));
            this.agents.put(source, selected); agents.addView(selected);
        }
        agents.addView(text(AppText.tr(WideWidgetConfigActivity.this, "Antigravity needs its desktop integration connected before it can report status."), 13, MUTED));

        LinearLayout appearance = card(page);
        counts = choice(AppText.tr(WideWidgetConfigActivity.this, "Show chat counts"), state != null ? state.getBoolean("counts") : options.showCounts); appearance.addView(counts);
        appearance.addView(text(AppText.tr(WideWidgetConfigActivity.this, "A small count appears only when an agent has multiple chats."), 13, MUTED));
        order = new LinearLayout(this); order.setOrientation(LinearLayout.VERTICAL); appearance.addView(order);

        TextView previewLabel = text(AppText.tr(WideWidgetConfigActivity.this, "CURRENT PREVIEW"), 12, MUTED); previewLabel.setPadding(0, dp(14), 0, dp(10)); page.addView(previewLabel);
        preview = new FrameLayout(this);
        LinearLayout.LayoutParams previewParams = new LinearLayout.LayoutParams(dp(260), dp((int) Math.ceil(104 * getResources().getConfiguration().fontScale)));
        previewParams.gravity = Gravity.CENTER_HORIZONTAL; page.addView(preview, previewParams);
        validation = text("", 13, 0xFFFFC66B); validation.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        validation.setPadding(0, dp(12), 0, 0); page.addView(validation);
        save = button(AppText.tr(WideWidgetConfigActivity.this, "Save widget")); page.addView(save, spaced()); save.setOnClickListener(view -> save());
        Button cancel = button(AppText.tr(WideWidgetConfigActivity.this, "Cancel")); page.addView(cancel, spaced()); cancel.setOnClickListener(view -> finish());
        for (CheckBox selected : this.agents.values()) selected.setOnCheckedChangeListener((button, checked) -> updatePreview());
        counts.setOnCheckedChangeListener((button, checked) -> updatePreview());
        updatePreview();
    }

    private java.util.List<String> selectedSources() {
        java.util.List<String> result = new java.util.ArrayList<>();
        for (String source : sourceOrder) if (agents.get(source).isChecked()) result.add(source);
        return result;
    }

    private WideWidgetOptions draft() {
        return new WideWidgetOptions(String.join(",", selectedSources()), counts.isChecked());
    }

    private void updatePreview() {
        java.util.List<String> selected = selectedSources();
        boolean valid = !selected.isEmpty();
        save.setEnabled(valid); order.removeAllViews(); order.setVisibility(selected.size() > 1 ? View.VISIBLE : View.GONE);
        if (selected.size() > 1) {
            order.addView(text(AppText.tr(WideWidgetConfigActivity.this, "ROW ORDER"), 12, MUTED));
            for (int index = 0; index < selected.size(); index++) {
                final String source = selected.get(index);
                final int position = index;
                LinearLayout row = new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
                TextView title = text(SessionOverview.providerName(source), 15, Color.WHITE);
                row.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
                for (int direction : new int[] {-1, 1}) {
                    Button move = button(direction < 0 ? "↑" : "↓"); move.setMinWidth(dp(48));
                    move.setContentDescription(AppText.tr(WideWidgetConfigActivity.this, "Move ") + SessionOverview.providerName(source) + (direction < 0 ? AppText.tr(WideWidgetConfigActivity.this, " up") : AppText.tr(WideWidgetConfigActivity.this, " down")));
                    move.setEnabled(position + direction >= 0 && position + direction < selected.size());
                    row.addView(move, new LinearLayout.LayoutParams(dp(48), dp(48)));
                    move.setOnClickListener(view -> {
                        java.util.Collections.swap(sourceOrder, sourceOrder.indexOf(source), sourceOrder.indexOf(selected.get(position + direction)));
                        updatePreview();
                    });
                }
                order.addView(row);
            }
        }
        validation.setText(valid ? "" : AppText.tr(WideWidgetConfigActivity.this, "Choose at least one agent."));
        preview.removeAllViews();
        if (valid) preview.addView(WideBeaconWidget.preview(this, draft()).apply(this, preview),
                new FrameLayout.LayoutParams(-1, -1));
    }

    private void save() {
        if (selectedSources().isEmpty()) return;
        if (!WideWidgetSettings.isWideWidget(this, widgetId)) { unavailable(); return; }
        if (!WideWidgetSettings.save(this, widgetId, draft())) {
            validation.setText(AppText.tr(WideWidgetConfigActivity.this, "Could not save this widget. Try again.")); return;
        }
        WideBeaconWidget.refresh(this, widgetId);
        setResult(RESULT_OK, new Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId));
        finish();
    }

    private void unavailable() {
        Toast.makeText(this, AppText.tr(WideWidgetConfigActivity.this, "This Agent overview widget is no longer available."), Toast.LENGTH_LONG).show(); finish();
    }

    @Override protected void onResume() {
        super.onResume();
        if (isFinishing()) return;
        if (widgetId == AppWidgetManager.INVALID_APPWIDGET_ID) showList();
        else if (!WideWidgetSettings.isWideWidget(this, widgetId)) unavailable();
        else if (preview != null) updatePreview();
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        if (counts != null) {
            for (java.util.Map.Entry<String, CheckBox> entry : agents.entrySet()) out.putBoolean(entry.getKey(), entry.getValue().isChecked());
            out.putBoolean("counts", counts.isChecked()); out.putString("order", String.join(",", sourceOrder));
        }
        super.onSaveInstanceState(out);
    }

    private LinearLayout card(LinearLayout page) {
        LinearLayout card = new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL); card.setPadding(dp(16), dp(12), dp(16), dp(12));
        GradientDrawable background = new GradientDrawable(); background.setColor(0xFF191919); background.setCornerRadius(dp(16)); card.setBackground(background);
        page.addView(card, spaced()); return card;
    }
    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color); return view;
    }
    private CheckBox choice(String title, boolean checked) {
        CheckBox view = new CheckBox(this); view.setText(title); view.setTextSize(16); view.setTextColor(Color.WHITE);
        view.setMinHeight(dp(48)); view.setChecked(checked); view.setSaveEnabled(false); return view;
    }
    private Button button(String title) {
        Button view = UiStyle.button(this, "", true); view.setText(title); view.setTextSize(15); view.setAllCaps(false); view.setMinHeight(dp(48)); return view;
    }
    private LinearLayout.LayoutParams spaced() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2); params.topMargin = dp(10); return params;
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
}
