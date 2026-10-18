package com.agentbeacon.android;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/** Bounded UTF-8 SSE frames; status parsing still performs strict JSON validation. */
final class EventStreamReader {
    interface Listener { void onData(byte[] data) throws Exception; }

    static void read(InputStream input, Listener listener) throws Exception {
        ByteArrayOutputStream line = new ByteArrayOutputStream();
        ByteArrayOutputStream frame = new ByteArrayOutputStream();
        int total = 0, frames = 0, value;
        while ((value = input.read()) != -1) {
            if (++total > 2 * 1024 * 1024) throw new IOException("Event stream too large");
            if (value != '\n') {
                if (line.size() >= Protocol.MAX_RESPONSE_BYTES + 16) throw new IOException("Event line too large");
                line.write(value);
                continue;
            }
            byte[] bytes = line.toByteArray(); line.reset();
            int length = bytes.length;
            if (length > 0 && bytes[length - 1] == '\r') length--;
            if (length == 0) {
                if (frame.size() > 0) {
                    if (++frames > 256) throw new IOException("Too many events");
                    listener.onData(frame.toByteArray()); frame.reset();
                }
            } else if (length >= 5 && bytes[0] == 'd' && bytes[1] == 'a' && bytes[2] == 't'
                    && bytes[3] == 'a' && bytes[4] == ':') {
                int offset = length > 5 && bytes[5] == ' ' ? 6 : 5;
                int extra = length - offset + (frame.size() > 0 ? 1 : 0);
                if (frame.size() + extra > Protocol.MAX_RESPONSE_BYTES) throw new IOException("Event frame too large");
                if (frame.size() > 0) frame.write('\n');
                frame.write(bytes, offset, length - offset);
            } else if (bytes[0] != ':' && !new String(bytes, 0, length, StandardCharsets.US_ASCII).startsWith("event:")) {
                throw new IOException("Unexpected event field");
            }
        }
        if (frames == 0 || line.size() > 0 || frame.size() > 0) throw new IOException("Incomplete event stream");
    }

    private EventStreamReader() {}
}
