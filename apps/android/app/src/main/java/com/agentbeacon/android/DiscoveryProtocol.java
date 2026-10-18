package com.agentbeacon.android;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Authenticated endpoint relocation; neither discovery packet contains the bearer token. */
final class DiscoveryProtocol {
    static final int PORT = 47665;
    static final String NAME = "agent-beacon-discovery-v1";

    static String nonce() {
        byte[] value = new byte[32]; new SecureRandom().nextBytes(value); return hex(value);
    }

    static byte[] request(Credentials credentials, String nonce) throws Exception {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("protocol", NAME); data.put("device_id", credentials.deviceId); data.put("nonce", nonce);
        data.put("proof", sign(credentials, NAME + "\nprobe\n" + credentials.deviceId + "\n" + nonce));
        return MiniJson.stringify(data).getBytes(StandardCharsets.US_ASCII);
    }

    static Credentials verify(byte[] bytes, String sender, Credentials expected, String nonce) throws Exception {
        if (bytes.length > 1024) return null;
        Object value = MiniJson.parse(bytes);
        if (!(value instanceof Map)) return null;
        Map<?, ?> data = (Map<?, ?>) value;
        if (!NAME.equals(data.get("protocol")) || !nonce.equals(data.get("nonce")) || !sender.equals(data.get("host"))) return null;
        PairingInput.validateStoredAddress(sender);
        Object rawPort = data.get("port"), rawInstance = data.get("instance_id"), rawProof = data.get("proof");
        if (!(rawPort instanceof Double) || !(rawInstance instanceof String) || !(rawProof instanceof String)) return null;
        double number = (Double) rawPort;
        if (!Double.isFinite(number) || number != Math.rint(number) || number < 1 || number > 65535) return null;
        int port = (int) number; String instance = (String) rawInstance, proof = (String) rawProof;
        if (!instance.matches("[0-9a-f-]{36}") || !proof.matches("[0-9a-f]{64}")) return null;
        String message = NAME + "\nreply\n" + expected.deviceId + "\n" + nonce + "\n" + sender + "\n" + port + "\n" + instance;
        if (!MessageDigest.isEqual(sign(expected, message).getBytes(StandardCharsets.US_ASCII), proof.getBytes(StandardCharsets.US_ASCII))) return null;
        return new Credentials(sender, port, expected.token, instance, expected.deviceId, false);
    }

    private static String sign(Credentials credentials, String message) throws Exception {
        byte[] key = MessageDigest.getInstance("SHA-256").digest(credentials.token.getBytes(StandardCharsets.US_ASCII));
        Mac mac = Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec(key, "HmacSHA256"));
        return hex(mac.doFinal(message.getBytes(StandardCharsets.US_ASCII)));
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) result.append(Character.forDigit((value >>> 4) & 15, 16)).append(Character.forDigit(value & 15, 16));
        return result.toString();
    }
    private DiscoveryProtocol() {}
}
