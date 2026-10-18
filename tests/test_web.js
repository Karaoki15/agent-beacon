const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const crypto = require("node:crypto");

const source = fs.readFileSync(path.join(__dirname, "../agent_beacon/web/app.js"), "utf8");
const qrSource = fs.readFileSync(path.join(__dirname, "../agent_beacon/web/vendor/qr.js"), "utf8");

function makeHarness(fetch, hash = "", { clipboard = null, secure = false } = {}) {
  let now = 0;
  let nextTimer = 1;
  const timers = new Map();
  const elements = new Map();
  const documentListeners = new Map();
  const storage = new Map();
  const historyCalls = [];

  class Element {
    constructor(id = "") {
      this.id = id;
      this.dataset = {};
      this.hidden = false;
      this.disabled = false;
      this.value = "";
      this.textContent = "";
      this.children = [];
      this.listeners = new Map();
      this.style = {};
      this.lastElementChild = new ElementChild();
      this.drawCalls = [];
    }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    dispatch(name, event = {}) { this.listeners.get(name)?.({ ...event, key: event.key }); }
    querySelector() { return new Element(); }
    setAttribute() {}
    replaceChildren(...children) { this.children = children; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.children.push(child); }
    getContext(kind) {
      if (kind !== "2d") return null;
      return { fillStyle: "", fillRect: (...args) => this.drawCalls.push(args) };
    }
    remove() {}
    focus() {}
    select() {}
  }
  class ElementChild { set textContent(value) { this.text = value; } get textContent() { return this.text || ""; } }

  const document = {
    visibilityState: "visible",
    body: new Element("body"),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element(id));
      return elements.get(id);
    },
    createElement: () => new Element(),
    execCommand: () => false,
    addEventListener(name, callback) { documentListeners.set(name, callback); },
  };
  const window = {
    location: { hash, pathname: "/", search: "", port: "8765" },
    history: { replaceState(...args) { historyCalls.push(args); } },
    sessionStorage: {
      getItem(key) { return storage.get(key) ?? null; },
      setItem(key, value) { storage.set(key, value); },
      removeItem(key) { storage.delete(key); },
    },
    isSecureContext: secure,
    setTimeout(callback, delay = 0) {
      const id = nextTimer++;
      timers.set(id, { callback, at: now + delay, interval: 0 });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    setInterval(callback, delay) {
      const id = nextTimer++;
      timers.set(id, { callback, at: now + delay, interval: delay });
      return id;
    },
    clearInterval(id) { timers.delete(id); },
  };
  const context = vm.createContext({
    window, document, navigator: clipboard ? { clipboard } : {}, fetch, AbortController, URLSearchParams,
    performance: { now: () => now },
    setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
    Date, Number, Math, JSON, Set, String, Boolean, Array, Object, Error, encodeURIComponent,
  });
  vm.runInContext(qrSource, context);
  vm.runInContext(source, context);

  async function flush() {
    for (let index = 0; index < 8; index += 1) await Promise.resolve();
  }
  async function advance(milliseconds) {
    const end = now + milliseconds;
    while (true) {
      const due = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [id, timer] = due;
      now = timer.at;
      if (timer.interval) timer.at += timer.interval;
      else timers.delete(id);
      timer.callback();
      await flush();
    }
    now = end;
    await flush();
  }
  return {
    document, window, elements, storage, historyCalls, advance, flush,
    hide() { document.visibilityState = "hidden"; documentListeners.get("visibilitychange")?.(); },
    show() { document.visibilityState = "visible"; documentListeners.get("visibilitychange")?.(); },
  };
}

function response(status, payload) {
  return { status, ok: status >= 200 && status < 300, json: async () => payload };
}

function freshSnapshot(overrides = {}) {
  return {
    schema_version: 1, instance_id: "instance-a", revision: 1, ttl_seconds: 30,
    valid_for_seconds: 20, available: true, state: "ready", reason: null, partial: false,
    sessions: [{ agent_id: "demo-agent", session_id: "demo-session", source: "manual", sequence: 1, state: "ready", available: true, updated_at: "2026-10-16T00:00:00Z" }],
    server_time: "2026-10-16T00:00:00Z", ...overrides,
  };
}

