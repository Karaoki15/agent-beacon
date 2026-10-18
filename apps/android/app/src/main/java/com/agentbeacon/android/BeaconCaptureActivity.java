package com.agentbeacon.android;

import android.content.Intent;
import android.os.Bundle;
import android.util.Log;
import android.view.KeyEvent;

import com.journeyapps.barcodescanner.CaptureActivity;

import java.util.IdentityHashMap;

/** Keeps scanner lifecycle failures inside the pairing flow and returns a shareable, scrubbed diagnostic. */
public final class BeaconCaptureActivity extends CaptureActivity {
    private boolean failureReported;

    @Override protected void attachBaseContext(android.content.Context base) {
        super.attachBaseContext(AppLanguage.wrap(base));
    }

    @Override protected void onCreate(Bundle state) {
        try { super.onCreate(state); }
        catch (RuntimeException error) { reportFailure(error); }
    }

    @Override protected void onResume() {
        try { super.onResume(); }
        catch (RuntimeException error) { reportFailure(error); }
    }

    @Override protected void onPause() {
        try { super.onPause(); }
        catch (RuntimeException error) { reportFailure(error); }
    }

    @Override protected void onDestroy() {
        try { super.onDestroy(); }
        catch (RuntimeException error) { reportFailure(error); }
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        try { super.onSaveInstanceState(state); }
        catch (RuntimeException error) { reportFailure(error); }
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        try { super.onRequestPermissionsResult(requestCode, permissions, grantResults); }
        catch (RuntimeException error) { reportFailure(error); }
    }

    @Override public boolean onKeyDown(int keyCode, KeyEvent event) {
        try { return super.onKeyDown(keyCode, event); }
        catch (RuntimeException error) { reportFailure(error); return true; }
    }

    private void reportFailure(RuntimeException error) {
        if (failureReported) return;
        failureReported = true;
        String diagnostic = scannerDiagnostic(error);
        getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putString("last_scanner_failure", diagnostic).apply();
        Log.w("AgentBeacon", "QR scanner failed: " + diagnostic);
        setResult(RESULT_CANCELED, new Intent().putExtra(MainActivity.EXTRA_SCANNER_FAILURE, diagnostic));
        finish();
    }

    private static String scannerDiagnostic(Throwable error) {
        StringBuilder diagnostic = new StringBuilder();
        IdentityHashMap<Throwable, Boolean> seen = new IdentityHashMap<>();
        Throwable current = error;
        for (int depth = 0; current != null && depth < 3 && seen.put(current, Boolean.TRUE) == null; depth++) {
            if (diagnostic.length() > 0) diagnostic.append(" <- ");
            diagnostic.append(current.getClass().getSimpleName());
            StackTraceElement[] frames = current.getStackTrace();
            if (frames.length > 0) {
                StackTraceElement frame = frames[0];
                String className = frame.getClassName();
                int separator = className.lastIndexOf('.');
                String simpleClass = separator < 0 ? className : className.substring(separator + 1);
                diagnostic.append(" at ").append(simpleClass).append('.').append(frame.getMethodName())
                        .append(':').append(frame.getLineNumber());
            }
            current = current.getCause();
        }
        return diagnostic.substring(0, Math.min(160, diagnostic.length()));
    }
}
