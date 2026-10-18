package com.agentbeacon.android;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class PromotedDismissReceiver extends BroadcastReceiver {
    static final String ACTION_DISMISSED = "com.agentbeacon.android.action.PROMOTED_DISMISSED";

    @Override public void onReceive(Context context, Intent intent) {
        if (intent == null || !ACTION_DISMISSED.equals(intent.getAction())) return;
        context.getSharedPreferences("beacon_runtime", Context.MODE_PRIVATE).edit().putBoolean("promoted_suppressed", true).apply();
        StatusNotifier.cancelStatus(context);
    }
}
