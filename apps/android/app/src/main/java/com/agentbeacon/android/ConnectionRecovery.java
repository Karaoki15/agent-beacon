package com.agentbeacon.android;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.SystemClock;
import android.util.Log;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Set;

/** A short search at startup/on failure, never a second continuous status loop. */
final class ConnectionRecovery {
    private static long nextSearch;
    private static String lastResult = "Ready; searches only at startup or after connection errors";

    static synchronized void reset() { nextSearch = 0; }
    static synchronized String diagnostics() { return lastResult; }

    static Credentials find(Context context, Credentials saved, BeaconClient.LiveRequest active, boolean saver) {
        synchronized (ConnectionRecovery.class) {
            if (saved.legacy || active.cancelled() || SystemClock.elapsedRealtime() < nextSearch) return null;
            nextSearch = SystemClock.elapsedRealtime() + (saver ? 60000L : 15000L);
            lastResult = "Looking for your paired computer";
        }
        try {
            Credentials found = search(context, saved, active);
            synchronized (ConnectionRecovery.class) { lastResult = found == null ? "Computer not found; saved pairing retained" : "Paired computer verified at " + found.host + ":" + found.port; }
            return found != null && (!found.host.equals(saved.host) || found.port != saved.port || !found.instanceId.equals(saved.instanceId)) ? found : null;
        } catch (Exception error) {
            synchronized (ConnectionRecovery.class) { lastResult = "Search unavailable; saved pairing retained"; }
            if (!active.cancelled()) Log.d("AgentBeacon", "Local discovery unavailable (" + error.getClass().getSimpleName() + ")");
            return null;
        }
    }

    private static Credentials search(Context context, Credentials saved, BeaconClient.LiveRequest active) throws Exception {
        ConnectivityManager manager = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (manager == null) return null;
        Network network = manager.getActiveNetwork();
        NetworkCapabilities caps = network == null ? null : manager.getNetworkCapabilities(network);
        if (caps == null || (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) && !caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) return null;
        Set<String> targets = new LinkedHashSet<>(); targets.add(saved.host);
        LinkProperties properties = manager.getLinkProperties(network);
        if (properties != null) for (LinkAddress link : properties.getLinkAddresses()) {
            if (!(link.getAddress() instanceof Inet4Address) || link.getPrefixLength() < 1 || link.getPrefixLength() > 30) continue;
            byte[] ip = link.getAddress().getAddress(); int bits = link.getPrefixLength();
            for (int i = 0; i < 4; i++) { int kept = Math.max(0, Math.min(8, bits - i * 8)); ip[i] = (byte) ((ip[i] & 255) | (255 >>> kept)); }
            targets.add(InetAddress.getByAddress(ip).getHostAddress());
            if (targets.size() >= 6) break;
        }
        targets.add("255.255.255.255");
        String nonce = DiscoveryProtocol.nonce(); byte[] request = DiscoveryProtocol.request(saved, nonce);
        try (DatagramSocket socket = new DatagramSocket()) {
            active.attach(socket); network.bindSocket(socket); socket.setBroadcast(true);
            for (int attempt = 0; attempt < 2 && !active.cancelled(); attempt++) {
                for (String target : targets) {
                    try { socket.send(new DatagramPacket(request, request.length, InetAddress.getByName(target), DiscoveryProtocol.PORT)); }
                    catch (java.io.IOException unavailable) { if (active.cancelled()) return null; }
                }
                long deadline = SystemClock.elapsedRealtime() + 750L;
                for (int count = 0; count < 16 && !active.cancelled(); count++) {
                    long remaining = deadline - SystemClock.elapsedRealtime(); if (remaining <= 0) break;
                    socket.setSoTimeout((int) remaining);
                    DatagramPacket packet = new DatagramPacket(new byte[1025], 1025);
                    try { socket.receive(packet); } catch (SocketTimeoutException timeout) { break; }
                    if (packet.getPort() != DiscoveryProtocol.PORT || packet.getLength() > 1024) continue;
                    try {
                        Credentials found = DiscoveryProtocol.verify(Arrays.copyOf(packet.getData(), packet.getLength()), packet.getAddress().getHostAddress(), saved, nonce);
                        if (found != null) return found;
                    } catch (IllegalArgumentException | MiniJson.JsonException invalid) { /* Ignore untrusted discovery responses. */ }
                }
            }
        } catch (java.io.IOException unavailable) {
            if (active.cancelled()) return null;
            Log.d("AgentBeacon", "UDP discovery unavailable; checking the remembered computer address");
        }
        // Broadcast can be filtered by a router. Probe only the remembered IP
        // and the launcher's small port range, with proofs instead of tokens.
        Set<Integer> ports = new LinkedHashSet<>(); ports.add(saved.port);
        for (int port = 8765; port <= 8775; port++) ports.add(port);
        long deadline = SystemClock.elapsedRealtime() + 2500L;
        for (int port : ports) {
            if (active.cancelled() || SystemClock.elapsedRealtime() >= deadline) break;
            try {
                byte[] reply = BeaconClient.discover(saved.host, port, request, active);
                Credentials found = reply == null ? null : DiscoveryProtocol.verify(reply, saved.host, saved, nonce);
                if (found != null && found.port == port) return found;
            } catch (Exception unavailable) { if (active.cancelled()) return null; }
        }
        return null;
    }
    private ConnectionRecovery() {}
}
