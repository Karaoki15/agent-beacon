package com.agentbeacon.android;

import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.widget.Button;

/** Shared native buttons with readable disabled states, rounded borders and touch feedback. */
final class UiStyle {
    static Button button(Context context, String label, boolean secondary) {
        Button button = new Button(context);
        button.setText(label); button.setAllCaps(false); button.setTextSize(15);
        button.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL)); button.setIncludeFontPadding(false);
        button.setMinWidth(0); button.setMinimumWidth(0); button.setMinHeight(dp(context, 48));
        button.setPadding(dp(context, 16), dp(context, 12), dp(context, 16), dp(context, 12));
        button.setBackgroundTintList(null);
        StateListDrawable fills = new StateListDrawable();
        fills.addState(new int[]{-android.R.attr.state_enabled}, shape(context, 0xFF202020, 0xFF303030));
        fills.addState(new int[]{}, shape(context, secondary ? 0xFF202020 : 0xFF8BDAC3, secondary ? 0xFF424242 : 0xFFA5E7D4));
        button.setBackground(new RippleDrawable(ColorStateList.valueOf(secondary ? 0x24FFFFFF : 0x20000000), fills, null));
        button.setTextColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled}, new int[]{}},
                new int[]{0xFF888888, secondary ? 0xFFF4F4F4 : 0xFF09251D}));
        button.setElevation(0); button.setStateListAnimator(null);
        return button;
    }
    static GradientDrawable shape(Context context, int fill, int stroke) {
        GradientDrawable shape = new GradientDrawable(); shape.setColor(fill); shape.setCornerRadius(dp(context, 14));
        shape.setStroke(dp(context, 1), stroke); return shape;
    }
    private static int dp(Context context, int value) { return Math.round(value * context.getResources().getDisplayMetrics().density); }
    private UiStyle() {}
}
