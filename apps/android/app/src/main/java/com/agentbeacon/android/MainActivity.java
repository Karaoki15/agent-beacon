package com.agentbeacon.android;

import android.Manifest;
import android.app.Activity;
import android.annotation.SuppressLint;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.util.Log;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;

public final class MainActivity extends LocalizedActivity {
    private static final AtomicLong PAIR_GENERATION = new AtomicLong();
    private static final int REQUEST_NOTIFICATIONS = 90;
    private static final int REQUEST_CAMERA = 91;
    static final String EXTRA_SCANNER_FAILURE = "com.agentbeacon.android.extra.SCANNER_FAILURE";
    static final String EXTRA_RECONNECT = "com.agentbeacon.android.extra.RECONNECT";
    private static final int BG = Color.rgb(8, 8, 8);
    private static final int PANEL = Color.rgb(20, 20, 20);
    private static final int MUTED = Color.rgb(170, 170, 170);
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private EditText pairingInput;
    private BeaconCircleView circle;
    private TextView connectionLine, freshnessLine, sessionsTitle, monitorLine, messageLine;
    private LinearLayout projectsList, connectionCard;
    private Button connectionToggle;
    private boolean showConnectionSettings;
    private String projectsKey;
    private Button pairButton, monitorButton, disconnectButton;
    private Button scanButton;
    private Button checkButton;
    private boolean pendingConnectPermission;
    private long manualCheckStarted;
    private boolean scannerPending;
    private boolean scannerInProgress;
    private boolean pairingInProgress;
    private long pairAttemptGeneration;
    private boolean receiverRegistered;
    private final PollGenerationGate foregroundPollGate = new PollGenerationGate();
    private NetworkReconnect networkReconnect;
    private Snapshot latest;
    private int pollFailures;
    private boolean discoveryPending = true;
    private BeaconClient.LiveRequest activeRequest;
    private final SnapshotDelivery delivery = new SnapshotDelivery(handler);
    private static boolean visible;

    static boolean isVisible() { return visible; }

