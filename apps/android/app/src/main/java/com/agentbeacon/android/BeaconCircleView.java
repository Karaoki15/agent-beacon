package com.agentbeacon.android;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.view.View;

final class BeaconCircleView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private String state = "unknown";
    private String label;

    BeaconCircleView(Context context) { super(context); label = AppText.tr(context, "No update").toUpperCase(java.util.Locale.ROOT); setLayerType(View.LAYER_TYPE_SOFTWARE, null); }

    void setState(String state) {
        if (this.state.equals(state)) return;
        this.state = state; this.label = AppText.tr(getContext(), "working".equals(state) ? "Working" : "awaiting_user".equals(state) ? "Needs you" : "ready".equals(state) ? "Ready" : "No update").toUpperCase(java.util.Locale.ROOT); invalidate();
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        float radius = Math.min(getWidth(), getHeight()) * 0.38f;
        int color = MainActivity.stateColor(state);
        paint.setColor(color); paint.setAlpha(28); paint.setStyle(Paint.Style.FILL); canvas.drawCircle(cx, cy, radius + 18, paint);
        paint.setAlpha(255); canvas.drawCircle(cx, cy, radius, paint);
        paint.setColor(0xFFFFFFFF); paint.setTextAlign(Paint.Align.CENTER); paint.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL)); paint.setTextSize(Math.min(26f, radius * 0.22f));
        float width = paint.measureText(label);
        if (width > radius * 1.7f) paint.setTextSize(paint.getTextSize() * radius * 1.7f / width);
        canvas.drawText(label, cx, cy + (paint.getTextSize() * 0.36f), paint);
    }
}
