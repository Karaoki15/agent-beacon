package com.agentbeacon.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.util.Log;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.LinkedHashMap;
import java.util.Map;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class ConnectionStore {
    private static final String PREFS = "beacon_private";
    private static final String KEY = "credentials_gcm";
    private static final String KEY_ALIAS = "agent_beacon_pairing_v1";
    private ConnectionStore() {}

    static void save(Context context, Credentials credentials) throws Exception {
        Map<String, Object> data = new LinkedHashMap<String, Object>();
        data.put("host", credentials.host); data.put("port", credentials.port); data.put("token", credentials.token);
        data.put("instance_id", credentials.instanceId); data.put("device_id", credentials.deviceId); data.put("legacy", credentials.legacy);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, secretKey());
        byte[] iv = cipher.getIV(); byte[] encrypted = cipher.doFinal(MiniJson.stringify(data).getBytes(StandardCharsets.UTF_8));
        byte[] packed = new byte[iv.length + encrypted.length]; System.arraycopy(iv, 0, packed, 0, iv.length); System.arraycopy(encrypted, 0, packed, iv.length, encrypted.length);
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, Base64.encodeToString(packed, Base64.NO_WRAP)).apply();
    }

    @SuppressWarnings("unchecked")
    static Credentials load(Context context) {
        try {
            String encoded = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null);
            if (encoded == null) return null;
            byte[] packed = Base64.decode(encoded, Base64.NO_WRAP); if (packed.length < 29) return null;
            byte[] iv = new byte[12]; byte[] body = new byte[packed.length - iv.length];
            System.arraycopy(packed, 0, iv, 0, iv.length); System.arraycopy(packed, iv.length, body, 0, body.length);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.DECRYPT_MODE, secretKey(), new GCMParameterSpec(128, iv));
            Object decoded = MiniJson.parse(cipher.doFinal(body)); if (!(decoded instanceof Map)) return null;
            Map<String, Object> map = (Map<String, Object>) decoded;
            String host = string(map.get("host")); String token = string(map.get("token")); String instance = string(map.get("instance_id")); String device = string(map.get("device_id"));
            Object portValue = map.get("port"); if (host == null || token == null || instance == null || device == null || !(portValue instanceof Double)) return null;
            PairingInput.validateStoredAddress(host); // revalidate at every use boundary
            int port = ((Double) portValue).intValue(); if (port < 1 || port > 65535) return null;
            return new Credentials(host, port, token, instance, device, Boolean.TRUE.equals(map.get("legacy")));
        } catch (Exception error) {
            Log.w("AgentBeacon", "Encrypted pairing credentials could not be loaded (" + error.getClass().getSimpleName() + ")");
            return null;
        }
    }

    static void clear(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY).apply();
        try { KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null); if (store.containsAlias(KEY_ALIAS)) store.deleteEntry(KEY_ALIAS); }
        catch (Exception error) { Log.w("AgentBeacon", "Could not delete the pairing key (" + error.getClass().getSimpleName() + ")"); }
    }

    static boolean isConnected(Context context) { return load(context) != null; }

    static boolean matches(Context context, Credentials expected) {
        Credentials current = load(context);
        return current != null && expected != null && current.host.equals(expected.host) && current.port == expected.port
                && current.token.equals(expected.token) && current.instanceId.equals(expected.instanceId);
    }

    static boolean relocate(Context context, Credentials expected, Credentials verified) throws Exception {
        if (!matches(context, expected) || expected.legacy || verified.legacy
                || !expected.token.equals(verified.token) || !expected.deviceId.equals(verified.deviceId)) return false;
        save(context, verified);
        SnapshotCache.clear(context);
        return true;
    }

    private static String string(Object value) { return value instanceof String && !((String) value).isEmpty() ? (String) value : null; }
    private static SecretKey secretKey() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (store.containsAlias(KEY_ALIAS)) return (SecretKey) store.getKey(KEY_ALIAS, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setRandomizedEncryptionRequired(true).build());
        return generator.generateKey();
    }
}
