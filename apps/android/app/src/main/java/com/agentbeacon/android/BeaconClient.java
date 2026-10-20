package com.agentbeacon.android;

import java.io.ByteArrayOutputStream;
import java.io.BufferedInputStream;
import java.io.InterruptedIOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;

final class BeaconClient {
    private static final ScheduledThreadPoolExecutor DEADLINE_EXECUTOR = createDeadlineExecutor();

    private BeaconClient() {}

    interface UpdateListener { void onSnapshot(Snapshot snapshot, String transport, long responseMs); }

    static final class LiveRequest {
        private HttpURLConnection connection;
        private java.net.DatagramSocket discovery;
        private boolean cancelled;
        synchronized void attach(HttpURLConnection value) throws InterruptedIOException {
            if (cancelled) { value.disconnect(); throw new InterruptedIOException("Cancelled"); }
            connection = value;
        }
        synchronized void attach(java.net.DatagramSocket value) throws InterruptedIOException {
            if (cancelled) { value.close(); throw new InterruptedIOException("Cancelled"); }
            discovery = value;
        }
        synchronized void cancel() { cancelled = true; if (connection != null) connection.disconnect(); if (discovery != null) discovery.close(); }
        synchronized boolean cancelled() { return cancelled; }
    }

    private static String fallbackAddress;
    private static long fallbackUntil;

    static boolean updates(Credentials credentials, boolean saver, LiveRequest active, UpdateListener listener) throws Exception {
        long started = android.os.SystemClock.elapsedRealtime();
        boolean fallback;
        synchronized (BeaconClient.class) {
            fallback = credentials.baseUrl().equals(fallbackAddress) && started < fallbackUntil;
        }
        if (fallback) {
            Snapshot snapshot = status(credentials, active);
            if (!active.cancelled()) listener.onSnapshot(snapshot, "Status checks (compatibility)", snapshot.receivedElapsedMs - snapshot.requestStartedElapsedMs);
            return false;
        }
        HttpURLConnection connection = (HttpURLConnection) new URL(credentials.baseUrl() + "/api/events").openConnection();
        active.attach(connection);
        ScheduledFuture<?> deadlineTask = DEADLINE_EXECUTOR.schedule(connection::disconnect, Protocol.REQUEST_DEADLINE_MS, TimeUnit.MILLISECONDS);
        boolean useFallback = false;
        try {
            connection.setRequestMethod("GET"); connection.setConnectTimeout(Protocol.CONNECT_TIMEOUT_MS); connection.setReadTimeout(12000);
            connection.setInstanceFollowRedirects(false); connection.setUseCaches(false);
            connection.setRequestProperty("Authorization", "Bearer " + credentials.token);
            connection.setRequestProperty("Accept", "text/event-stream"); connection.setRequestProperty("Cache-Control", "no-store");
            connection.setRequestProperty("X-Beacon-Battery-Saver", saver ? "1" : "0");
            int code = connection.getResponseCode();
            if (code == 404 || code == 405 || code == 503) {
                synchronized (BeaconClient.class) {
                    fallbackAddress = credentials.baseUrl(); fallbackUntil = android.os.SystemClock.elapsedRealtime() + (code == 503 ? 10000L : 60000L);
                }
                useFallback = true;
            } else {
                if (code != 200) throw new HttpFailure(code);
                String type = connection.getContentType();
                if (type == null || !type.toLowerCase(java.util.Locale.ROOT).startsWith("text/event-stream")) throw new MiniJson.JsonException("Expected event stream");
                final long[] responseMs = {-1L};
                try (InputStream input = new BufferedInputStream(connection.getInputStream())) {
                    EventStreamReader.read(input, data -> {
                        if (active.cancelled()) throw new InterruptedIOException("Cancelled");
                        long received = android.os.SystemClock.elapsedRealtime();
                        if (responseMs[0] < 0) responseMs[0] = received - started;
                        Snapshot snapshot = Snapshot.parseEvent(data, started, received);
                        listener.onSnapshot(snapshot, "Live events", responseMs[0]);
                    });
                }
            }
        } finally {
            deadlineTask.cancel(false); connection.disconnect();
        }
        if (useFallback && !active.cancelled()) {
            Snapshot snapshot = status(credentials, active);
            if (!active.cancelled()) listener.onSnapshot(snapshot, "Status checks (compatibility)", snapshot.receivedElapsedMs - snapshot.requestStartedElapsedMs);
        }
        return !useFallback;
    }

    static Credentials pair(PairingInput input, String deviceName) throws Exception {
        if (input.legacy) return new Credentials(input.host, input.port, input.secret, "legacy", "legacy", true);
        Map<String, Object> payload = new LinkedHashMap<String, Object>(); payload.put("code", input.secret); payload.put("device_name", deviceName);
        byte[] response = request("POST", "http://" + input.host + ":" + input.port + "/api/pair", null,
                MiniJson.stringify(payload).getBytes(StandardCharsets.UTF_8));
        Object parsed = MiniJson.parse(response);
        if (!(parsed instanceof Map)) throw new MiniJson.JsonException("Pairing response must be an object");
        @SuppressWarnings("unchecked") Map<String, Object> map = (Map<String, Object>) parsed;
        String deviceId = requiredString(map.get("device_id"), "device_id");
        String token = requiredString(map.get("device_token"), "device_token");
        String instance = requiredString(map.get("instance_id"), "instance_id");
        if (!token.matches("[A-Za-z0-9._~-]{16,512}")) throw new MiniJson.JsonException("Invalid device token");
        return new Credentials(input.host, input.port, token, instance, deviceId, false);
    }

