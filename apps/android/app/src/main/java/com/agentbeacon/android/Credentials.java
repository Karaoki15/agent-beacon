package com.agentbeacon.android;

final class Credentials {
    final String host, token, instanceId, deviceId;
    final int port;
    final boolean legacy;
    Credentials(String host, int port, String token, String instanceId, String deviceId, boolean legacy) {
        this.host = host; this.port = port; this.token = token; this.instanceId = instanceId; this.deviceId = deviceId; this.legacy = legacy;
    }
    String baseUrl() { return "http://" + host + ":" + port; }
}