    private final Runnable refreshUi = new Runnable() {
        @Override public void run() { render(); if (isResumed()) handler.postDelayed(this, 1000L); }
    };
    private final Runnable pollForeground = new Runnable() {
        @Override public void run() {
            if (!resumed) return;
            pollOnce();
        }
    };
    private final BroadcastReceiver snapshotReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            latest = SnapshotCache.load(MainActivity.this); render();
            if (StatusService.isRunning()) {
                foregroundPollGate.invalidate(); delivery.cancel();
                if (activeRequest != null) { activeRequest.cancel(); activeRequest = null; }
                handler.removeCallbacks(pollForeground);
            }
            if (resumed && !StatusService.isRunning()) {
                handler.removeCallbacks(pollForeground); handler.post(pollForeground);
            }
        }
    };

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        StatusNotifier.ensureChannels(this);
        if (StatusService.isRunning()) StatusNotifier.languageChanged(this);
        if (Build.VERSION.SDK_INT >= 30) getWindow().setDecorFitsSystemWindows(false);
        getWindow().setStatusBarColor(BG); getWindow().setNavigationBarColor(BG);
        getWindow().getDecorView().setSystemUiVisibility(0);
        buildUi();
        if (state != null) showConnectionSettings = state.getBoolean("show_connection", false);
        scannerPending = state != null && state.getBoolean("scanner_pending", false);
        if (scannerPending) setScanButtonEnabled(false);
        latest = SnapshotCache.load(this);
        String launchUri = getIntent() == null || getIntent().getData() == null ? null : getIntent().getDataString();
        if (launchUri != null) { pairingInput.setText(launchUri); showConnectionSettings = true; }
        String scannerFailure = getSharedPreferences("beacon_runtime", MODE_PRIVATE).getString("last_scanner_failure", null);
        if (scannerFailure != null && !scannerFailure.isEmpty()) {
            setMessage(AppText.tr(MainActivity.this, "QR scanner error: ") + scannerFailure + AppText.tr(MainActivity.this, ". Paste the pairing link instead."));
        }
        render();
    }

    private void buildUi() {
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.setBackgroundColor(BG);
        LinearLayout page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL); page.setPadding(dp(22), dp(22), dp(22), dp(32));
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            int topInset;
            int bottomInset;
            if (Build.VERSION.SDK_INT >= 30) {
                Insets systemBars = insets.getInsets(WindowInsets.Type.systemBars());
                topInset = systemBars.top;
                bottomInset = systemBars.bottom;
            } else {
                topInset = insets.getSystemWindowInsetTop();
                bottomInset = insets.getSystemWindowInsetBottom();
            }
            page.setPadding(dp(22), dp(22) + topInset, dp(22), dp(32) + bottomInset);
            return insets;
        });
        scroll.addView(page); setContentView(scroll);

        TextView brand = new TextView(this); brand.setText("AGENT BEACON"); brand.setTextColor(Color.WHITE); brand.setTextSize(14); brand.setLetterSpacing(0.16f); brand.setTypeface(Typeface.DEFAULT, Typeface.BOLD); page.addView(brand);
        TextView tagline = text(AppText.tr(MainActivity.this, "Know when your agents need you"), 14, MUTED); LinearLayout.LayoutParams taglineParams = params(-1, -2); taglineParams.topMargin = dp(6); page.addView(tagline, taglineParams);
        TextView versionLine = text(AppText.tr(MainActivity.this, "Version ") + installedVersionName(), 12, MUTED); LinearLayout.LayoutParams versionParams = params(-1, -2); versionParams.topMargin = dp(3); page.addView(versionLine, versionParams);

        circle = new BeaconCircleView(this); LinearLayout.LayoutParams circleParams = params(-1, dp(180)); circleParams.topMargin = dp(6); page.addView(circle, circleParams);
        connectionLine = text(AppText.tr(MainActivity.this, "Not connected"), 22, Color.WHITE); connectionLine.setTypeface(Typeface.DEFAULT, Typeface.BOLD); connectionLine.setGravity(Gravity.CENTER);
        page.addView(connectionLine, params(-1, -2));
        freshnessLine = text(AppText.tr(MainActivity.this, "Connect your computer to see your agents"), 13, MUTED); freshnessLine.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams freshnessParams = params(-1, -2); freshnessParams.topMargin = dp(7); page.addView(freshnessLine, freshnessParams);
        checkButton = button(AppText.tr(MainActivity.this, "Check & connect"), false);
        LinearLayout.LayoutParams checkParams = params(-1, -2); checkParams.topMargin = dp(14); page.addView(checkButton, checkParams);
        checkButton.setOnClickListener(view -> checkAndConnect());
        circle.setOnClickListener(view -> checkAndConnect());

        LinearLayout projectsCard = card(page);
        sessionsTitle = text(AppText.tr(MainActivity.this, "YOUR PROJECTS"), 12, MUTED); sessionsTitle.setLetterSpacing(0.08f); projectsCard.addView(sessionsTitle);
        TextView projectsHelp = text(AppText.tr(MainActivity.this, "Working, waiting and recently finished chats. Ready chats leave this list after 30 minutes."), 12, MUTED); projectsHelp.setPadding(0, dp(6), 0, dp(8)); projectsCard.addView(projectsHelp);
        projectsList = new LinearLayout(this); projectsList.setOrientation(LinearLayout.VERTICAL); projectsCard.addView(projectsList);
        Button history = button(AppText.tr(MainActivity.this, "Recent activity"), true); LinearLayout.LayoutParams historyParams = params(-1, -2); historyParams.topMargin = dp(12); projectsCard.addView(history, historyParams);
        history.setOnClickListener(view -> startActivity(new Intent(this, HistoryActivity.class)));
        Button widgetSettings = button(AppText.tr(MainActivity.this, "Widget settings"), true); LinearLayout.LayoutParams widgetParams = params(-1, -2); widgetParams.topMargin = dp(8); projectsCard.addView(widgetSettings, widgetParams);
        widgetSettings.setOnClickListener(view -> startActivity(new Intent(this, WideWidgetConfigActivity.class)));
        connectionToggle = button(AppText.tr(MainActivity.this, "Computer connection"), true); LinearLayout.LayoutParams connectionToggleParams = params(-1, -2); connectionToggleParams.topMargin = dp(16); page.addView(connectionToggle, connectionToggleParams);
        connectionToggle.setOnClickListener(view -> { showConnectionSettings = !showConnectionSettings; render(); });
        connectionCard = card(page);
        TextView section = text(AppText.tr(MainActivity.this, "CONNECT YOUR COMPUTER"), 12, MUTED); section.setLetterSpacing(0.08f); connectionCard.addView(section);
        pairingInput = new EditText(this); pairingInput.setSingleLine(true); pairingInput.setTextColor(Color.WHITE); pairingInput.setHintTextColor(Color.rgb(118, 123, 139)); pairingInput.setHint(AppText.tr(MainActivity.this, "Paste the pairing link from your desktop"));
        pairingInput.setTextSize(14); pairingInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI); pairingInput.setImeOptions(EditorInfo.IME_ACTION_GO); pairingInput.setSelectAllOnFocus(false);
        pairingInput.setBackgroundTintList(android.content.res.ColorStateList.valueOf(Color.rgb(94, 98, 113)));
        LinearLayout.LayoutParams inputParams = params(-1, dp(52)); inputParams.topMargin = dp(10); connectionCard.addView(pairingInput, inputParams);
        TextView pairingHelp = text(AppText.tr(MainActivity.this, "Or scan its QR code. Older private HTTP pairing links are also supported."), 12, MUTED);
        LinearLayout.LayoutParams helpParams = params(-1, -2); helpParams.topMargin = dp(8); connectionCard.addView(pairingHelp, helpParams);
        pairButton = button(AppText.tr(MainActivity.this, "Connect"), false); LinearLayout.LayoutParams pairParams = params(-1, -2); pairParams.topMargin = dp(13); connectionCard.addView(pairButton, pairParams); pairButton.setOnClickListener(v -> pair());
        messageLine = text(AppText.tr(MainActivity.this, "Your phone connects directly to the private Agent Beacon server. This app does not send prompts or responses."), 12, MUTED);
        messageLine.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        LinearLayout.LayoutParams messageParams = params(-1, -2); messageParams.topMargin = dp(8); connectionCard.addView(messageLine, messageParams);
        disconnectButton = button(AppText.tr(MainActivity.this, "Forget this server"), true); LinearLayout.LayoutParams disconnectParams = params(-1, -2); disconnectParams.topMargin = dp(8); connectionCard.addView(disconnectButton, disconnectParams); disconnectButton.setOnClickListener(v -> disconnect());
        scanButton = button(AppText.tr(MainActivity.this, "Scan pairing QR"), true); LinearLayout.LayoutParams scanParams = params(-1, -2); scanParams.topMargin = dp(8); connectionCard.addView(scanButton, scanParams); scanButton.setOnClickListener(v -> requestScan());
        pairingInput.setOnEditorActionListener((v, action, event) -> { if (action == EditorInfo.IME_ACTION_GO) { pair(); return true; } return false; });

        LinearLayout monitorCard = card(page);
        TextView monitorHeading = text(AppText.tr(MainActivity.this, "STAY CONNECTED"), 12, MUTED); monitorHeading.setLetterSpacing(0.08f); monitorCard.addView(monitorHeading);
        monitorLine = text(AppText.tr(MainActivity.this, "The app checks while open."), 14, Color.WHITE); LinearLayout.LayoutParams monitorLineParams = params(-1, -2); monitorLineParams.topMargin = dp(9); monitorCard.addView(monitorLine, monitorLineParams);
        TextView monitorHelp = text(AppText.tr(MainActivity.this, "Questions and completed requests alert with sound and vibration. Starting work and routine updates stay silent. Monitoring pauses after 30 minutes without a reply from your computer."), 12, MUTED);
        LinearLayout.LayoutParams monitorHelpParams = params(-1, -2); monitorHelpParams.topMargin = dp(6); monitorCard.addView(monitorHelp, monitorHelpParams);
        monitorButton = button(AppText.tr(MainActivity.this, "Start background monitoring"), true); LinearLayout.LayoutParams monitorButtonParams = params(-1, -2); monitorButtonParams.topMargin = dp(13); monitorCard.addView(monitorButton, monitorButtonParams); monitorButton.setOnClickListener(v -> toggleMonitoring());
        CheckBox batterySaver = new CheckBox(this);
        batterySaver.setText(AppText.tr(MainActivity.this, "Battery saver")); batterySaver.setTextColor(Color.WHITE);
        batterySaver.setChecked(UpdateSettings.batterySaver(this));
        batterySaver.setOnCheckedChangeListener((view, checked) -> { UpdateSettings.setBatterySaver(this, checked); restartTransport(); });
        LinearLayout.LayoutParams batteryParams = params(-1, -2); batteryParams.topMargin = dp(8); monitorCard.addView(batterySaver, batteryParams);
        TextView batteryHelp = text(AppText.tr(MainActivity.this, "Reduce background network activity and retries when offline. Reconnecting can take slightly longer."), 12, MUTED);
        monitorCard.addView(batteryHelp, params(-1, -2));
        Button diagnostics = button(AppText.tr(MainActivity.this, "Update diagnostics"), true);
        LinearLayout.LayoutParams diagnosticsParams = params(-1, -2); diagnosticsParams.topMargin = dp(10);
        monitorCard.addView(diagnostics, diagnosticsParams);
        diagnostics.setOnClickListener(view -> startActivity(new Intent(this, DiagnosticsActivity.class)));
        TextView lockHelp = text(AppText.tr(MainActivity.this, "Samsung LockStar can place a Beacon widget on your lock screen. Keep background monitoring on. The Live Updates switch is not needed for that widget."), 12, MUTED);
        LinearLayout.LayoutParams lockHelpParams = params(-1, -2); lockHelpParams.topMargin = dp(10); monitorCard.addView(lockHelp, lockHelpParams);
        if (StatusNotifier.supportsPromotedOngoing()) {
            CheckBox promotedOptIn = new CheckBox(this);
            promotedOptIn.setText(AppText.tr(MainActivity.this, "Request Android Live Updates (experimental)"));
            promotedOptIn.setTextColor(MUTED);
            promotedOptIn.setButtonTintList(android.content.res.ColorStateList.valueOf(Color.rgb(139, 218, 195)));
            promotedOptIn.setChecked(getSharedPreferences("beacon_runtime", MODE_PRIVATE).getBoolean("promoted_opt_in", false));
            promotedOptIn.setOnCheckedChangeListener((buttonView, checked) -> getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("promoted_opt_in", checked).apply());
            LinearLayout.LayoutParams promotedParams = params(-1, -2); promotedParams.topMargin = dp(8); monitorCard.addView(promotedOptIn, promotedParams);
            TextView promotedHelp = text(AppText.tr(MainActivity.this, "Requests an enhanced Android notification. Your phone decides whether to show it. This is separate from a LockStar widget."), 12, MUTED);
            LinearLayout.LayoutParams promotedHelpParams = params(-1, -2); promotedHelpParams.topMargin = dp(2); monitorCard.addView(promotedHelp, promotedHelpParams);
        }

        Button language = button(AppText.tr(this, "App language") + " · " + ("en".equals(AppLanguage.language(this)) ? "English" : "Українська"), true);
        LinearLayout.LayoutParams languageParams = params(-1, -2); languageParams.topMargin = dp(20);
        page.addView(language, languageParams); language.setOnClickListener(view -> chooseLanguage());
        page.addView(text(AppText.tr(this, "English for an English phone; Ukrainian for all other languages. Widgets keep short English labels."), 12, MUTED));
        render();
    }

    private void chooseLanguage() {
        if (pairingInProgress) { setMessage(AppText.tr(this, "Wait until the connection attempt finishes before changing the language.")); return; }
        String[] modes = {"auto", "uk", "en"};
        String[] labels = {AppText.tr(this, "Automatic (phone language)"), "Українська", "English"};
        int selected = java.util.Arrays.asList(modes).indexOf(AppLanguage.mode(this));
        new android.app.AlertDialog.Builder(this).setTitle(AppText.tr(this, "App language"))
                .setSingleChoiceItems(labels, Math.max(0, selected), (dialog, which) -> {
                    if (!AppLanguage.save(this, modes[which])) { setMessage(AppText.tr(this, "Could not save the language. Try again.")); return; }
                    dialog.dismiss(); StatusNotifier.languageChanged(this); recreate();
                }).setNegativeButton(AppText.tr(this, "Cancel"), null).show();
    }

    private void pair() {
        if (pairingInProgress) return;
        final String raw = pairingInput.getText().toString();
        final PairingInput parsed;
        try { parsed = PairingInput.parse(raw); }
        catch (Exception error) { setMessage(AppText.tr(MainActivity.this, "That is not a valid Agent Beacon pairing link. Check the link and try again.")); return; }
        final long attemptGeneration = PAIR_GENERATION.incrementAndGet();
        pairAttemptGeneration = attemptGeneration;
        pairingInProgress = true;
        if (activeRequest != null) activeRequest.cancel();
        delivery.cancel(); foregroundPollGate.invalidate();
        setBusy(true); setMessage((parsed.legacy ? AppText.tr(MainActivity.this, "Saving connection to ") : AppText.tr(MainActivity.this, "Connecting to ")) + parsed.host + ":" + parsed.port + "…");
        hideKeyboard();
        worker.execute(() -> {
            try {
                String model = Build.MODEL == null ? "" : Build.MODEL.replaceAll("[^A-Za-z0-9 _.-]", "").trim();
                if (model.isEmpty()) model = "Android phone";
                if (model.length() > 48) model = model.substring(0, 48);
                Credentials credentials = BeaconClient.pair(parsed, model);
                runOnUiThread(() -> {
                    if (!isPairAttemptCurrent(attemptGeneration)) return;
                    try {
                        // Serialize credential replacement with StatusService's main-thread completion path.
                        ConnectionStore.save(MainActivity.this, credentials);
                        if (!ConnectionStore.matches(MainActivity.this, credentials)) {
                            throw new IllegalStateException("Encrypted credentials could not be verified after saving.");
                        }
                    } catch (Exception saveError) {
                        pairingInProgress = false;
                        setBusy(false);
                        setMessage(AppText.tr(MainActivity.this, "Android could not save and verify the encrypted connection. Check device storage and try pairing again."));
                        return;
                    }
                    try { SnapshotCache.clear(MainActivity.this); }
                    catch (Exception error) { Log.w("AgentBeacon", "Could not clear the old cached status (" + error.getClass().getSimpleName() + ")"); }
                    try { StatusNotifier.cancelStatus(MainActivity.this); AttentionNotifier.cancelAll(MainActivity.this); }
                    catch (Exception error) { Log.w("AgentBeacon", "Could not clear the old status notification (" + error.getClass().getSimpleName() + ")"); }
                    try { BeaconWidget.refresh(MainActivity.this); }
                    catch (Exception error) { Log.w("AgentBeacon", "Could not refresh the home-screen widget (" + error.getClass().getSimpleName() + ")"); }
                    pairingInProgress = false;
                    latest = null; pairingInput.setText(""); setBusy(false);
                    setMessage(parsed.legacy ? AppText.tr(MainActivity.this, "Connected with a legacy process token. It stops working when the server restarts.") : AppText.tr(MainActivity.this, "Paired. Your device credential is encrypted with Android Keystore."));
                    pollOnce(); render();
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (!isPairAttemptCurrent(attemptGeneration)) return;
                    pairingInProgress = false; setBusy(false); setMessage(pairError(error, parsed.host, parsed.port));
                });
            }
        });
    }

    private boolean isPairAttemptCurrent(long attemptGeneration) {
        return PAIR_GENERATION.get() == attemptGeneration && pairAttemptGeneration == attemptGeneration
                && !isFinishing() && (Build.VERSION.SDK_INT < 17 || !isDestroyed());
    }

    private void disconnect() {
        foregroundPollGate.invalidate(); delivery.cancel();
        if (activeRequest != null) activeRequest.cancel();
        PAIR_GENERATION.incrementAndGet();
        pairingInProgress = false;
        pairAttemptGeneration = 0L;
        setBusy(false);
        ConnectionStore.clear(this);
        SnapshotCache.clear(this);
        getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", false).apply();
        Intent stop = new Intent(this, StatusService.class).setAction(StatusService.ACTION_STOP);
        try { startService(stop); }
        catch (Exception error) { Log.w("AgentBeacon", "Could not stop the background monitor (" + error.getClass().getSimpleName() + ")"); }
        StatusNotifier.cancelStatus(this);
        AttentionNotifier.cancelAll(this);
        latest = null;
        BeaconWidget.refresh(this);
        setMessage(AppText.tr(MainActivity.this, "Disconnected. Pair a server to see task status."));
        render();
    }

    private void requestScan() {
        if (scannerPending || scannerInProgress) return;
        if (Build.VERSION.SDK_INT >= 23 && checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            scannerPending = true;
            setScanButtonEnabled(false);
            try { requestPermissions(new String[]{Manifest.permission.CAMERA}, REQUEST_CAMERA); }
            catch (RuntimeException error) {
                scannerPending = false;
                setScanButtonEnabled(true);
                recordScannerFailure(scannerDiagnostic(error));
            }
            return;
        }
        launchScanner();
    }

    private void launchScanner() {
        if (scannerInProgress) return;
        scannerPending = false;
        scannerInProgress = true;
        setScanButtonEnabled(false);
        getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().remove("last_scanner_failure").apply();
        try {
            IntentIntegrator integrator = new IntentIntegrator(this);
            integrator.setCaptureActivity(BeaconCaptureActivity.class);
            integrator.setDesiredBarcodeFormats(IntentIntegrator.QR_CODE);
            integrator.setPrompt(AppText.tr(MainActivity.this, "Scan an Agent Beacon pairing QR code"));
            integrator.setBeepEnabled(false);
            integrator.setOrientationLocked(false);
            integrator.initiateScan();
        } catch (RuntimeException error) {
            // A missing/disabled camera or an unavailable scanner activity should not close the app.
            scannerInProgress = false;
            setScanButtonEnabled(true);
            recordScannerFailure(scannerDiagnostic(error));
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == IntentIntegrator.REQUEST_CODE) {
            scannerPending = false;
            scannerInProgress = false;
            setScanButtonEnabled(true);
        }
        if (data != null && data.hasExtra(EXTRA_SCANNER_FAILURE)) {
            recordScannerFailure(data.getStringExtra(EXTRA_SCANNER_FAILURE));
            return;
        }
        IntentResult result = IntentIntegrator.parseActivityResult(requestCode, resultCode, data);
        if (result == null) return;
        String contents = result.getContents();
        if (contents == null) { setMessage(AppText.tr(MainActivity.this, "QR scan cancelled. You can paste a pairing link instead.")); return; }
        try {
            PairingInput.parse(contents);
            pairingInput.setText(contents);
            setMessage(AppText.tr(MainActivity.this, "Pairing link scanned. Tap Connect to review and pair this phone."));
        } catch (Exception error) {
            setMessage(AppText.tr(MainActivity.this, "That QR code is not a supported private Agent Beacon pairing link. You can paste a valid link instead."));
        }
    }

    private String pairError(Exception error, String host, int port) {
        if (error instanceof BeaconClient.HttpFailure) {
            int status = ((BeaconClient.HttpFailure) error).statusCode;
            if (status == 401 || status == 403 || status == 409) return AppText.tr(MainActivity.this, "Pairing link was rejected or has expired. Create a fresh link and try again.");
            if (status == 404) return AppText.tr(MainActivity.this, "This server does not provide the phone pairing route yet.");
            return AppText.tr(MainActivity.this, "The server returned HTTP ") + status + AppText.tr(MainActivity.this, ". Check the server and pairing link.");
        }
        if (error instanceof java.net.SocketTimeoutException) return AppText.tr(MainActivity.this, "The server at ") + host + ":" + port + AppText.tr(MainActivity.this, " timed out. Check Wi-Fi and try again.");
        if (error instanceof java.net.ConnectException || error instanceof java.net.NoRouteToHostException
                || error instanceof java.net.UnknownHostException || error instanceof java.net.SocketException) {
            return AppText.tr(MainActivity.this, "Could not connect to ") + host + ":" + port + AppText.tr(MainActivity.this, ". Check Wi-Fi and the server, then try again.");
        }
        if (error instanceof MiniJson.JsonException) return AppText.tr(MainActivity.this, "The server at ") + host + ":" + port + AppText.tr(MainActivity.this, " returned an invalid pairing response. Create a fresh link and try again.");
        return AppText.tr(MainActivity.this, "Pairing with ") + host + ":" + port + AppText.tr(MainActivity.this, " failed. Check the server and pairing link, then try again.");
    }

    private String installedVersionName() {
        try {
            String versionName = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
            return versionName == null || versionName.isEmpty() ? AppText.tr(MainActivity.this, "Unknown") : versionName;
        } catch (PackageManager.NameNotFoundException error) {
            Log.w("AgentBeacon", "Could not read installed app version");
            return AppText.tr(MainActivity.this, "Unknown");
        }
    }

    private void pollOnce() {
        if (pairingInProgress || StatusService.isRunning()) return;
        Credentials credentials = ConnectionStore.load(this);
        if (credentials == null) { latest = null; render(); return; }
        final int pollGeneration = foregroundPollGate.current();
        if (!foregroundPollGate.begin(pollGeneration)) return;
        final BeaconClient.LiveRequest request = new BeaconClient.LiveRequest();
        activeRequest = request;
        final boolean saver = UpdateSettings.batterySaver(this);
        final boolean discover = discoveryPending;
        discoveryPending = false;
        worker.execute(() -> {
            try {
                if (discover) {
                    Credentials found = ConnectionRecovery.find(this, credentials, request, saver);
                    if (found != null) { runOnUiThread(() -> applyRecovery(pollGeneration, credentials, found)); return; }
                }
                if (request.cancelled()) throw new java.io.InterruptedIOException("Cancelled");
                boolean streamed = BeaconClient.updates(credentials, saver, request, (response, transport, responseMs) -> runOnUiThread(() -> {
                    if (!foregroundCurrent(pollGeneration, credentials)) return;
                    pollFailures = 0;
                    UpdateDiagnostics.received(response, transport, responseMs);
                    AttentionNotifier.received(this, response, credentials);
                    delivery.receive(this, response, credentials, () -> foregroundCurrent(pollGeneration, credentials), () -> {
                        latest = SnapshotCache.load(this); render(); BeaconWidget.refresh(this);
                    });
                }));
                runOnUiThread(() -> {
                    boolean current = foregroundPollGate.complete(pollGeneration);
                    if (!resumed || StatusService.isRunning()) return;
                    long delay = !current ? 0L : streamed ? UpdateSettings.reconnectDelay(saver)
                            : PollSchedule.delayMs(latest, SystemClock.elapsedRealtime(), true, 0);
                    handler.removeCallbacks(pollForeground); handler.postDelayed(pollForeground, delay);
                });
            } catch (Exception error) {
                Credentials found = ConnectionRecovery.find(this, credentials, request, saver);
                if (found != null) { runOnUiThread(() -> applyRecovery(pollGeneration, credentials, found)); return; }
                runOnUiThread(() -> {
                    boolean current = foregroundPollGate.complete(pollGeneration);
                    if (!resumed || StatusService.isRunning()) return;
                    if (current && ConnectionStore.matches(this, credentials)) {
                        pollFailures = Math.min(6, pollFailures + 1); delivery.cancel();
                        UpdateDiagnostics.failed(error);
                        Log.w("AgentBeacon", "Foreground live connection interrupted (" + error.getClass().getSimpleName() + ")");
                        latest = SnapshotCache.load(this); render();
                    }
                    handler.removeCallbacks(pollForeground);
                    handler.postDelayed(pollForeground, current ? UpdateSettings.retryDelay(pollFailures, saver) : 0L);
                });
            }
        });
    }

    private boolean foregroundCurrent(int generation, Credentials credentials) {
        return resumed && generation == foregroundPollGate.current() && !StatusService.isRunning()
                && !isFinishing() && !isDestroyed() && ConnectionStore.matches(this, credentials);
    }

    private void applyRecovery(int generation, Credentials previous, Credentials verified) {
        boolean current = foregroundPollGate.complete(generation);
        if (!resumed || StatusService.isRunning() || isFinishing() || isDestroyed()) return;
        if (current && ConnectionStore.matches(this, previous)) {
            try {
                if (ConnectionStore.relocate(this, previous, verified)) {
                    delivery.cancel(); pollFailures = 0; latest = null;
                    setMessage(AppText.tr(MainActivity.this, "Computer found automatically. Your existing pairing is retained."));
                    render(); BeaconWidget.refresh(this);
                }
            } catch (Exception error) {
                setMessage(AppText.tr(MainActivity.this, "Could not save the recovered address. Your existing pairing is retained."));
                Log.w("AgentBeacon", "Could not save recovered connection (" + error.getClass().getSimpleName() + ")");
                handler.postDelayed(pollForeground, 5000L); return;
            }
        }
        handler.removeCallbacks(pollForeground); handler.post(pollForeground);
    }

    private void restartTransport() {
        discoveryPending = true;
        foregroundPollGate.invalidate(); delivery.cancel();
        if (activeRequest != null) activeRequest.cancel();
        pollFailures = 0;
        if (StatusService.isRunning()) {
            try { startService(new Intent(this, StatusService.class).setAction("com.agentbeacon.android.action.SETTINGS")); }
            catch (RuntimeException error) { setMessage(AppText.tr(MainActivity.this, "Could not refresh monitoring settings. Stop and start monitoring to apply them.")); }
        } else if (resumed) { handler.removeCallbacks(pollForeground); handler.post(pollForeground); }
    }

    private void checkAndConnect() {
        if (pairingInProgress) return;
        if (ConnectionStore.load(this) == null) {
            showConnectionSettings = true; render(); setMessage(AppText.tr(MainActivity.this, "Pair your computer once using its QR. Later, this button finds it automatically.")); return;
        }
        ConnectionRecovery.reset(); discoveryPending = true; pollFailures = 0;
        manualCheckStarted = SystemClock.elapsedRealtime();
        foregroundPollGate.invalidate(); delivery.cancel(); if (activeRequest != null) activeRequest.cancel();
        StatusNotifier.ensureChannels(this);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            pendingConnectPermission = true; requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS); return;
        }
        if (!StatusNotifier.enabled(this)) {
            setMessage(AppText.tr(MainActivity.this, "Checking while open. Allow Agent Beacon notifications to resume background monitoring."));
            restartTransport(); return;
        }
        try {
            startForegroundService(new Intent(this, StatusService.class).setAction(StatusService.ACTION_CHECK));
            setMessage(AppText.tr(MainActivity.this, "Looking for your paired computer… No new QR is needed."));
        } catch (RuntimeException error) {
            Log.w("AgentBeacon", "Could not resume background monitoring (" + error.getClass().getSimpleName() + ")");
            setMessage(AppText.tr(MainActivity.this, "Checking while open. Background monitoring could not start.")); restartTransport();
        }
        render();
    }

    private void recoverMonitoring() {
        if (StatusService.isRunning()) {
            Snapshot cached = SnapshotCache.load(this);
            if (cached == null || !cached.isFresh(SystemClock.elapsedRealtime())) {
                try { startService(new Intent(this, StatusService.class).setAction("com.agentbeacon.android.action.REFRESH")); }
                catch (RuntimeException error) { Log.w("AgentBeacon", "Could not request a fresh connection (" + error.getClass().getSimpleName() + ")"); }
            }
            return;
        }
        boolean requested = getSharedPreferences("beacon_runtime", MODE_PRIVATE).getBoolean("monitor_enabled", false);
        if (!requested) return;
        StatusNotifier.ensureChannels(this);
        if (ConnectionStore.load(this) == null || !StatusNotifier.enabled(this)) {
            getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", false).apply();
            return;
        }
        try { startForegroundService(new Intent(this, StatusService.class).setAction("com.agentbeacon.android.action.RECOVER")); }
        catch (RuntimeException error) {
            getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", false).apply();
            setMessage(AppText.tr(MainActivity.this, "Background monitoring could not resume. Tap Start background monitoring to try again."));
        }
    }

    private void toggleMonitoring() {
        if (isMonitoring()) {
            Intent stop = new Intent(this, StatusService.class).setAction(StatusService.ACTION_STOP); startService(stop);
            getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", false).apply();
            BeaconWidget.refresh(this); render(); return;
        }
        if (ConnectionStore.load(this) == null) { setMessage(AppText.tr(MainActivity.this, "Pair a server before starting background monitoring.")); return; }
        StatusNotifier.ensureChannels(this);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS); return;
        }
        if (!StatusNotifier.enabled(this)) {
            setMessage(AppText.tr(MainActivity.this, "Enable both Agent Beacon notification channels in Android settings to keep background monitoring visible."));
            try { startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName())); }
            catch (Exception error) { Log.w("AgentBeacon", "Could not open notification settings (" + error.getClass().getSimpleName() + ")"); }
            return;
        }
        Intent start = new Intent(this, StatusService.class).setAction(StatusService.ACTION_START);
        try {
            foregroundPollGate.invalidate(); delivery.cancel();
            if (activeRequest != null) activeRequest.cancel();
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(start); else startService(start);
            getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", true).apply(); render();
        } catch (Exception error) {
            getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putBoolean("monitor_enabled", false).apply();
            setMessage(AppText.tr(MainActivity.this, "Android could not start the monitor. Keep notifications enabled and try again.")); render();
        }
    }

    private boolean isMonitoring() { return StatusService.isRunning(); }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQUEST_CAMERA) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                if (scannerPending) launchScanner();
            } else {
                scannerPending = false;
                setScanButtonEnabled(true);
                setMessage(AppText.tr(MainActivity.this, "Camera permission was declined. Paste the pairing link instead."));
            }
            return;
        }
        if (requestCode == REQUEST_NOTIFICATIONS) {
            boolean reconnect = pendingConnectPermission; pendingConnectPermission = false;
            if (StatusNotifier.enabled(this)) { if (reconnect) checkAndConnect(); else toggleMonitoring(); }
            else { setMessage(AppText.tr(MainActivity.this, "Background monitoring stays off until notifications are allowed. Foreground checks continue while this app is open.")); render(); }
        }
    }

    private void render() {
        if (circle == null) return;
        Credentials credentials = ConnectionStore.load(this);
        latest = SnapshotCache.load(this);
        long now = SystemClock.elapsedRealtime();
        boolean reminderOnly = credentials != null && latest != null && latest.isQuestionReminderOnly(now);
        StatusPresentation presentation = StatusPresentation.select(this, latest, credentials != null && (resumed || StatusService.isRunning()), now);
        String state = credentials == null ? "unknown" : presentation.state;
        boolean checking = manualCheckStarted > 0 && now - manualCheckStarted < 15000L
                && (latest == null || latest.receivedElapsedMs < manualCheckStarted);
        circle.setState(state);
        circle.setClickable(!checking && ("unknown".equals(state) || !StatusService.isRunning()));
        circle.setContentDescription("unknown".equals(state) || !StatusService.isRunning() ? AppText.tr(MainActivity.this, "Check and reconnect to your computer") : AppText.state(this, state));
        if (checkButton != null) { checkButton.setEnabled(!pairingInProgress && !checking); checkButton.setText(checking ? AppText.tr(MainActivity.this, "Checking…") : AppText.tr(MainActivity.this, "Check & connect")); }
        connectionLine.setText(credentials == null ? AppText.tr(MainActivity.this, "Not connected") : presentation.reconnecting ? AppText.state(this, state) + AppText.tr(MainActivity.this, " · reconnecting")
                : (reminderOnly ? AppText.tr(MainActivity.this, "Question reminder") : AppText.state(this, state)));
        connectionLine.setTextColor(stateColor(state));
        if (credentials == null) freshnessLine.setText(AppText.tr(MainActivity.this, "Connect your computer to see your agents"));
        else if (checking) freshnessLine.setText(AppText.tr(MainActivity.this, "Looking for your paired computer…"));
        else if (presentation.reconnecting) freshnessLine.setText(AppText.tr(MainActivity.this, "Last known state · reconnecting to your server…"));
        else if (latest == null) freshnessLine.setText(AppText.tr(MainActivity.this, "Waiting for your computer to send an update"));
        else if (!latest.available && now >= latest.receivedElapsedMs && now - latest.receivedElapsedMs < 15000L) {
            StringBuilder missing = new StringBuilder();
            for (SessionOverview.Chat chat : SessionOverview.chats(latest, now)) if ("unknown".equals(chat.state)) {
                if (missing.length() > 0) missing.append("; ");
                missing.append(SessionOverview.providerName(chat.source)).append(" · ").append(chat.title);
            }
            freshnessLine.setText(missing.length() > 0 ? AppText.tr(MainActivity.this, "Needs a confirmed update: ") + missing : AppText.tr(MainActivity.this, "Waiting for agent activity"));
        }
        else if (!latest.isFresh(now)) freshnessLine.setText(AppText.tr(MainActivity.this, "No recent update from your computer"));
        else if (reminderOnly) freshnessLine.setText(AppText.reminder(this, latest.questionReminderRemainingMs(now)));
        else freshnessLine.setText(AppText.tr(MainActivity.this, "Your computer is connected · ") + AppText.chatCount(this, SessionOverview.activeCount(latest)));
        renderProjects(credentials != null, now);
        connectionCard.setVisibility(credentials == null || showConnectionSettings || pairingInProgress ? View.VISIBLE : View.GONE);
        connectionToggle.setText(showConnectionSettings ? AppText.tr(MainActivity.this, "Hide computer connection") : AppText.tr(MainActivity.this, "Computer connection"));
        boolean running = isMonitoring();
        disconnectButton.setEnabled(credentials != null || pairingInProgress);
        monitorButton.setText(running ? AppText.tr(MainActivity.this, "Stop background monitoring") : AppText.tr(MainActivity.this, "Start background monitoring"));
        monitorLine.setText(running ? AppText.tr(MainActivity.this, "Your phone reconnects automatically. After 30 minutes offline, monitoring pauses to save battery.")
                : MonitorState.offlinePaused(this) ? AppText.tr(MainActivity.this, "Paused after 30 minutes without your computer. Tap Check & connect to resume.")
                : (StatusNotifier.enabled(this) ? AppText.tr(MainActivity.this, "The app checks while open. Tap Check & connect for background updates.") : AppText.tr(MainActivity.this, "Notifications are disabled. Foreground checks work while this app is open.")));
    }

    private void renderProjects(boolean connected, long now) {
        java.util.List<SessionOverview.Chat> chats = SessionOverview.chats(latest, now);
        StringBuilder key = new StringBuilder(connected ? "connected" : "offline");
        for (SessionOverview.Chat chat : chats) key.append(chat.source).append(chat.id).append(chat.title).append(chat.project).append(chat.state).append(chat.detail);
        if (key.toString().equals(projectsKey)) return;
        projectsKey = key.toString(); projectsList.removeAllViews();
        sessionsTitle.setText(AppText.tr(MainActivity.this, "CURRENT CHATS") + (chats.isEmpty() ? "" : " · " + chats.size()));
        if (chats.isEmpty()) {
            projectsList.addView(text(connected ? AppText.tr(MainActivity.this, "No current chats. Finished chats leave this overview after 30 minutes and return when used again.") : AppText.tr(MainActivity.this, "Connect your computer to see your agents and chats."), 14, MUTED));
        }
        for (int pass = 0; pass < 2; pass++) {
            if (pass == 1 && chats.stream().anyMatch(chat -> "unknown".equals(chat.state))) {
                TextView warning = text(AppText.tr(MainActivity.this, "CONNECTION WARNINGS"), 12, MUTED); warning.setPadding(0, dp(14), 0, dp(5)); projectsList.addView(warning);
            }
            for (SessionOverview.Chat chat : chats) {
                if ("unknown".equals(chat.state) != (pass == 1)) continue;
                LinearLayout item = new LinearLayout(this); item.setOrientation(LinearLayout.VERTICAL); item.setPadding(0, dp(10), 0, dp(12));
                TextView title = text(chat.untitled ? AppText.tr(this, "Untitled chat") : chat.title, 18, Color.WHITE); title.setTypeface(Typeface.DEFAULT, Typeface.BOLD); item.addView(title);
                item.addView(text((chat.projectMissing ? AppText.tr(this, "Project not provided") : chat.project) + " · " + SessionOverview.providerName(chat.source), 12, MUTED));
                item.addView(text("unknown".equals(chat.state) ? AppText.tr(MainActivity.this, "Connection needs attention") : AppText.state(this, chat.state), 14, stateColor(chat.state)));
                if (!chat.detail.isEmpty()) item.addView(text(AppText.tr(this, chat.detail), 12, MUTED));
                projectsList.addView(item);
            }
        }
        SessionOverview.Project claude = SessionOverview.provider(latest, "claude", now);
        if (connected && claude.count == 0 && !"ready".equals(claude.state)) {
            TextView help = text(AppText.tr(MainActivity.this, "Claude Code: open a new Local chat in the Claude app to load Beacon hooks."), 12, MUTED); help.setPadding(0, dp(8), 0, 0); projectsList.addView(help);
        }
    }

    private void setBusy(boolean busy) {
        if (checkButton != null) checkButton.setEnabled(!busy);
        pairButton.setEnabled(!busy);
        pairButton.setText(busy ? AppText.tr(MainActivity.this, "Connecting…") : AppText.tr(MainActivity.this, "Connect"));
        if (disconnectButton != null) disconnectButton.setEnabled(busy || ConnectionStore.isConnected(this));
    }
    private void setMessage(String text) { if (messageLine != null) messageLine.setText(text); }
    private void setScanButtonEnabled(boolean enabled) {
        if (scanButton == null) return;
        scanButton.setEnabled(enabled);
        scanButton.setText(enabled ? AppText.tr(MainActivity.this, "Scan pairing QR") : (scannerPending ? AppText.tr(MainActivity.this, "Requesting camera access…") : AppText.tr(MainActivity.this, "Opening camera…")));
    }
    private void recordScannerFailure(String diagnostic) {
        String safeDiagnostic = diagnostic == null ? "ScannerFailure" : diagnostic.substring(0, Math.min(160, diagnostic.length()));
        getSharedPreferences("beacon_runtime", MODE_PRIVATE).edit().putString("last_scanner_failure", safeDiagnostic).apply();
        Log.w("AgentBeacon", "QR scanner failed: " + safeDiagnostic);
        setMessage(AppText.tr(MainActivity.this, "QR scanner error: ") + safeDiagnostic + AppText.tr(MainActivity.this, ". Paste the pairing link instead."));
    }
    private static String scannerDiagnostic(RuntimeException error) {
        String type = error.getClass().getSimpleName();
        StackTraceElement[] frames = error.getStackTrace();
        if (frames.length == 0) return type;
        StackTraceElement frame = frames[0];
        String className = frame.getClassName();
        int separator = className.lastIndexOf('.');
        String simpleClass = separator < 0 ? className : className.substring(separator + 1);
        return type + " at " + simpleClass + "." + frame.getMethodName() + ":" + frame.getLineNumber();
    }
    private void hideKeyboard() {
        try { ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(pairingInput.getWindowToken(), 0); }
        catch (Exception error) { Log.w("AgentBeacon", "Could not hide the keyboard (" + error.getClass().getSimpleName() + ")"); }
    }
    private LinearLayout card(LinearLayout parent) {
        LinearLayout card = new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL); card.setPadding(dp(16), dp(16), dp(16), dp(16)); card.setBackground(UiStyle.shape(this, PANEL, 0xFF2C2C2C));
        LinearLayout.LayoutParams p = params(-1, -2); p.topMargin = dp(20); parent.addView(card, p); return card;
    }
    private Button button(String label, boolean secondary) {
        return UiStyle.button(this, label, secondary);
    }
    private TextView text(String value, int size, int color) { TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color); return view; }
    private LinearLayout.LayoutParams params(int width, int height) { return new LinearLayout.LayoutParams(width, height); }
    private int dp(int px) { return Math.round(px * getResources().getDisplayMetrics().density); }
    private boolean isResumed() { return resumed; }
    private boolean resumed;

    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    @Override protected void onResume() {
        super.onResume(); resumed = true; visible = true; discoveryPending = true; pollFailures = 0; latest = SnapshotCache.load(this); render(); handler.removeCallbacks(refreshUi); handler.post(refreshUi); handler.removeCallbacks(pollForeground); handler.post(pollForeground);
        if (!receiverRegistered) {
            IntentFilter filter = new IntentFilter(StatusService.ACTION_SNAPSHOT);
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(snapshotReceiver, filter, Context.RECEIVER_NOT_EXPORTED); else registerReceiver(snapshotReceiver, filter);
            receiverRegistered = true;
        }
        recoverMonitoring();
        if (getIntent().getBooleanExtra(EXTRA_RECONNECT, false)) { getIntent().removeExtra(EXTRA_RECONNECT); handler.post(this::checkAndConnect); }
        networkReconnect = new NetworkReconnect(this, () -> {
            if (resumed && !StatusService.isRunning()) restartTransport();
        });
        if (scannerPending && Build.VERSION.SDK_INT >= 23
                && checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) launchScanner();
    }
    @Override protected void onPause() { if (networkReconnect != null) { networkReconnect.close(); networkReconnect = null; } resumed = false; visible = false; foregroundPollGate.invalidate(); delivery.cancel(); if (activeRequest != null) activeRequest.cancel(); handler.removeCallbacks(refreshUi); handler.removeCallbacks(pollForeground); if (receiverRegistered) { unregisterReceiver(snapshotReceiver); receiverRegistered = false; } super.onPause(); }
    @Override protected void onSaveInstanceState(Bundle state) { state.putBoolean("scanner_pending", scannerPending); state.putBoolean("show_connection", showConnectionSettings); super.onSaveInstanceState(state); }
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent); setIntent(intent);
        if (resumed && intent.getBooleanExtra(EXTRA_RECONNECT, false)) { intent.removeExtra(EXTRA_RECONNECT); handler.post(this::checkAndConnect); }
        if (intent.getDataString() != null && pairingInput != null) {
            PAIR_GENERATION.incrementAndGet();
            pairAttemptGeneration = 0L;
            pairingInProgress = false;
            setBusy(false);
            pairingInput.setText(intent.getDataString()); showConnectionSettings = true; render();
        }
    }
    @Override protected void onDestroy() {
        if (pairAttemptGeneration != 0L) PAIR_GENERATION.compareAndSet(pairAttemptGeneration, pairAttemptGeneration + 1L);
        foregroundPollGate.invalidate(); delivery.cancel(); if (activeRequest != null) activeRequest.cancel(); worker.shutdownNow(); super.onDestroy();
    }

    static String stateLabel(String state) {
        if ("working".equals(state)) return "Working";
        if ("awaiting_user".equals(state)) return "Waiting for you";
        if ("ready".equals(state)) return "Ready for next task";
        return "Waiting for update";
    }
    static String reminderDescription(long remainingMs) {
        long totalSeconds = (Math.max(0, remainingMs) + 999L) / 1000L;
        long minutes = totalSeconds / 60L;
        long seconds = totalSeconds % 60L;
        return "Agent is working · previous request reminder · remaining "
                + String.format(java.util.Locale.ROOT, "%02d:%02d", minutes, seconds);
    }
    static int stateColor(String state) {
        if ("working".equals(state)) return Color.rgb(240, 77, 103);
        if ("awaiting_user".equals(state)) return Color.rgb(231, 173, 57);
        if ("ready".equals(state)) return Color.rgb(62, 201, 136);
        return Color.rgb(115, 120, 137);
    }
}
