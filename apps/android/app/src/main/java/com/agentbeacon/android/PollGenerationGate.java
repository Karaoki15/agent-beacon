package com.agentbeacon.android;

/** Keeps polling single-flight and rejects completions from an earlier lifecycle generation. */
final class PollGenerationGate {
    private int generation;
    private int inFlightGeneration = -1;

    int current() { return generation; }

    int invalidate() { return ++generation; }

    boolean begin(int expectedGeneration) {
        if (expectedGeneration != generation || inFlightGeneration != -1) return false;
        inFlightGeneration = expectedGeneration;
        return true;
    }

    /** Completes the request and reports whether its lifecycle is still current. */
    boolean complete(int requestGeneration) {
        if (inFlightGeneration == requestGeneration) inFlightGeneration = -1;
        return requestGeneration == generation;
    }
}