    static Snapshot status(Credentials credentials) throws Exception {
        return status(credentials, null);
    }

    private static Snapshot status(Credentials credentials, LiveRequest active) throws Exception {
        long requestStarted = android.os.SystemClock.elapsedRealtime();
        byte[] response = request("GET", credentials.baseUrl() + "/api/status", credentials.token, null, active);
        return Snapshot.parse(response, requestStarted, android.os.SystemClock.elapsedRealtime());
    }

    static byte[] discover(String host, int port, byte[] proof, LiveRequest active) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL("http://" + host + ":" + port + "/api/discovery").openConnection();
        active.attach(connection);
        long deadline = android.os.SystemClock.elapsedRealtime() + 500L;
        ScheduledFuture<?> timer = DEADLINE_EXECUTOR.schedule(connection::disconnect, 500L, TimeUnit.MILLISECONDS);
        try {
            connection.setRequestMethod("POST"); connection.setConnectTimeout(250); connection.setReadTimeout(250);
            connection.setInstanceFollowRedirects(false); connection.setUseCaches(false); connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json"); connection.setRequestProperty("Accept", "application/json");
            connection.setFixedLengthStreamingMode(proof.length);
            try (OutputStream out = connection.getOutputStream()) { out.write(proof); }
            if (connection.getResponseCode() != 200) return null;
            String type = connection.getContentType();
            if (type == null || !type.toLowerCase(java.util.Locale.ROOT).startsWith("application/json")) return null;
            byte[] bytes = readBounded(connection.getInputStream(), deadline);
            return bytes.length <= 1024 ? bytes : null;
        } finally { timer.cancel(false); connection.disconnect(); }
    }

    private static String requiredString(Object value, String field) throws MiniJson.JsonException {
        if (!(value instanceof String) || ((String) value).isEmpty() || ((String) value).length() > 512) throw new MiniJson.JsonException("Invalid " + field);
        return (String) value;
    }

    private static byte[] request(String method, String address, String token, byte[] body) throws Exception {
        return request(method, address, token, body, null);
    }

    private static byte[] request(String method, String address, String token, byte[] body, LiveRequest active) throws Exception {
        long deadline = android.os.SystemClock.elapsedRealtime() + Protocol.REQUEST_DEADLINE_MS;
        HttpURLConnection connection = null;
        ScheduledFuture<?> deadlineTask = null;
        try {
            connection = (HttpURLConnection) new URL(address).openConnection();
            if (active != null) active.attach(connection);
            final HttpURLConnection timedConnection = connection;
            long delay = Math.max(0L, deadline - android.os.SystemClock.elapsedRealtime());
            deadlineTask = DEADLINE_EXECUTOR.schedule(timedConnection::disconnect, delay, TimeUnit.MILLISECONDS);
            connection.setRequestMethod(method); connection.setConnectTimeout(Protocol.CONNECT_TIMEOUT_MS); connection.setReadTimeout(Protocol.READ_TIMEOUT_MS);
            connection.setInstanceFollowRedirects(false); connection.setUseCaches(false); connection.setDoInput(true);
            connection.setRequestProperty("Accept", "application/json"); connection.setRequestProperty("Cache-Control", "no-store");
            if (token != null) connection.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.setFixedLengthStreamingMode(body.length);
                try (OutputStream output = connection.getOutputStream()) { output.write(body); }
            }
            int status = connection.getResponseCode();
            checkDeadline(deadline);
            if (status != 200) throw new HttpFailure(status);
            String contentType = connection.getContentType();
            if (contentType == null || !contentType.toLowerCase(java.util.Locale.ROOT).startsWith("application/json")) throw new MiniJson.JsonException("Expected JSON response");
            return readBounded(connection.getInputStream(), deadline);
        } finally {
            if (deadlineTask != null) deadlineTask.cancel(false);
            if (connection != null) connection.disconnect();
        }
    }

    private static ScheduledThreadPoolExecutor createDeadlineExecutor() {
        ThreadFactory daemonFactory = runnable -> {
            Thread thread = new Thread(runnable, "AgentBeacon-request-deadline");
            thread.setDaemon(true);
            return thread;
        };
        ScheduledThreadPoolExecutor executor = new ScheduledThreadPoolExecutor(1, daemonFactory);
        executor.setRemoveOnCancelPolicy(true);
        executor.setExecuteExistingDelayedTasksAfterShutdownPolicy(false);
        return executor;
    }

    private static byte[] readBounded(InputStream input, long deadline) throws Exception {
        try (InputStream in = input; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096]; int count;
            while (true) {
                checkDeadline(deadline);
                count = in.read(buffer);
                checkDeadline(deadline);
                if (count == -1) break;
                if (out.size() + count > Protocol.MAX_RESPONSE_BYTES) throw new MiniJson.JsonException("Response too large");
                out.write(buffer, 0, count);
            }
            return out.toByteArray();
        }
    }

    private static void checkDeadline(long deadline) throws java.net.SocketTimeoutException {
        if (android.os.SystemClock.elapsedRealtime() >= deadline) throw new java.net.SocketTimeoutException("Response exceeded total deadline");
    }

    static final class HttpFailure extends Exception {
        final int statusCode;
        HttpFailure(int statusCode) { super("HTTP " + statusCode); this.statusCode = statusCode; }
    }
}