test("captures and removes the token fragment, then paints a validated status", async () => {
  let authHeader = "";
  const harness = makeHarness(async (url, options = {}) => {
    if (url === "/api/control") return response(403, {});
    authHeader = options.headers.Authorization;
    return response(200, freshSnapshot());
  }, "#token=private-view-key");
  assert.equal(harness.elements.get("beaconBody").dataset.state, "unavailable");
  await harness.advance(0);
  assert.equal(harness.historyCalls.length, 1);
  assert.equal(harness.historyCalls[0][2], "/");
  assert.equal(authHeader, "Bearer private-view-key");
  assert.equal(harness.elements.get("beaconBody").dataset.state, "ready");
  assert.equal(harness.elements.get("stateTitle").textContent, "Готовий до завдання");
  assert.equal(harness.elements.get("stateOverline").textContent, "ГОТОВИЙ ДО НАСТУПНОГО ЗАВДАННЯ");
  assert.equal(harness.elements.get("liveChip").lastElementChild.textContent, "ГОТОВИЙ");
  assert.equal(harness.storage.get("agent-beacon.viewer-token"), "private-view-key");
});

test("keeps the pairing instructions visible when a tokenless tab is hidden and restored", async () => {
  let statusCalls = 0;
  const harness = makeHarness(async (url) => {
    if (url === "/api/control") return response(403, {});
    statusCalls += 1;
    return response(200, freshSnapshot());
  });
  const expected = /посилання для перегляду, створене на комп’ютері\. Сама IP-адреса не надає доступ\. Для застосунку Agent Beacon використайте QR підключення/i;

  await harness.advance(0);
  assert.match(harness.elements.get("stateReason").textContent, expected);
  assert.equal(harness.elements.get("stateTitle").textContent, "Підключіть цей пристрій");
  assert.equal(harness.elements.get("beaconCaption").textContent, "Потрібне посилання з комп’ютера");
  assert.equal(harness.elements.get("connectionLabel").lastElementChild.textContent, "Режим перегляду");

  harness.hide();
  assert.match(harness.elements.get("stateReason").textContent, expected);
  assert.equal(harness.elements.get("connectionLabel").lastElementChild.textContent, "Очікуємо ключ");
  harness.show();
  assert.match(harness.elements.get("stateReason").textContent, expected);
  await harness.advance(0);

  assert.match(harness.elements.get("stateReason").textContent, expected);
  assert.equal(harness.elements.get("connectionLabel").lastElementChild.textContent, "Режим перегляду");
  assert.equal(harness.elements.get("tokenRecovery").hidden, false);
  assert.equal(statusCalls, 0);
});

test("describes green as readiness for the next task in static browser controls", () => {
  const html = fs.readFileSync(path.join(__dirname, "../agent_beacon/web/index.html"), "utf8");
  assert.match(html, /<strong>Готовий до завдання<\/strong><small>Готовий прийняти наступне завдання<\/small>/);
  assert.match(html, /<option value="ready">Готовий до завдання · зелений<\/option>/);
  assert.doesNotMatch(html, /Відповідь завершено|Поточна відповідь закінчилася/);
});

