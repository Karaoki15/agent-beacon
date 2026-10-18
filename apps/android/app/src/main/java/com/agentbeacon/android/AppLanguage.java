package com.agentbeacon.android;

import android.content.Context;
import android.content.res.Configuration;
import android.content.res.Resources;
import java.util.Locale;

final class AppLanguage {
    static String mode(Context context) {
        return context.getSharedPreferences("beacon_appearance", Context.MODE_PRIVATE).getString("language", "auto");
    }
    static String language(Context context) {
        return LanguageChoice.resolve(mode(context), Resources.getSystem().getConfiguration().getLocales().get(0).getLanguage());
    }
    static boolean save(Context context, String mode) {
        if (!"auto".equals(mode) && !"en".equals(mode) && !"uk".equals(mode)) return false;
        return context.getSharedPreferences("beacon_appearance", Context.MODE_PRIVATE).edit().putString("language", mode).commit();
    }
    static Context wrap(Context context) {
        Configuration configuration = new Configuration(context.getResources().getConfiguration());
        Locale locale = Locale.forLanguageTag(language(context));
        configuration.setLocale(locale); configuration.setLayoutDirection(locale);
        return context.createConfigurationContext(configuration);
    }
    private AppLanguage() {}
}
