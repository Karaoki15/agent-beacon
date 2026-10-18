package com.agentbeacon.android;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

/** Wake the existing single-flight transport when the default network returns. */
final class NetworkReconnect implements AutoCloseable {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ConnectivityManager manager;
    private final Runnable reconnect;
    private boolean closed;
    private boolean registered;
    private Network current;
    private final ConnectivityManager.NetworkCallback callback = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network network) {
            handler.post(() -> {
                if (closed || network.equals(current)) return;
                current = network;
                ConnectionRecovery.reset();
                handler.removeCallbacks(reconnect);
                handler.postDelayed(reconnect, 250L);
            });
        }
        @Override public void onLost(Network network) {
            handler.post(() -> { if (network.equals(current)) current = null; });
        }
    };

    NetworkReconnect(Context context, Runnable action) {
        manager = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        reconnect = () -> { if (!closed) action.run(); };
        if (manager == null) return;
        try {
            // Ignore registration's initial callback; normal startup already connects.
            current = manager.getActiveNetwork();
            manager.registerDefaultNetworkCallback(callback);
            registered = true;
        } catch (RuntimeException error) {
            Log.w("AgentBeacon", "Network recovery observer unavailable (" + error.getClass().getSimpleName() + ")");
        }
    }

    @Override public void close() {
        closed = true;
        handler.removeCallbacksAndMessages(null);
        if (registered) {
            try { manager.unregisterNetworkCallback(callback); }
            catch (RuntimeException error) { Log.w("AgentBeacon", "Network observer already removed"); }
            registered = false;
        }
    }
}
