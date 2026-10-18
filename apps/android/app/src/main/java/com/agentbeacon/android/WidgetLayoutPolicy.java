package com.agentbeacon.android;

/** Space decisions in dp; text keeps the system font scale for accessibility. */
final class WidgetLayoutPolicy {
    final int rowHeight;
    final boolean rows, header, details, hint;

    WidgetLayoutPolicy(int width, int height, int agents, float fontScale) {
        rowHeight = (int) Math.ceil(18 * fontScale);
        rows = height >= 12 + agents * rowHeight;
        header = !rows || height >= 16 + agents * rowHeight + (int) Math.ceil(22 * fontScale);
        details = width >= Math.ceil(170 * fontScale);
        hint = !rows && height >= 16 + Math.ceil(38 * fontScale);
    }
}
