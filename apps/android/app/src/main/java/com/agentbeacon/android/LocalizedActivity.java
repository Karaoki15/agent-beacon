package com.agentbeacon.android;

import android.app.Activity;
import android.content.Context;

/** Recreate screens after a system-language change without changing pairing or service state. */
abstract class LocalizedActivity extends Activity {
    private String language;
    @Override protected void attachBaseContext(Context base) {
        language = AppLanguage.language(base);
        super.attachBaseContext(AppLanguage.wrap(base));
    }
    @Override protected void onResume() {
        super.onResume();
        if (!AppLanguage.language(this).equals(language)) recreate();
    }
}