test("creates the documented one-time native pairing link without changing browser token links", async () => {
  const requests = [];
  const harness = makeHarness(async (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/control") return response(200, { viewer_token: "local-view-key", instance_id: "instance-a", lan_enabled: true });
    if (url === "/api/pairing") return response(200, { code: "A".repeat(43), expires_in_seconds: 120 });
    return response(200, freshSnapshot());
  });
  await harness.advance(0);
  harness.elements.get("lanIp").value = "192.168.1.10";
  harness.elements.get("createNativePairing").dispatch("click");
  await harness.flush();
  assert.equal(requests.find((item) => item.url === "/api/pairing").options.body, "{}");
  assert.match(harness.elements.get("pairingLinkOutput").value,
    /^agentbeacon:\/\/pair\?host=192\.168\.1\.10&port=8765#code=A{43}$/);
  assert.equal(harness.elements.get("nativeQr").hidden, false);
  assert.ok(harness.elements.get("nativeQrCanvas").drawCalls.length > 100);
  assert.equal(harness.elements.get("pairingNotice").textContent.includes("2 хвилини"), true);
  await harness.advance(120000);
  assert.equal(harness.elements.get("nativeQr").hidden, true);
  assert.equal(harness.elements.get("pairingLinkOutput").value, "");
  assert.match(harness.elements.get("qrExpiresNotice").textContent, /минув/i);
});

test("lists paired phones as text and revokes the selected device locally", async () => {
  const requests = [];
  let devices = [{ device_id: "12345678-1234-1234-1234-123456789abc", device_name: "Work iPhone" }];
  const harness = makeHarness(async (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/control") return response(200, { viewer_token: "local-view-key", instance_id: "instance-a", lan_enabled: true });
    if (url === "/api/devices") return response(200, { devices });
    if (url === "/api/revoke") {
      assert.deepEqual(JSON.parse(options.body), { device_id: devices[0].device_id });
      devices = [];
      return response(200, { revoked: true });
    }
    return response(200, freshSnapshot());
  });
  await harness.advance(0);
  await harness.flush();
  const list = harness.elements.get("deviceList");
  assert.equal(list.children.length, 1);
  assert.equal(list.children[0].children[0].textContent, "Work iPhone");
  list.children[0].children[1].dispatch("click");
  await harness.flush();
  await harness.flush();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(requests.some((item) => item.url === "/api/revoke"), true);
  assert.equal(list.children[0].textContent, "Підключених телефонів немає.");
});

test("does not render a pairing response that arrives after the page is hidden", async () => {
  let resolvePairing;
  const harness = makeHarness(async (url) => {
    if (url === "/api/control") return response(200, { viewer_token: "local-view-key", instance_id: "instance-a", lan_enabled: true });
    if (url === "/api/pairing") return new Promise((resolve) => { resolvePairing = resolve; });
    return response(200, freshSnapshot());
  });
  await harness.advance(0);
  harness.elements.get("lanIp").value = "192.168.1.10";
  harness.elements.get("createNativePairing").dispatch("click");
  await harness.flush();
  assert.equal(typeof resolvePairing, "function");
  harness.hide();
  resolvePairing(response(200, { code: "A".repeat(43), expires_in_seconds: 120 }));
  await harness.flush();
  assert.equal(harness.elements.get("nativeQrCanvas").drawCalls.length, 0);
  assert.equal(harness.elements.get("pairingLinkOutput").value, "");
  assert.equal(harness.elements.get("createNativePairing").disabled, false);
});

test("does not display a pairing code after a delayed clipboard write outlives its expiry", async () => {
  let resolveClipboard;
  const harness = makeHarness(async (url) => {
    if (url === "/api/control") return response(200, { viewer_token: "local-view-key", instance_id: "instance-a", lan_enabled: true });
    if (url === "/api/pairing") return response(200, { code: "B".repeat(43), expires_in_seconds: 120 });
    return response(200, freshSnapshot());
  }, "", {
    secure: true,
    clipboard: { writeText: () => new Promise((resolve) => { resolveClipboard = resolve; }) },
  });
  await harness.advance(0);
  harness.elements.get("lanIp").value = "192.168.1.10";
  harness.elements.get("createNativePairing").dispatch("click");
  await harness.flush();
  assert.equal(typeof resolveClipboard, "function");
  await harness.advance(120001);
  resolveClipboard();
  await harness.flush();
  assert.equal(harness.elements.get("nativeQrCanvas").drawCalls.length, 0);
  assert.equal(harness.elements.get("pairingLinkOutput").value, "");
  assert.match(harness.elements.get("pairingNotice").textContent, /минув/i);
});

test("vendored QR encoder matches the known one-time pairing matrix", () => {
  const context = vm.createContext({});
  vm.runInContext(qrSource, context);
  const payload = `agentbeacon://pair?host=192.168.1.10&port=8765#code=${"A".repeat(43)}`;
  const qr = context.qrcode(0, "M");
  qr.addData(payload, "Byte");
  qr.make();
  const size = qr.getModuleCount();
  const matrix = Array.from({ length: size }, (_, row) =>
    Array.from({ length: size }, (_, column) => qr.isDark(row, column) ? "1" : "0").join("")
  ).join("\n");
  assert.equal(size, 41);
  assert.equal(crypto.createHash("sha256").update(matrix).digest("hex"), "9f49eafde04c1096111c42685cac437c25e4cb621c88645e78d2646371d35d53");
});

test("accepts observed-session metadata and renders provider labels as text", async () => {
  const observed = freshSnapshot({
    state: "working",
    sessions: [{
      agent_id: "codex", session_id: "session-1", source: "codex", sequence: 4,
      state: "working", available: true, updated_at: "2026-10-16T00:00:00Z",
      turn_id: "turn-2", project_name: "<img src=x>", parent_session_id: "root", active: true,
    }],
    sources: [{ source: "codex", healthy: true, inventory_complete: true, coverage: "observed_sessions" }],
  });
  const harness = makeHarness(async (url) => url === "/api/control" ? response(403, {}) : response(200, observed), "#token=view");
  await harness.advance(0);
  assert.equal(harness.elements.get("beaconBody").dataset.state, "working");
  assert.equal(harness.elements.get("stateReason").textContent, "Стан підключених чатів на вашому комп’ютері");
  assert.equal(harness.elements.get("sessionsList").children[0].children[0].textContent,
    "Чат без назви");
  assert.match(harness.elements.get("sessionsList").children[0].children[2].textContent, /<img src=x>/);
  assert.equal(harness.elements.get("sessionsList").children[0].children[3].children[1].textContent,
    "codex · session-1 · turn turn-2 · parent root");
});

test("rejects unknown source coverage values", async () => {
  const observed = freshSnapshot({
    sources: [{ source: "codex", healthy: true, inventory_complete: true, coverage: "whole_host" }],
  });
  const harness = makeHarness(async (url) => url === "/api/control" ? response(403, {}) : response(200, observed), "#token=view");
  await harness.advance(0);
  assert.equal(harness.elements.get("beaconBody").dataset.state, "unavailable");
});

test("renders the question reminder on a working row while preserving actual state", async () => {
  const snapshot = freshSnapshot({
    state: "awaiting_user", task_state: "working", question_reminder_seconds: 180,
    sessions: [{
      agent_id: "codex", session_id: "session-1", source: "codex", sequence: 4,
      state: "working", display_state: "awaiting_user", question_reminder_active: true,
      question_reminder_remaining_seconds: 90, available: true, updated_at: "2026-10-16T00:00:00Z",
      turn_id: "turn-2", project_name: "project", parent_session_id: null, active: true,
    }],
    sources: [{ source: "codex", healthy: true, inventory_complete: true }],
  });
  const harness = makeHarness(async (url) => url === "/api/control" ? response(403, {}) : response(200, snapshot), "#token=view");
  await harness.advance(0);
  const row = harness.elements.get("sessionsList").children[0];
  assert.equal(row.dataset.state, "awaiting_user");
  assert.match(row.children[1].textContent, /^Нагадування · 1:30$/);
  assert.equal(harness.elements.get("stateTitle").textContent, "Нагадування про запит");
  assert.match(harness.elements.get("stateReason").textContent, /Агент працює; жовтий нагадує про попередній запит · залишилося 1:30/);
});

test("keeps a real waiting session ahead of a reminder-only yellow projection", async () => {
  const snapshot = freshSnapshot({
    state: "awaiting_user", task_state: "working", question_reminder_seconds: 180,
    sessions: [
      { agent_id: "codex", session_id: "working", source: "codex", sequence: 1, state: "working", display_state: "awaiting_user", question_reminder_active: true, question_reminder_remaining_seconds: 60, available: true, updated_at: "2026-10-16T00:00:00Z", turn_id: null, parent_session_id: null, project_name: null, active: true },
      { agent_id: "manual", session_id: "waiting", source: "manual", sequence: 2, state: "awaiting_user", available: true, updated_at: "2026-10-16T00:00:00Z" },
    ],
  });
  const harness = makeHarness(async (url) => url === "/api/control" ? response(403, {}) : response(200, snapshot), "#token=view");
  await harness.advance(0);
  assert.equal(harness.elements.get("stateTitle").textContent, "Очікує на вас");
  assert.equal(harness.elements.get("stateReason").textContent, "Свіжий сигнал від активної сесії.");
  assert.equal(harness.elements.get("sessionsList").children[1].children[1].textContent, "Очікує на вас");
});

test("subtracts monotonic time from the reminder countdown", async () => {
  const snapshot = freshSnapshot({
    state: "awaiting_user", task_state: "working", question_reminder_seconds: 180,
    sessions: [{
      agent_id: "codex", session_id: "session-1", source: "codex", sequence: 4,
      state: "working", display_state: "awaiting_user", question_reminder_active: true,
      question_reminder_remaining_seconds: 90, available: true, updated_at: "2026-10-16T00:00:00Z",
      turn_id: null, project_name: null, parent_session_id: null, active: true,
    }],
  });
  let statusCalls = 0;
  const harness = makeHarness(async (url) => {
    if (url === "/api/control") return response(403, {});
    statusCalls += 1;
    return statusCalls === 1 ? response(200, snapshot) : new Promise(() => {});
  }, "#token=view");
  await harness.advance(0);
  assert.match(harness.elements.get("stateReason").textContent, /залишилося 1:30/);
  await harness.advance(2000);
  assert.match(harness.elements.get("stateReason").textContent, /залишилося 1:28/);
});

test("APK install QR uses the fixed LAN download URL without a viewer token", async () => {
  const harness = makeHarness(async (url) => url === "/api/control"
    ? response(200, { viewer_token: "local-view-key", instance_id: "instance-a", lan_enabled: true })
    : response(200, freshSnapshot()));
  await harness.advance(0);
  harness.elements.get("lanIp").value = "192.168.1.10";
  harness.elements.get("createApkQr").dispatch("click");
  assert.equal(harness.elements.get("apkQr").hidden, false);
  assert.equal(harness.elements.get("apkUrlOutput").textContent, "http://192.168.1.10:8765/downloads/AgentBeacon-debug.apk");
  assert.equal(harness.elements.get("apkUrlOutput").textContent.includes("token"), false);
});

test("blocks phone QR creation when LAN access is disabled or unverified", async () => {
  for (const control of [
    { viewer_token: "local-view-key", instance_id: "instance-a", lan_enabled: false },
    { viewer_token: "local-view-key", instance_id: "instance-a" },
  ]) {
    const harness = makeHarness(async (url) => url === "/api/control" ? response(200, control) : response(200, freshSnapshot()));
    await harness.advance(0);
    harness.elements.get("lanIp").value = "192.168.1.10";
    harness.elements.get("createApkQr").dispatch("click");
    assert.equal(harness.elements.get("apkQr").hidden, true);
    assert.match(harness.elements.get("pairingNotice").textContent, /LAN-доступ/);
    harness.elements.get("nativeQr").hidden = true;
    harness.elements.get("createNativePairing").dispatch("click");
    await harness.flush();
    assert.equal(harness.elements.get("nativeQr").hidden, true);
    assert.equal(harness.elements.get("pairingNotice").textContent.includes("LAN-доступ"), true);
  }
});

test("rejects unsafe project labels and self-parent links before rendering", async () => {
  for (const metadata of [
    { project_name: "C:/private/project", parent_session_id: "root" },
    { project_name: "C:private-project", parent_session_id: "root" },
    { project_name: "private\\project", parent_session_id: "root" },
    { project_name: "private\nproject", parent_session_id: "root" },
    { project_name: "private\u202Eproject", parent_session_id: "root" },
    { project_name: "safe-project", parent_session_id: "session-1" },
  ]) {
    const observed = freshSnapshot({
      state: "working",
      sessions: [{
        agent_id: "codex", session_id: "session-1", source: "codex", sequence: 4,
        state: "working", available: true, updated_at: "2026-10-16T00:00:00Z",
        turn_id: "turn-2", ...metadata, active: true,
      }],
      sources: [{ source: "codex", healthy: true, inventory_complete: true }],
    });
    const harness = makeHarness(async (url) => url === "/api/control" ? response(403, {}) : response(200, observed), "#token=view");
    await harness.advance(0);
    assert.equal(harness.elements.get("beaconBody").dataset.state, "unavailable");
  }
});

test("rejects closed observed sessions that claim an available state", async () => {
  const observed = freshSnapshot({
    sessions: [{
      agent_id: "codex", session_id: "session-1", source: "codex", sequence: 4,
      state: "working", available: true, updated_at: "2026-10-16T00:00:00Z",
      turn_id: "turn-2", project_name: "safe-project", parent_session_id: null, active: false,
    }],
  });
  const harness = makeHarness(async (url) => url === "/api/control" ? response(403, {}) : response(200, observed), "#token=view");
  await harness.advance(0);
  assert.equal(harness.elements.get("beaconBody").dataset.state, "unavailable");
});

test("dims an expired lease even when the next status request is still pending", async () => {
  let statusCalls = 0;
  const harness = makeHarness(async (url) => {
    if (url === "/api/control") return response(403, {});
    statusCalls += 1;
    if (statusCalls === 1) return response(200, freshSnapshot({ ttl_seconds: 5, valid_for_seconds: 1 }));
    return new Promise(() => {});
  }, "#token=view");
  await harness.advance(0);
  assert.equal(harness.elements.get("beaconBody").dataset.state, "ready");
  await harness.advance(1000);
  assert.equal(statusCalls, 2);
  assert.equal(harness.elements.get("beaconBody").dataset.state, "unavailable");
});

test("dims the signal when a status request exceeds its three second timeout", async () => {
  const harness = makeHarness(async (url, options = {}) => {
    if (url === "/api/control") return response(403, {});
    return new Promise((resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new Error("request timed out")), { once: true });
    });
  }, "#token=view");
  await harness.advance(0);
  await harness.advance(3000);
  assert.equal(harness.elements.get("beaconBody").dataset.state, "unavailable");
  assert.equal(harness.elements.get("connectionLabel").lastElementChild.textContent, "З’єднання перервано");
});

