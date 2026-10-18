package com.agentbeacon.android;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Small strict JSON codec so the protocol parser has no third-party dependency. */
final class MiniJson {
    private MiniJson() {}

    static Object parse(byte[] bytes) throws JsonException {
        if (bytes == null || bytes.length > Protocol.MAX_RESPONSE_BYTES) throw new JsonException("JSON size");
        try {
            String input = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes)).toString();
            Parser parser = new Parser(input);
            Object value = parser.value(0);
            parser.space();
            if (!parser.end()) throw new JsonException("Trailing JSON data");
            return value;
        } catch (CharacterCodingException error) {
            throw new JsonException("Invalid UTF-8");
        }
    }

    static String stringify(Object value) {
        StringBuilder out = new StringBuilder();
        write(value, out);
        return out.toString();
    }

    private static void write(Object value, StringBuilder out) {
        if (value == null) out.append("null");
        else if (value instanceof String) quote((String) value, out);
        else if (value instanceof Boolean || value instanceof Number) out.append(value.toString());
        else if (value instanceof Map) {
            out.append('{'); boolean first = true;
            for (Object item : ((Map<?, ?>) value).entrySet()) {
                Map.Entry<?, ?> entry = (Map.Entry<?, ?>) item;
                if (!(entry.getKey() instanceof String)) throw new IllegalArgumentException("JSON object key");
                if (!first) out.append(','); first = false;
                quote((String) entry.getKey(), out); out.append(':'); write(entry.getValue(), out);
            }
            out.append('}');
        } else if (value instanceof List) {
            out.append('['); boolean first = true;
            for (Object item : (List<?>) value) { if (!first) out.append(','); first = false; write(item, out); }
            out.append(']');
        } else throw new IllegalArgumentException("Unsupported JSON type");
    }

    private static void quote(String text, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default: if (c < 0x20) out.append(String.format("\\u%04x", (int) c)); else out.append(c);
            }
        }
        out.append('"');
    }

    static final class JsonException extends Exception {
        JsonException(String message) { super(message); }
    }

    private static final class Parser {
        private final String text;
        private int at;
        Parser(String text) { this.text = text; }
        boolean end() { return at >= text.length(); }
        void space() { while (!end() && (text.charAt(at) == ' ' || text.charAt(at) == '\n' || text.charAt(at) == '\r' || text.charAt(at) == '\t')) at++; }

        Object value(int depth) throws JsonException {
            if (depth > 32) throw new JsonException("JSON nesting");
            space(); if (end()) throw new JsonException("Missing JSON value");
            char c = text.charAt(at);
            if (c == '{') return object(depth + 1);
            if (c == '[') return array(depth + 1);
            if (c == '"') return string();
            if (c == 't' && literal("true")) return Boolean.TRUE;
            if (c == 'f' && literal("false")) return Boolean.FALSE;
            if (c == 'n' && literal("null")) return null;
            if (c == '-' || (c >= '0' && c <= '9')) return number();
            throw new JsonException("Invalid JSON value");
        }

        private Map<String, Object> object(int depth) throws JsonException {
            at++; space(); Map<String, Object> out = new LinkedHashMap<String, Object>();
            if (take('}')) return out;
            while (true) {
                space(); if (end() || text.charAt(at) != '"') throw new JsonException("Object key expected");
                String key = string(); space(); if (!take(':')) throw new JsonException("Colon expected");
                if (out.containsKey(key)) throw new JsonException("Duplicate JSON key");
                out.put(key, value(depth)); space();
                if (take('}')) return out;
                if (!take(',')) throw new JsonException("Comma expected");
            }
        }

        private List<Object> array(int depth) throws JsonException {
            at++; space(); List<Object> out = new ArrayList<Object>();
            if (take(']')) return out;
            while (true) {
                if (out.size() > 128) throw new JsonException("Array too large");
                out.add(value(depth)); space();
                if (take(']')) return out;
                if (!take(',')) throw new JsonException("Comma expected");
            }
        }

        private String string() throws JsonException {
            at++; StringBuilder out = new StringBuilder();
            while (!end()) {
                char c = text.charAt(at++);
                if (c == '"') return out.toString();
                if (c < 0x20) throw new JsonException("Control in string");
                if (c != '\\') { out.append(c); continue; }
                if (end()) throw new JsonException("Incomplete escape");
                char e = text.charAt(at++);
                switch (e) {
                    case '"': case '\\': case '/': out.append(e); break;
                    case 'b': out.append('\b'); break;
                    case 'f': out.append('\f'); break;
                    case 'n': out.append('\n'); break;
                    case 'r': out.append('\r'); break;
                    case 't': out.append('\t'); break;
                    case 'u':
                        if (at + 4 > text.length()) throw new JsonException("Incomplete unicode escape");
                        try { out.append((char) Integer.parseInt(text.substring(at, at + 4), 16)); }
                        catch (NumberFormatException error) { throw new JsonException("Invalid unicode escape"); }
                        at += 4; break;
                    default: throw new JsonException("Invalid escape");
                }
            }
            throw new JsonException("Unterminated string");
        }

        private Double number() throws JsonException {
            int start = at;
            if (take('-') && end()) throw new JsonException("Invalid number");
            if (take('0')) { if (!end() && Character.isDigit(text.charAt(at))) throw new JsonException("Leading zero"); }
            else { if (end() || text.charAt(at) < '1' || text.charAt(at) > '9') throw new JsonException("Invalid number"); while (!end() && Character.isDigit(text.charAt(at))) at++; }
            if (take('.')) { int digits = at; while (!end() && Character.isDigit(text.charAt(at))) at++; if (digits == at) throw new JsonException("Invalid fraction"); }
            if (!end() && (text.charAt(at) == 'e' || text.charAt(at) == 'E')) {
                at++; if (!end() && (text.charAt(at) == '+' || text.charAt(at) == '-')) at++;
                int digits = at; while (!end() && Character.isDigit(text.charAt(at))) at++; if (digits == at) throw new JsonException("Invalid exponent");
            }
            try { double d = Double.parseDouble(text.substring(start, at)); if (Double.isInfinite(d) || Double.isNaN(d)) throw new Exception(); return d; }
            catch (Exception error) { throw new JsonException("Invalid number"); }
        }
        private boolean literal(String value) { if (!text.regionMatches(at, value, 0, value.length())) return false; at += value.length(); return true; }
        private boolean take(char c) { if (!end() && text.charAt(at) == c) { at++; return true; } return false; }
    }
}
