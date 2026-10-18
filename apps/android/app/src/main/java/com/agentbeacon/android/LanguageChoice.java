package com.agentbeacon.android;

import java.util.Locale;

/** Explicit choices override the phone; automatic is English only for English phones. */
final class LanguageChoice {
    static String resolve(String mode, String systemLanguage) {
        if ("en".equals(mode) || "uk".equals(mode)) return mode;
        String language = systemLanguage == null ? "" : systemLanguage.toLowerCase(Locale.ROOT);
        return language.equals("en") || language.startsWith("en-") || language.startsWith("en_") ? "en" : "uk";
    }
    private LanguageChoice() {}
}
