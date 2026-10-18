package com.agentbeacon.android;

import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

final class PairingInput {
    final String host;
    final int port;
    final String secret;
    final boolean legacy;

    private PairingInput(String host, int port, String secret, boolean legacy) { this.host = host; this.port = port; this.secret = secret; this.legacy = legacy; }

    static PairingInput parse(String raw) throws Exception {
        String input = raw == null ? "" : raw.trim();
        if (input.length() > 4096) throw new IllegalArgumentException("Pairing text is too long.");
        URI uri = new URI(input);
        if ("agentbeacon".equals(uri.getScheme())) {
            if (!"pair".equals(uri.getHost()) || uri.getRawUserInfo() != null || uri.getPort() != -1 || uri.getPath() != null && !uri.getPath().isEmpty()) throw new IllegalArgumentException("Invalid Agent Beacon pairing link.");
            Map<String, String> query = query(uri.getRawQuery());
            if (query.size() != 2 || !query.containsKey("host") || !query.containsKey("port")) throw new IllegalArgumentException("Pairing link needs host and port.");
            String code = fragmentValue(uri.getRawFragment(), "code");
            if (!code.matches("[A-Za-z0-9_-]{43}")) throw new IllegalArgumentException("Pairing code is invalid.");
            return new PairingInput(validateHost(query.get("host")), parsePort(query.get("port")), code, false);
        }
        // Explicit pasted legacy link only. The old process-local token may stop working after server restart.
        if (!"http".equals(uri.getScheme()) || uri.getRawUserInfo() != null || uri.getRawQuery() != null
                || (uri.getRawPath() != null && !uri.getRawPath().isEmpty() && !"/".equals(uri.getRawPath()))) throw new IllegalArgumentException("Paste an Agent Beacon pairing link.");
        String token = fragmentValue(uri.getRawFragment(), "token");
        if (!token.matches("[A-Za-z0-9._~-]{20,256}")) throw new IllegalArgumentException("Legacy token is invalid.");
        return new PairingInput(validateHost(uri.getHost()), validPort(uri.getPort()), token, true);
    }

    private static Map<String, String> query(String raw) throws Exception {
        if (raw == null || raw.isEmpty()) throw new IllegalArgumentException("Pairing link is missing host and port.");
        Map<String, String> result = new LinkedHashMap<String, String>();
        for (String piece : raw.split("&", -1)) {
            int split = piece.indexOf('='); if (split <= 0) throw new IllegalArgumentException("Invalid pairing link.");
            String key = URLDecoder.decode(piece.substring(0, split), "UTF-8");
            String value = URLDecoder.decode(piece.substring(split + 1), "UTF-8");
            if (result.put(key, value) != null) throw new IllegalArgumentException("Duplicate pairing field.");
        }
        return result;
    }
    private static String fragmentValue(String fragment, String expected) throws Exception {
        if (fragment == null || !fragment.startsWith(expected + "=") || fragment.indexOf('&') >= 0) throw new IllegalArgumentException("Pairing secret is missing.");
        return URLDecoder.decode(fragment.substring(expected.length() + 1), "UTF-8");
    }
    private static String validateHost(String host) {
        if (host == null) throw new IllegalArgumentException("Server address is missing.");
        String[] pieces = host.split("\\.", -1); if (pieces.length != 4) throw new IllegalArgumentException("Use a private IPv4 address.");
        int[] octets = new int[4];
        for (int i = 0; i < 4; i++) {
            if (!pieces[i].matches("0|[1-9][0-9]{0,2}")) throw new IllegalArgumentException("Use a canonical private IPv4 address.");
            octets[i] = Integer.parseInt(pieces[i]); if (octets[i] > 255) throw new IllegalArgumentException("Invalid IPv4 address.");
        }
        boolean privateRange = octets[0] == 10 || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31) || (octets[0] == 192 && octets[1] == 168);
        if (!privateRange) throw new IllegalArgumentException("Only RFC1918 private addresses are allowed.");
        return host;
    }
    static String validateStoredAddress(String host) { return validateHost(host); }
    private static int parsePort(String port) { if (port == null || !port.matches("[1-9][0-9]{0,4}")) throw new IllegalArgumentException("Invalid server port."); return validPort(Integer.parseInt(port)); }
    private static int validPort(int port) { if (port < 1 || port > 65535) throw new IllegalArgumentException("Invalid server port."); return port; }
}