test("ignores a status response that arrives after the tab was hidden", async () => {
  let resolveStatus;
  const harness = makeHarness(async (url) => {
    if (url === "/api/control") return response(403, {});
    return new Promise((resolve) => { resolveStatus = resolve; });
  }, "#token=view");
  await harness.advance(0);
  assert.equal(typeof resolveStatus, "function");
  harness.hide();
  resolveStatus(response(200, freshSnapshot()));
  await harness.flush();
  assert.equal(harness.elements.get("beaconBody").dataset.state, "unavailable");
  assert.equal(harness.elements.get("stateTitle").textContent, "Стан агента не підтверджено");
});

test("clears a rejected token and shows the minimal recovery control", async () => {
  const harness = makeHarness(async (url) => url === "/api/control" ? response(403, {}) : response(401, {}), "#token=bad-key");
  await harness.advance(0);
  assert.equal(harness.storage.has("agent-beacon.viewer-token"), false);
  assert.equal(harness.elements.get("tokenRecovery").hidden, false);
  assert.match(harness.elements.get("recoveryNotice").textContent, /ключ перегляду/i);
  assert.equal(harness.elements.get("beaconBody").dataset.state, "unavailable");
});

test("an old 401 cannot clear the token accepted from local bootstrap", async () => {
  let controlCalls = 0;
  let statusCalls = 0;
  let resolveControl;
  let resolveOldStatus;
  const authHeaders = [];
  const harness = makeHarness(async (url, options = {}) => {
    if (url === "/api/control") {
      controlCalls += 1;
      if (controlCalls === 1) return response(403, {});
      return new Promise((resolve) => { resolveControl = resolve; });
    }
    authHeaders.push(options.headers.Authorization);
    statusCalls += 1;
    if (statusCalls === 1) return response(200, freshSnapshot());
    if (statusCalls === 2) return new Promise((resolve) => { resolveOldStatus = resolve; });
    return response(200, freshSnapshot({ instance_id: "instance-b", revision: 0 }));
  }, "#token=old-view-key");

  await harness.advance(0);
  harness.hide();
  harness.show();
  await harness.advance(0);
  assert.equal(typeof resolveControl, "function");
  assert.equal(typeof resolveOldStatus, "function");

  resolveControl(response(200, { viewer_token: "new-view-key", instance_id: "instance-b" }));
  await harness.flush();
  resolveOldStatus(response(401, {}));
  await harness.flush();
  await harness.advance(0);

  assert.equal(harness.storage.get("agent-beacon.viewer-token"), "new-view-key");
  assert.equal(authHeaders.at(-1), "Bearer new-view-key");
  assert.equal(harness.elements.get("beaconBody").dataset.state, "ready");
});

test("a local page bootstraps a rotated token after the server restarts", async () => {
  let controlCalls = 0;
  let statusCalls = 0;
  const authHeaders = [];
  const harness = makeHarness(async (url, options = {}) => {
    if (url === "/api/control") {
      controlCalls += 1;
      if (controlCalls === 1) return response(403, {});
      return response(200, {
        viewer_token: "new-local-key",
        instance_id: "instance-b",
      });
    }
    authHeaders.push(options.headers.Authorization);
    statusCalls += 1;
    if (statusCalls === 1) return response(200, freshSnapshot());
    if (statusCalls === 2) return response(401, {});
    return response(200, freshSnapshot({ instance_id: "instance-b", revision: 0 }));
  }, "#token=old-local-key");

  await harness.advance(0);
  assert.equal(harness.elements.get("beaconBody").dataset.state, "ready");
  await harness.advance(1000);
  await harness.advance(150);

  assert.equal(controlCalls, 2);
  assert.equal(authHeaders.at(-1), "Bearer new-local-key");
  assert.equal(harness.storage.get("agent-beacon.viewer-token"), "new-local-key");
  assert.equal(harness.elements.get("beaconBody").dataset.state, "ready");
});

