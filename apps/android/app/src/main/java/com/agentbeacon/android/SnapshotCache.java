package com.agentbeacon.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;

final class SnapshotCache {
    private static final String PREFS = "beacon_snapshot_cache";
    private static final String JSON = "snapshot";
    private static final String RECEIVED = "received_elapsed";
    private static final String REQUEST_STARTED = "request_started_elapsed";
    private static final String BOOT = "boot_count";
    private static final String CREDENTIAL = "credential_fingerprint";
    private static final ReadyConfirmation READY_CONFIRMATION = new ReadyConfirmation();
    private SnapshotCache() {}

    static synchronized Snapshot saveIfNewer(Context context, Snapshot incoming, Credentials credentials) {
        return save(context, incoming, credentials, false);
    }

    static synchronized Snapshot saveConfirmed(Context context, Snapshot incoming, Credentials credentials) {
        return save(context, incoming, credentials, true);
    }

    private static Snapshot save(Context context, Snapshot incoming, Credentials credentials, boolean confirmed) {
        if (credentials == null || !ConnectionStore.matches(context, credentials)) return null;
        String fingerprint = fingerprint(credentials);
        Snapshot existing = loadForFingerprint(context, fingerprint);
        if (!SnapshotOrder.shouldReplace(existing, incoming)) return existing;
        Snapshot selected = confirmed ? incoming : READY_CONFIRMATION.select(existing, incoming, fingerprint, SystemClock.elapsedRealtime());
        if (confirmed) READY_CONFIRMATION.reset();
        if (selected == existing) return existing;
        SharedPreferences.Editor editor = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(JSON, selected.toJson()).putLong(RECEIVED, selected.receivedElapsedMs)
                .putLong(REQUEST_STARTED, selected.requestStartedElapsedMs)
                .putInt(BOOT, bootCount(context)).putString(CREDENTIAL, fingerprint);
        Snapshot last = !"unknown".equals(selected.displayState(SystemClock.elapsedRealtime())) ? selected : null;
        if (last == null && !context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).contains("last_json")
                && existing != null && existing.available && existing.validForSeconds > 0) last = existing;
        if (last != null) {
            editor.putString("last_json", last.toJson()).putLong("last_received", last.receivedElapsedMs)
                    .putLong("last_started", last.requestStartedElapsedMs).putInt("last_boot", bootCount(context))
                    .putString("last_credential", fingerprint);
        }
        editor.apply();
        return selected;
    }

    static Snapshot loadLastKnown(Context context) {
        Credentials credentials = ConnectionStore.load(context);
        if (credentials == null) return null;
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String json = prefs.getString("last_json", null);
        long started = prefs.getLong("last_started", -1L), received = prefs.getLong("last_received", -1L);
        if (json == null) return load(context); // Upgrade from the old single-snapshot cache.
        if (bootCount(context) < 0 || prefs.getInt("last_boot", -2) != bootCount(context)
                || !fingerprint(credentials).equals(prefs.getString("last_credential", null))
                || started < 0 || received < started || received > SystemClock.elapsedRealtime()) return null;
        try { return Snapshot.fromCache(json, started, received); }
        catch (Exception error) { Log.w("AgentBeacon", "Last-known status rejected (" + error.getClass().getSimpleName() + ")"); return null; }
    }

    static Snapshot load(Context context) {
        Credentials credentials = ConnectionStore.load(context);
        if (credentials == null) return null;
        return loadForFingerprint(context, fingerprint(credentials));
    }

    private static Snapshot loadForFingerprint(Context context, String expectedFingerprint) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String json = prefs.getString(JSON, null); long received = prefs.getLong(RECEIVED, -1L); long started = prefs.getLong(REQUEST_STARTED, -1L);
        int currentBoot = bootCount(context);
        if (currentBoot < 0 || json == null || received < 0 || started < 0 || SystemClock.elapsedRealtime() < received
                || prefs.getInt(BOOT, -2) != currentBoot
                || !expectedFingerprint.equals(prefs.getString(CREDENTIAL, null))) return null;
        try { return Snapshot.fromCache(json, started, received); }
        catch (Exception error) { Log.w("AgentBeacon", "Cached status was rejected (" + error.getClass().getSimpleName() + ")"); return null; }
    }

    private static String fingerprint(Credentials credentials) {
        try {
            byte[] tokenDigest = MessageDigest.getInstance("SHA-256").digest(credentials.token.getBytes(StandardCharsets.US_ASCII));
            StringBuilder result = new StringBuilder(64);
            for (byte value : tokenDigest) result.append(String.format(Locale.ROOT, "%02x", value & 0xff));
            return credentials.host + ":" + credentials.port + ":" + credentials.deviceId + ":" + result;
        } catch (Exception error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    static synchronized void clear(Context context) {
        READY_CONFIRMATION.reset();
        StatusPresentation.reset();
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply();
    }

    private static int bootCount(Context context) {
        try { return Settings.Global.getInt(context.getContentResolver(), Settings.Global.BOOT_COUNT, -1); }
        catch (Exception error) { Log.w("AgentBeacon", "Could not read boot count (" + error.getClass().getSimpleName() + ")"); return -1; }
    }
}
