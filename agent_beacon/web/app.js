(() => {
  "use strict";

  const VALID_STATES = new Set(["working", "awaiting_user", "ready"]);
  const STATE_TEXT = {
    working: { title: "Працює", overline: "АГЕНТ ВИКОНУЄ ЗАВДАННЯ", chip: "ПРАЦЮЄ" },
    awaiting_user: { title: "Очікує на вас", overline: "ПОТРІБНА ВАША ДІЯ", chip: "ОЧІКУЄ" },
    ready: { title: "Готовий до завдання", overline: "ГОТОВИЙ ДО НАСТУПНОГО ЗАВДАННЯ", chip: "ГОТОВИЙ" },
  };
  const TOKEN_STORAGE_KEY = "agent-beacon.viewer-token";
  const MAX_TOKEN_LENGTH = 4096;
  const MISSING_TOKEN_MESSAGE = "Відкрийте посилання для перегляду, створене на комп’ютері. Сама IP-адреса не надає доступ. Для застосунку Agent Beacon використайте QR підключення.";

  const elements = {
    body: document.getElementById("beaconBody"),
    caption: document.getElementById("beaconCaption"),
    liveChip: document.getElementById("liveChip"),
    stateOverline: document.getElementById("stateOverline"),
    stateTitle: document.getElementById("stateTitle"),
    stateReason: document.getElementById("stateReason"),
    updated: document.getElementById("updatedLabel"),
    sessionCount: document.getElementById("sessionCount"),
    sessionsList: document.getElementById("sessionsList"),
    providers: document.getElementById("providerOverview"),
    projectsEmpty: document.getElementById("projectsEmpty"),
    history: document.getElementById("stateHistory"),
    leaseSeconds: document.getElementById("leaseSeconds"),
    connection: document.getElementById("connectionLabel"),
    pairingNotice: document.getElementById("pairingNotice"),
    pairingLinkLabel: document.getElementById("pairingLinkLabel"),
    pairingLinkOutput: document.getElementById("pairingLinkOutput"),
    nativeQr: document.getElementById("nativeQr"),
    nativeQrCanvas: document.getElementById("nativeQrCanvas"),
    qrExpiresNotice: document.getElementById("qrExpiresNotice"),
    recoveryNotice: document.getElementById("recoveryNotice"),
    controlNotice: document.getElementById("controlNotice"),
    tokenRecovery: document.getElementById("tokenRecovery"),
    viewerTokenInput: document.getElementById("viewerTokenInput"),
    lanIp: document.getElementById("lanIp"),
    lanStatus: document.getElementById("lanStatus"),
    apkQr: document.getElementById("apkQr"),
    apkQrCanvas: document.getElementById("apkQrCanvas"),
    apkUrlOutput: document.getElementById("apkUrlOutput"),
    localPanel: document.getElementById("localPanel"),
  };

  let viewerToken = readInitialToken();
  let currentSnapshot = null;
  let expiresAt = 0;
  let snapshotReceivedAt = 0;
  let pollEpoch = 0;
  let uiEpoch = 0;
  let polling = false;
  let pollController = null;
  let mutationController = null;
  let mutationInFlight = false;
  let nativeCodeTimer = null;
  let nativeCodeInterval = null;
  let nativePairingEpoch = 0;
  let nativePairingInFlight = false;
  let pollTimer = null;
  let failureCount = 0;
  let lastControlInstance = null;
  let lanEnabled = null;
  let controlEpoch = 0;
  let deviceListEpoch = 0;
  let deviceMutationInFlight = false;
  let historyKey = null;
  let sessionRenderKey = null;

  function readInitialToken() {
    let fromFragment = "";
    const fragment = window.location.hash;
    if (fragment) {
      const params = new URLSearchParams(fragment.slice(1));
      fromFragment = params.get("token") || "";
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      if (!isValidToken(fromFragment)) {
        try { window.sessionStorage.removeItem(TOKEN_STORAGE_KEY); } catch (_) { /* No stored token is required to remove the URL fragment. */ }
      }
    }
    if (isValidToken(fromFragment)) {
      try { window.sessionStorage.setItem(TOKEN_STORAGE_KEY, fromFragment); } catch (_) { /* Use the in-memory token for this page. */ }
      return fromFragment;
    }
    try {
      const saved = window.sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
      return isValidToken(saved) ? saved : "";
    } catch (_) {
      return "";
    }
  }

  function isValidToken(token) {
    return typeof token === "string" && token.length > 0 && token.length <= MAX_TOKEN_LENGTH && /^[\x21-\x7e]+$/.test(token);
  }

  function setConnection(label, online = false) {
    elements.connection.lastElementChild.textContent = label;
    elements.connection.dataset.online = String(online);
  }

  function setUnavailable(reason) {
    elements.body.dataset.state = "unavailable";
    elements.body.querySelector(".signal-stack").setAttribute("aria-label", "Світлофор: сигнал недоступний");
    elements.stateOverline.textContent = "СИГНАЛ НЕАКТИВНИЙ";
    elements.stateTitle.textContent = "Стан агента не підтверджено";
    elements.stateReason.textContent = reason;
    elements.caption.textContent = "Очікуємо свіжий сигнал";
    elements.liveChip.lastElementChild.textContent = "ОЧІКУВАННЯ";
    elements.updated.textContent = "Сигнал ще не отримано";
    elements.sessionCount.textContent = "0 чатів";
    elements.sessionsList.hidden = true;
    elements.sessionsList.replaceChildren();
    sessionRenderKey = null;
    elements.providers.replaceChildren();
    elements.projectsEmpty.hidden = false;
  }

  function showMissingTokenState() {
    setUnavailable(MISSING_TOKEN_MESSAGE);
    elements.stateTitle.textContent = "Підключіть цей пристрій";
    elements.caption.textContent = "Потрібне посилання з комп’ютера";
    setConnection("Очікуємо ключ", false);
    elements.tokenRecovery.hidden = false;
  }

  function isSafeIdentifier(value) {
    return typeof value === "string" && value.length >= 1 && value.length <= 64 && /^[A-Za-z0-9._-]+$/.test(value);
  }

  function isSafeProjectName(value) {
    return typeof value === "string" && value.length <= 160 &&
      !/[\\/:]/.test(value) && !/[\p{Cc}\p{Cf}]/u.test(value);
  }

  function validateSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) return false;
    if (snapshot.schema_version !== 1 || typeof snapshot.instance_id !== "string" || snapshot.instance_id.length < 1 || snapshot.instance_id.length > 128) return false;
    if (!Number.isSafeInteger(snapshot.revision) || snapshot.revision < 0) return false;
    if (typeof snapshot.ttl_seconds !== "number" || !Number.isFinite(snapshot.ttl_seconds) || snapshot.ttl_seconds <= 0 || snapshot.ttl_seconds > 86400) return false;
    if (typeof snapshot.valid_for_seconds !== "number" || !Number.isFinite(snapshot.valid_for_seconds) || snapshot.valid_for_seconds < 0 || snapshot.valid_for_seconds > snapshot.ttl_seconds) return false;
    if (snapshot.question_reminder_seconds !== undefined && (typeof snapshot.question_reminder_seconds !== "number" || !Number.isFinite(snapshot.question_reminder_seconds) || snapshot.question_reminder_seconds < 0 || snapshot.question_reminder_seconds > 86400)) return false;
    if (snapshot.task_state !== undefined &&
        ((snapshot.task_state !== null && !VALID_STATES.has(snapshot.task_state)) ||
          (snapshot.available && snapshot.task_state === null) || (!snapshot.available && snapshot.task_state !== null))) return false;
    if (typeof snapshot.available !== "boolean" || typeof snapshot.partial !== "boolean") return false;
    if (snapshot.reason !== null && snapshot.reason !== "unavailable" && snapshot.reason !== "stale") return false;
    if (snapshot.available && (!VALID_STATES.has(snapshot.state) || snapshot.reason !== null)) return false;
    if (!snapshot.available && (snapshot.state !== null || snapshot.reason === null)) return false;
    if (!snapshot.available && snapshot.valid_for_seconds !== 0) return false;
    if (!Array.isArray(snapshot.sessions) || typeof snapshot.server_time !== "string" || !Number.isFinite(Date.parse(snapshot.server_time))) return false;
    const validSessions = snapshot.sessions.every((session) => {
      if (!session || typeof session !== "object" || Array.isArray(session) ||
          !isSafeIdentifier(session.agent_id) || !isSafeIdentifier(session.session_id) ||
          !Number.isSafeInteger(session.sequence) || session.sequence <= 0 ||
          typeof session.available !== "boolean" || typeof session.updated_at !== "string" ||
          !Number.isFinite(Date.parse(session.updated_at))) return false;
      if (session.session_name !== undefined && session.session_name !== null &&
          (typeof session.session_name !== "string" || session.session_name.length > 320 || /[\p{Cc}\p{Cf}]/u.test(session.session_name))) return false;
      if (session.valid_for_seconds !== undefined && (typeof session.valid_for_seconds !== "number" ||
          !Number.isFinite(session.valid_for_seconds) || session.valid_for_seconds < 0 || session.valid_for_seconds > 86400 ||
          (!session.available && session.valid_for_seconds !== 0))) return false;
      if (session.source === "manual") {
        if (!VALID_STATES.has(session.state)) return false;
      } else {
        if (session.source !== "codex" && session.source !== "claude" && session.source !== "antigravity") return false;
        const nullableId = (value) => value === null || isSafeIdentifier(value);
        if (!((session.state === null || VALID_STATES.has(session.state)) &&
          (session.state !== null || !session.available) &&
          nullableId(session.turn_id) && nullableId(session.parent_session_id) &&
          (session.project_name === null || isSafeProjectName(session.project_name)) &&
          (session.parent_session_id === null || session.parent_session_id !== session.session_id) &&
          typeof session.active === "boolean" &&
          (session.active || (session.state === null && !session.available)))) return false;
      }
      const projectedStateValid = session.display_state === undefined || session.display_state === null || VALID_STATES.has(session.display_state);
      const reminderValid = session.question_reminder_active === undefined || typeof session.question_reminder_active === "boolean";
      const remainingValid = session.question_reminder_remaining_seconds === undefined ||
        (typeof session.question_reminder_remaining_seconds === "number" && Number.isFinite(session.question_reminder_remaining_seconds) &&
          session.question_reminder_remaining_seconds >= 0 && session.question_reminder_remaining_seconds <= (snapshot.question_reminder_seconds ?? 86400));
      const projectionConsistent = session.display_state === undefined ||
        (!session.available ? session.display_state === null : session.display_state !== null &&
          (session.state === "working"
            ? (session.display_state === "working" || (session.display_state === "awaiting_user" && session.question_reminder_active === true))
            : session.display_state === session.state));
      return projectedStateValid && projectionConsistent && reminderValid && remainingValid &&
        (session.question_reminder_active !== true || (session.available && session.state === "working" &&
          session.display_state === "awaiting_user" && session.question_reminder_remaining_seconds > 0));
    });
    const validSources = snapshot.sources === undefined || (Array.isArray(snapshot.sources) && snapshot.sources.length <= 16 &&
      snapshot.sources.every((item) => item && typeof item === "object" &&
        (item.source === "codex" || item.source === "claude" || item.source === "antigravity") &&
        typeof item.healthy === "boolean" && typeof item.inventory_complete === "boolean" &&
        (item.idle_ready === undefined || typeof item.idle_ready === "boolean") &&
        (item.valid_for_seconds === undefined || (typeof item.valid_for_seconds === "number" && Number.isFinite(item.valid_for_seconds) && item.valid_for_seconds >= 0 && item.valid_for_seconds <= 86400)) &&
        (item.coverage === undefined || item.coverage === "all_sessions" || item.coverage === "observed_sessions")));
    let previousId = 0;
    const validHistory = snapshot.history === undefined || (Array.isArray(snapshot.history) && snapshot.history.length <= 30 && snapshot.history.every((event) => {
      if (!event || !Number.isSafeInteger(event.id) || event.id <= previousId ||
          typeof event.at !== "string" || !Number.isFinite(Date.parse(event.at)) ||
          (!VALID_STATES.has(event.state) && event.state !== "unknown") ||
          typeof event.reminder !== "boolean" || typeof event.partial !== "boolean" ||
          !Number.isSafeInteger(event.chat_count) || event.chat_count < 0 || event.chat_count > 128 ||
          (event.project_name !== null && !isSafeProjectName(event.project_name)) ||
          (event.source !== undefined && !["codex", "claude", "antigravity", "manual"].includes(event.source)) ||
          (event.session_id !== undefined && !isSafeIdentifier(event.session_id)) ||
          (event.session_name != null && (typeof event.session_name !== "string" || event.session_name.length > 320 || /[\p{Cc}\p{Cf}]/u.test(event.session_name))) ||
          (event.status_reason != null && (typeof event.status_reason !== "string" || event.status_reason.length > 64))) return false;
      previousId = event.id;
      return true;
    }));
    return validSessions && validSources && validHistory && (!snapshot.available || snapshot.sessions.some((session) => session.available) || snapshot.sources?.some((item) => item.healthy && item.inventory_complete));
  }

  function formatAge(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    if (seconds < 2) return "Оновлено щойно";
    if (seconds < 60) return `Оновлено ${seconds} с тому`;
    const minutes = Math.floor(seconds / 60);
    return `Оновлено ${minutes} хв тому`;
  }

  function applySnapshot(snapshot, requestStartedAt) {
    if (!validateSnapshot(snapshot)) return false;
    if (currentSnapshot && snapshot.instance_id === currentSnapshot.instance_id && snapshot.revision < currentSnapshot.revision) return false;
    if (currentSnapshot && snapshot.instance_id !== currentSnapshot.instance_id) {
      currentSnapshot = null;
      expiresAt = 0;
    }
    currentSnapshot = snapshot;
    renderHistory(snapshot);
    snapshotReceivedAt = requestStartedAt ?? performance.now();
    const elapsedRequest = Math.max(0, performance.now() - (requestStartedAt ?? performance.now()));
    expiresAt = performance.now() + Math.max(0, snapshot.valid_for_seconds * 1000 - elapsedRequest);
    const remainingLease = Math.max(0, expiresAt - performance.now());
    elements.leaseSeconds.textContent = String(snapshot.ttl_seconds);

    if (!snapshot.available) {
      const observerUnknown = snapshot.sources?.some((item) => item.healthy && item.coverage === "observed_sessions") &&
        snapshot.sessions.some((session) => !session.available && session.state === null);
      setUnavailable(snapshot.reason === "stale"
        ? "Сигнал застарів. Очікуємо наступне оновлення."
        : observerUnknown ? "Стан сесії не підтверджено."
          : "Свіжий стан агента поки недоступний.");
      const missing = snapshot.sessions.filter((session) => session.active !== false && !session.available);
      if (missing.length) elements.stateReason.textContent = missing.map((session) => `${providerName(session.source)} · ${chatName(session.session_name)}: ${unavailableReason(session.status_reason)}`).join("; ");
      elements.sessionCount.textContent = `${snapshot.sessions.filter((session) => session.active !== false).length} ${pluralSessions(snapshot.sessions.filter((session) => session.active !== false).length)}`;
      renderSessions(snapshot.sessions);
      setConnection("Немає свіжого сигналу", false);
      return true;
    }
    if (remainingLease <= 0) {
      setUnavailable("Сигнал застарів. Очікуємо наступне оновлення.");
      elements.sessionCount.textContent = `${snapshot.sessions.filter((session) => session.active !== false).length} ${pluralSessions(snapshot.sessions.filter((session) => session.active !== false).length)}`;
      setConnection("Очікуємо сигнал", false);
      renderSessions(snapshot.sessions);
      return true;
    }

    renderSnapshotState(snapshot);
    elements.updated.textContent = "Оновлено щойно";
    elements.sessionCount.textContent = `${snapshot.sessions.filter((session) => session.active !== false).length} ${pluralSessions(snapshot.sessions.filter((session) => session.active !== false).length)}`;
    renderSessions(snapshot.sessions);
    setConnection("Підключено", true);
    return true;
  }

  function reminderSecondsLeft(session, elapsedSeconds = Math.max(0, performance.now() - snapshotReceivedAt) / 1000) {
    if (session.question_reminder_active !== true || typeof session.question_reminder_remaining_seconds !== "number") return 0;
    return Math.max(0, session.question_reminder_remaining_seconds - elapsedSeconds);
  }

  function reminderOverlay(snapshot) {
    if (snapshot.state !== "awaiting_user" || snapshot.task_state !== "working") return null;
    if (snapshot.sessions.some((session) => session.available && session.state === "awaiting_user")) return null;
    const remaining = Math.max(0, ...snapshot.sessions.map((session) => reminderSecondsLeft(session)));
    return remaining > 0 ? remaining : null;
  }

  function formatCodeTime(milliseconds) {
    const totalSeconds = Math.ceil(milliseconds / 1000);
    return `${Math.floor(totalSeconds / 60)}:${String(totalSeconds % 60).padStart(2, "0")}`;
  }

  function renderSnapshotState(snapshot) {
    const reminderLeft = reminderOverlay(snapshot);
    const reminderOnly = reminderLeft !== null;
    const actualWaiting = snapshot.sessions.some((session) => session.available && session.state === "awaiting_user");
    const displayState = actualWaiting ? "awaiting_user" : reminderOnly ? "awaiting_user"
      : snapshot.state === "awaiting_user" && snapshot.task_state === "working" ? "working" : snapshot.state;
    const state = STATE_TEXT[displayState];
    const observedCoverage = snapshot.sources?.some((item) => item.coverage === "observed_sessions") === true;
    elements.body.dataset.state = displayState;
    elements.body.querySelector(".signal-stack").setAttribute("aria-label", `Світлофор: ${state.title}`);
    elements.stateOverline.textContent = reminderOnly ? "АГЕНТ ВИКОНУЄ ЗАВДАННЯ" : state.overline;
    elements.stateTitle.textContent = reminderOnly ? "Нагадування про запит" : state.title;
    elements.stateReason.textContent = reminderOnly
      ? `Агент працює; жовтий нагадує про попередній запит · залишилося ${formatCodeTime(reminderLeft * 1000)}`
      : observedCoverage
      ? "Стан підключених чатів на вашому комп’ютері"
      : snapshot.partial ? "Частина сесій недоступна" : "Свіжий сигнал від активної сесії.";
    elements.caption.textContent = reminderOnly ? "Нагадування про попередній запит" : snapshot.partial ? "Доступна частина сесій" : "Стан підтверджено свіжим сигналом";
    elements.liveChip.lastElementChild.textContent = state.chip;
  }

  function renderSessions(sessions) {
    const active = sessions.filter((session) => session.active !== false);
    const key = JSON.stringify([currentSnapshot?.sources?.map((item) => [item.source, item.idle_ready, sourceIsFresh(item)]), active.map((session) => [session.source, session.session_id, session.project_name, session.session_name,
      session.state, rowIsAvailable(session), session.display_state, session.status_reason,
      session.turn_id, session.parent_session_id, Math.ceil(reminderSecondsLeft(session))])]);
    if (key === sessionRenderKey) return;
    sessionRenderKey = key;
    const expanded = new Set([...elements.sessionsList.children].filter((row) => row.lastElementChild?.open).map((row) => row.dataset.chat));
    elements.sessionsList.replaceChildren();
    elements.projectsEmpty.hidden = active.length > 0;
    renderProviders(active);
    if (active.length === 0) {
      elements.sessionsList.hidden = true;
      return;
    }
    elements.sessionsList.hidden = false;
    for (const session of active) {
      const row = document.createElement("div");
      row.className = "session-row";
      row.dataset.chat = `${session.source}:${session.session_id}`;
      const reminderLeft = reminderSecondsLeft(session);
      const reminderOnly = reminderLeft > 0 && session.state === "working";
      const projectedState = reminderOnly ? "awaiting_user"
        : session.state === "working" && session.question_reminder_active === true ? "working"
          : session.display_state === undefined ? session.state : session.display_state;
      row.dataset.state = rowIsAvailable(session) && projectedState ? projectedState : "unavailable";
      const name = document.createElement("span");
      name.className = "session-name";
      const label = chatName(session.session_name);
      const turn = session.turn_id ? ` · turn ${session.turn_id}` : "";
      const parent = session.parent_session_id ? ` · parent ${session.parent_session_id}` : "";
      name.textContent = label;
      const state = document.createElement("span");
      state.className = "session-state";
      state.textContent = !rowIsAvailable(session) || !projectedState ? "Потрібна перевірка зв’язку"
        : session.status_reason === "awaiting_first_request" ? "Готовий до першого запиту"
          : reminderOnly ? `Нагадування · ${formatCodeTime(reminderLeft * 1000)}` : STATE_TEXT[projectedState].title;
      row.append(name, state);
      const note = document.createElement("span");
      note.className = "session-note";
      note.textContent = (projectName(session.project_name)) + " · " + providerName(session.source)
        + (!rowIsAvailable(session) ? " · " + unavailableReason(session.status_reason) : "");
      const details = document.createElement("details");
      details.className = "session-technical";
      details.open = expanded.has(row.dataset.chat);
      const summary = document.createElement("summary"); summary.textContent = "Технічні дані чату";
      const ids = document.createElement("p"); ids.textContent = `${session.source} · ${session.session_id}${turn}${parent}`;
      details.append(summary, ids);
      row.append(note, details);
      elements.sessionsList.append(row);
    }
  }

  function chatName(name) { return name || "Чат без назви"; }
  function projectName(name) { return name && name.toLowerCase() !== "new-chat" ? name : "Проєкт не вказано"; }

  function providerName(source) { return source === "codex" ? "Codex" : source === "claude" ? "Claude Code" : source === "antigravity" ? "Antigravity" : "Демо"; }

  function rowIsAvailable(session) {
    return session.available && (session.valid_for_seconds === undefined ||
      Math.max(0, performance.now() - snapshotReceivedAt) < session.valid_for_seconds * 1000);
  }

  function sourceIsFresh(source) {
    const lease = source.valid_for_seconds ?? currentSnapshot?.valid_for_seconds ?? 0;
    return source.healthy && Math.max(0, performance.now() - snapshotReceivedAt) < lease * 1000;
  }

  function unavailableReason(reason) {
    return ({ awaiting_activity: "Очікує першого запиту", observer_restarted: "Перепідключається після перезапуску",
      observer_stale: "Давно не було оновлень", signal_expired: "Давно не було оновлень",
      observer_offline: "Втрачено зв’язок з агентом", closed: "Відстеження сесії завершено",
      tracking_lost: "Claude не передав ідентифікатор запиту; очікуємо нову подію цього чату",
      hook_gap: "Пропущено подію; очікуємо новий сигнал цього чату",
      invalid_hook: "Некоректна подія; очікуємо новий сигнал цього чату",
      observer_gap: "Зв’язок переривався; очікуємо нову подію цього чату",
      process_unavailable: "Процес агента недоступний; очікуємо повторне підключення",
      status_unconfirmed: "Очікуємо підтвердження стану" })[reason] || "Очікуємо підтвердження стану";
  }

  function renderProviders(sessions) {
    elements.providers.replaceChildren();
    for (const source of ["codex", "claude", "antigravity"]) {
      const chats = sessions.filter((session) => session.source === source);
      const row = document.createElement("div"); row.className = "provider-card";
      const title = document.createElement("strong"); title.textContent = providerName(source);
      const count = document.createElement("span");
      const waiting = chats.filter((chat) => rowIsAvailable(chat) && (chat.state === "awaiting_user" || reminderSecondsLeft(chat) > 0)).length;
      const working = chats.filter((chat) => rowIsAvailable(chat) && chat.state === "working" && reminderSecondsLeft(chat) <= 0).length;
      const missing = chats.filter((chat) => !rowIsAvailable(chat)).length;
      const parts = [];
      if (working) parts.push(`${working} працює`);
      if (waiting) parts.push(`${waiting} потребує уваги`);
      if (missing) parts.push(`${missing} без підтвердження`);
      const idle = currentSnapshot?.sources?.some((item) => item.source === source && item.idle_ready && sourceIsFresh(item) && item.inventory_complete);
      count.textContent = parts.length ? parts.join(" · ") : chats.length || idle ? "Готовий до нового запиту" : "Ще немає підключених чатів";
      row.append(title, count); elements.providers.append(row);
    }
  }

  function renderHistory(snapshot) {
    const events = snapshot.history || [];
    const key = snapshot.instance_id + ":" + JSON.stringify(events);
    if (key === historyKey) return;
    historyKey = key; elements.history.replaceChildren();
    if (!events.length) {
      const empty = document.createElement("p"); empty.className = "field-help"; empty.textContent = "Поки немає подій. Історія з’явиться після оновлення сервера."; elements.history.append(empty); return;
    }
    for (const event of [...events].reverse()) {
      const row = document.createElement("div"); row.className = "history-row"; row.dataset.state = event.state;
      const time = document.createElement("time"); time.textContent = new Date(event.at).toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const actor = event.source ? `${providerName(event.source)} · ${chatName(event.session_name)}` : "Комп’ютер";
      const label = event.status_reason === "closed" ? "Відстеження сесії завершено" : event.reminder ? "Нагадування про запит" : STATE_TEXT[event.state]?.title || "Стан не підтверджено";
      const title = document.createElement("strong"); title.textContent = `${actor} — ${label}`;
      const context = document.createElement("span"); context.textContent = event.source
        ? `${projectName(event.project_name)}${event.status_reason === "closed" ? " · Історія чату збережена" : ""}${event.state === "unknown" && event.status_reason !== "closed" ? " · " + unavailableReason(event.status_reason) : ""}`
        : `${event.chat_count} ${pluralSessions(event.chat_count)}${event.partial ? " · Частина чатів недоступна" : ""}`;
      row.append(time, title, context); elements.history.append(row);
    }
  }

  function pluralSessions(count) {
    const lastTwo = count % 100;
    const last = count % 10;
    if (lastTwo >= 11 && lastTwo <= 14) return "чатів";
    if (last === 1) return "чат";
    if (last >= 2 && last <= 4) return "чати";
    return "чатів";
  }

  function expireIfNeeded() {
    if (!currentSnapshot) return;
    if (!currentSnapshot.available) { renderSessions(currentSnapshot.sessions); return; }
    if (!expiresAt) return;
    if (document.visibilityState === "hidden" || performance.now() >= expiresAt) {
      setUnavailable(document.visibilityState === "hidden" ? "Вкладка була неактивна. Перевіряємо свіжий сигнал." : "Сигнал застарів. Очікуємо наступне оновлення.");
      setConnection("Очікуємо сигнал", false);
    } else {
      renderSnapshotState(currentSnapshot);
      renderSessions(currentSnapshot.sessions);
      const totalLease = currentSnapshot.ttl_seconds * 1000;
      elements.updated.textContent = formatAge(Math.max(0, totalLease - Math.max(0, expiresAt - performance.now())));
    }
  }

  function abortPoll() {
    pollEpoch += 1;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
    if (pollController) pollController.abort();
  }

  function schedulePoll(delay) {
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    if (!viewerToken || document.visibilityState === "hidden") return;
    pollTimer = window.setTimeout(() => {
      pollTimer = null;
      void pollStatus();
    }, delay);
  }

  async function pollStatus() {
    if (!viewerToken || document.visibilityState === "hidden") return;
    if (polling) {
      schedulePoll(150);
      return;
    }
    polling = true;
    const requestEpoch = pollEpoch;
    const controller = new AbortController();
    pollController = controller;
    const timeoutId = window.setTimeout(() => controller.abort(), 3000);
    try {
      const requestStartedAt = performance.now();
      const response = await fetch("/api/status", {
        method: "GET",
        headers: { Authorization: `Bearer ${viewerToken}` },
        cache: "no-store",
        signal: controller.signal,
      });
      if (requestEpoch !== pollEpoch || document.visibilityState === "hidden") return;
      if (response.status === 401 || response.status === 403) {
        clearViewerToken();
        setUnavailable("Ключ перегляду недійсний. Вставте актуальний ключ нижче.");
        setConnection("Потрібен ключ перегляду", false);
        elements.tokenRecovery.hidden = false;
        elements.recoveryNotice.textContent = "Доступ не підтверджено. Вставте актуальний ключ перегляду з посилання, отриманого на комп’ютері.";
        // A local page can obtain the process's rotated key after a restart. Remote
        // viewers are denied by /api/control and keep the manual recovery prompt.
        void loadLocalControl();
        return;
      }
      if (!response.ok) throw new Error("status-request-failed");
      const snapshot = await response.json();
      if (requestEpoch !== pollEpoch || document.visibilityState === "hidden") return;
      if (!applySnapshot(snapshot, requestStartedAt)) throw new Error("invalid-snapshot");
      failureCount = 0;
    } catch (_) {
      if (requestEpoch === pollEpoch && document.visibilityState !== "hidden") {
        failureCount += 1;
        setUnavailable("Не вдалося отримати свіжий сигнал. Перевірте з’єднання.");
        setConnection("З’єднання перервано", false);
      }
    } finally {
      window.clearTimeout(timeoutId);
      if (pollController === controller) pollController = null;
      polling = false;
      if (requestEpoch === pollEpoch && viewerToken && document.visibilityState !== "hidden") {
        const delay = Math.min(1000 * (2 ** Math.min(failureCount, 4)), 15000);
        schedulePoll(delay);
      }
    }
  }

  function clearViewerToken() {
    viewerToken = "";
    try { window.sessionStorage.removeItem(TOKEN_STORAGE_KEY); } catch (_) { /* The in-memory token is still cleared. */ }
    uiEpoch += 1;
    if (mutationController) mutationController.abort();
    currentSnapshot = null;
    expiresAt = 0;
    abortPoll();
  }

  function setViewerToken(token) {
    if (!isValidToken(token)) return false;
    controlEpoch += 1;
    uiEpoch += 1;
    if (mutationController) mutationController.abort();
    currentSnapshot = null;
    expiresAt = 0;
    viewerToken = token;
    try { window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token); } catch (_) { /* The in-memory token still works until this tab closes. */ }
    elements.tokenRecovery.hidden = true;
    elements.viewerTokenInput.value = "";
    elements.recoveryNotice.textContent = "Ключ збережено лише в цій вкладці. Перевіряємо доступ…";
    setUnavailable("Перевіряємо ключ перегляду.");
    setConnection("Перевіряємо доступ", false);
    abortPoll();
    failureCount = 0;
    schedulePoll(0);
    return true;
  }

  function timeoutSignal(milliseconds) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), milliseconds);
    return { controller, clear: () => window.clearTimeout(timer) };
  }

  async function loadDevices() {
    if (document.visibilityState === "hidden") return;
    const requestEpoch = ++deviceListEpoch;
    const list = document.getElementById("deviceList");
    const notice = document.getElementById("devicesNotice");
    const timeout = timeoutSignal(3000);
    try {
      const response = await fetch("/api/devices", { method: "GET", cache: "no-store", signal: timeout.controller.signal });
      if (requestEpoch !== deviceListEpoch || document.visibilityState === "hidden") return;
      if (!response.ok) throw new Error("Не вдалося завантажити список телефонів.");
      const payload = await response.json();
      if (requestEpoch !== deviceListEpoch || document.visibilityState === "hidden") return;
      if (!payload || !Array.isArray(payload.devices) || payload.devices.length > 32 || payload.devices.some((device) =>
        !device || typeof device.device_id !== "string" || !/^[0-9a-f-]{36}$/.test(device.device_id) ||
        typeof device.device_name !== "string" || device.device_name.length > 48)) {
        throw new Error("Сервер повернув некоректний список телефонів.");
      }
      list.replaceChildren();
      if (payload.devices.length === 0) {
        const empty = document.createElement("p");
        empty.className = "field-help";
        empty.textContent = "Підключених телефонів немає.";
        list.appendChild(empty);
      }
      for (const device of payload.devices) {
        const row = document.createElement("div");
        row.className = "device-row";
        const label = document.createElement("span");
        label.textContent = device.device_name;
        const revoke = document.createElement("button");
        revoke.type = "button";
        revoke.className = "button button-quiet";
        revoke.textContent = "Відкликати";
        revoke.addEventListener("click", () => { void revokeDevice(device.device_id, revoke); });
        row.append(label, revoke);
        list.appendChild(row);
      }
      notice.textContent = "";
    } catch (error) {
      if (requestEpoch === deviceListEpoch && document.visibilityState !== "hidden") {
        notice.textContent = error instanceof Error ? error.message : "Не вдалося завантажити список телефонів.";
      }
    } finally {
      timeout.clear();
    }
  }

  async function revokeDevice(deviceId, button) {
    if (deviceMutationInFlight || document.visibilityState === "hidden") return;
    deviceMutationInFlight = true;
    const operationEpoch = uiEpoch;
    const notice = document.getElementById("devicesNotice");
    const timeout = timeoutSignal(3000);
    button.disabled = true;
    notice.textContent = "";
    try {
      const response = await fetch("/api/revoke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId }),
        cache: "no-store",
        signal: timeout.controller.signal,
      });
      if (operationEpoch !== uiEpoch || document.visibilityState === "hidden") return;
      if (!response.ok) throw new Error("Не вдалося відкликати доступ цього телефону.");
      const result = await response.json();
      if (operationEpoch !== uiEpoch || document.visibilityState === "hidden") return;
      if (!result || result.revoked !== true) throw new Error("Телефон уже був відключений або не знайдений.");
      notice.textContent = "Доступ телефону відкликано.";
      await loadDevices();
    } catch (error) {
      if (operationEpoch === uiEpoch && document.visibilityState !== "hidden") {
        notice.textContent = error instanceof Error ? error.message : "Не вдалося відкликати доступ цього телефону.";
      }
    } finally {
      timeout.clear();
      deviceMutationInFlight = false;
      button.disabled = false;
    }
  }

  async function loadLocalControl() {
    const requestEpoch = ++controlEpoch;
    const timeout = timeoutSignal(3000);
    try {
      const response = await fetch("/api/control", { method: "GET", cache: "no-store", signal: timeout.controller.signal });
      if (requestEpoch !== controlEpoch || document.visibilityState === "hidden") return;
      if (!response.ok) {
        setConnection("Режим перегляду", Boolean(currentSnapshot && currentSnapshot.available));
        return;
      }
      const control = await response.json();
      if (requestEpoch !== controlEpoch || document.visibilityState === "hidden") return;
      if (!control || !isValidToken(control.viewer_token) || typeof control.instance_id !== "string" || !control.instance_id) return;
      lanEnabled = typeof control.lan_enabled === "boolean" ? control.lan_enabled : null;
      if (!elements.lanIp.value.trim()) {
        const addresses = Array.isArray(control.lan_addresses) ? control.lan_addresses.filter(isPrivateIpv4) : [];
        let saved = "";
        try { saved = window.localStorage.getItem("agent_beacon_lan_ipv4") || ""; } catch (_) { /* Storage is optional. */ }
        elements.lanIp.value = addresses.includes(saved) ? saved : addresses[0] || (isPrivateIpv4(saved) ? saved : "");
      }
      elements.localPanel.hidden = false;
      document.getElementById("pairingPanel").hidden = false;
      elements.lanStatus.textContent = lanEnabled === true
        ? "Сервер слухає локальну мережу. Для доступу телефон і комп’ютер мають бути в одній Wi‑Fi мережі."
        : lanEnabled === false
          ? "Зараз доступ лише цьому ПК. Увімкніть LAN-доступ у локальних налаштуваннях Agent Beacon."
          : "LAN-доступ не підтверджено. Перевірте локальні налаштування Agent Beacon перед підключенням телефона.";
      const instanceChanged = Boolean(lastControlInstance && lastControlInstance !== control.instance_id);
      if (instanceChanged) {
        abortPoll();
        currentSnapshot = null;
        expiresAt = 0;
        setUnavailable("Комп’ютер перезапустився. Очікуємо свіжий сигнал.");
      }
      lastControlInstance = control.instance_id;
      if (instanceChanged || !viewerToken || !currentSnapshot) {
        if (viewerToken !== control.viewer_token) abortPoll();
        viewerToken = control.viewer_token;
        try { window.sessionStorage.setItem(TOKEN_STORAGE_KEY, viewerToken); } catch (_) { /* The in-memory token still works for this page. */ }
        elements.tokenRecovery.hidden = true;
        if (!currentSnapshot) {
          setUnavailable("Очікуємо свіжий сигнал від комп’ютера.");
          setConnection("Підключення…", false);
        }
      }
      void loadDevices();
      if (document.visibilityState !== "hidden") schedulePoll(0);
    } catch (_) {
      // A remote phone is expected to be denied access to this local-only endpoint.
      if (!currentSnapshot || !currentSnapshot.available) setConnection("Режим перегляду", false);
    } finally {
      timeout.clear();
    }
  }

  function buildViewerUrl() {
    requireLanAccess();
    const ip = validatedLanIp();
    if (!viewerToken) throw new Error("Ключ перегляду ще не отримано. Відкрийте панель на комп’ютері.");
    const port = window.location.port ? `:${window.location.port}` : "";
    return `http://${ip}${port}/#token=${encodeURIComponent(viewerToken)}`;
  }

  function validatedLanIp() {
    const ip = elements.lanIp.value.trim();
    if (!isPrivateIpv4(ip)) throw new Error("Введіть приватну IPv4-адресу комп’ютера з діапазону 10.x, 172.16–31.x або 192.168.x.");
    try { window.localStorage.setItem("agent_beacon_lan_ipv4", ip); } catch (_) { /* No tokens are stored here. */ }
    return ip;
  }

  function isPrivateIpv4(ip) {
    if (typeof ip !== "string") return false;
    const parts = ip.split(".");
    const validIp = parts.length === 4 && parts.every((part) => /^(0|[1-9]\d{0,2})$/.test(part) && Number(part) <= 255);
    const octets = validIp ? parts.map(Number) : [];
    const privateIp = validIp && (
      octets[0] === 10 ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
      (octets[0] === 192 && octets[1] === 168)
    );
    return privateIp;
  }

  function requireLanAccess() {
    if (lanEnabled === false) throw new Error("Зараз доступ лише цьому ПК. Увімкніть LAN-доступ у локальних налаштуваннях Agent Beacon.");
    if (lanEnabled !== true) throw new Error("LAN-доступ не підтверджено. Перевірте локальні налаштування Agent Beacon.");
  }

  function buildApkUrl() {
    requireLanAccess();
    const ip = validatedLanIp();
    const port = window.location.port || "80";
    return `http://${ip}:${port}/downloads/AgentBeacon-debug.apk`;
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (_) {
        // Fall back to selecting the link for manual copy.
      }
    }
    const input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    const copied = document.execCommand("copy");
    input.remove();
    return copied;
  }

  function paintQr(canvas, container, payload) {
    if (typeof qrcode !== "function") throw new Error("Локальний QR-генератор недоступний.");
    const qr = qrcode(0, "M");
    qr.addData(payload, "Byte");
    qr.make();
    const modules = qr.getModuleCount();
    const quietZone = 4;
    const cellSize = 5;
    const dimension = (modules + quietZone * 2) * cellSize;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Браузер не підтримує canvas для QR-коду.");
    canvas.width = dimension;
    canvas.height = dimension;
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, dimension, dimension);
    context.fillStyle = "#111820";
    for (let row = 0; row < modules; row += 1) {
      for (let column = 0; column < modules; column += 1) {
        if (qr.isDark(row, column)) {
          context.fillRect((column + quietZone) * cellSize, (row + quietZone) * cellSize, cellSize, cellSize);
        }
      }
    }
    container.hidden = false;
  }

  function paintNativeQr(payload) {
    paintQr(elements.nativeQrCanvas, elements.nativeQr, payload);
  }

  function clearNativePairing(expired = false) {
    if (nativeCodeTimer !== null) window.clearTimeout(nativeCodeTimer);
    if (nativeCodeInterval !== null) window.clearInterval(nativeCodeInterval);
    nativeCodeTimer = null;
    nativeCodeInterval = null;
    elements.nativeQr.hidden = true;
    elements.nativeQrCanvas.width = 0;
    elements.nativeQrCanvas.height = 0;
    elements.pairingLinkOutput.value = "";
    elements.pairingLinkOutput.hidden = true;
    elements.pairingLinkLabel.hidden = true;
    if (expired) elements.qrExpiresNotice.textContent = "Код минув. Створіть новий одноразовий QR-код.";
  }

  async function copyPairingLink() {
    elements.pairingNotice.textContent = "";
    try {
      const url = buildViewerUrl();
      const copied = await copyText(url);
      if (copied) {
        elements.pairingLinkOutput.hidden = true;
        elements.pairingLinkLabel.hidden = true;
        elements.pairingNotice.textContent = "Посилання скопійовано. Відкрийте його на телефоні в тій самій Wi‑Fi мережі.";
      } else {
        elements.pairingLinkOutput.value = url;
        elements.pairingLinkOutput.hidden = false;
        elements.pairingLinkLabel.hidden = false;
        elements.pairingLinkOutput.focus();
        elements.pairingLinkOutput.select();
        elements.pairingNotice.textContent = "Посилання виділено. Скопіюйте його вручну.";
      }
    } catch (error) {
      elements.pairingNotice.textContent = error instanceof Error ? error.message : "Не вдалося створити посилання.";
    }
  }

  async function createNativePairingLink() {
    if (nativePairingInFlight || document.visibilityState === "hidden") return;
    nativePairingInFlight = true;
    const requestEpoch = ++nativePairingEpoch;
    const createButton = document.getElementById("createNativePairing");
    createButton.disabled = true;
    elements.pairingNotice.textContent = "";
    try {
      const ip = elements.lanIp.value.trim();
      requireLanAccess();
      buildViewerUrl(); // validates the RFC1918 IPv4 address and local viewer state
      const requestStartedAt = performance.now();
      const response = await fetch("/api/pairing", {
        method: "POST", cache: "no-store",
        headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (requestEpoch !== nativePairingEpoch || document.visibilityState === "hidden") return;
      if (!response.ok) throw new Error("Не вдалося створити код. Відкрийте панель на комп’ютері Beacon.");
      const payload = await response.json();
      if (requestEpoch !== nativePairingEpoch || document.visibilityState === "hidden") return;
      if (!payload || typeof payload.code !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(payload.code)
          || payload.expires_in_seconds !== 120) {
        throw new Error("Сервер повернув некоректний код.");
      }
      const expiresAt = requestStartedAt + 120_000;
      const remainingMs = Math.max(0, expiresAt - performance.now());
      if (remainingMs <= 0) throw new Error("Код минув під час створення. Спробуйте ще раз.");
      const port = window.location.port || "80";
      const link = `agentbeacon://pair?host=${encodeURIComponent(ip)}&port=${encodeURIComponent(port)}#code=${payload.code}`;
      clearNativePairing();
      const copied = await copyText(link);
      if (requestEpoch !== nativePairingEpoch || document.visibilityState === "hidden") return;
      const timeLeft = Math.max(0, expiresAt - performance.now());
      if (timeLeft <= 0) {
        clearNativePairing(true);
        elements.pairingNotice.textContent = "Код минув під час копіювання. Створіть новий одноразовий QR-код.";
        return;
      }
      elements.pairingLinkOutput.value = link;
      elements.pairingLinkOutput.hidden = false;
      elements.pairingLinkLabel.hidden = false;
      if (!copied) { elements.pairingLinkOutput.focus(); elements.pairingLinkOutput.select(); }
      let qrRendered = false;
      try {
        paintNativeQr(link);
        qrRendered = true;
        elements.qrExpiresNotice.textContent = `Одноразовий код · залишилося ${formatCodeTime(remainingMs)} · його можна використати один раз.`;
      } catch (qrError) {
        elements.pairingNotice.textContent = qrError instanceof Error
          ? `${qrError.message} Посилання можна скопіювати вручну.`
          : "QR-код недоступний. Посилання можна скопіювати вручну.";
      }
      nativeCodeTimer = window.setTimeout(() => clearNativePairing(true), timeLeft);
      nativeCodeInterval = window.setInterval(() => {
        const remaining = Math.max(0, expiresAt - performance.now());
        elements.qrExpiresNotice.textContent = `Одноразовий код · залишилося ${formatCodeTime(remaining)} · його можна використати один раз.`;
      }, 1000);
      if (qrRendered) {
        elements.pairingNotice.textContent = `Одноразове посилання ${copied ? "скопійовано" : "виділено для копіювання"}. Воно діє 2 хвилини.`;
      }
    } catch (error) {
      if (requestEpoch === nativePairingEpoch && document.visibilityState !== "hidden") {
        elements.pairingNotice.textContent = error instanceof Error ? error.message : "Не вдалося створити код.";
      }
    } finally {
      if (requestEpoch === nativePairingEpoch) {
        nativePairingInFlight = false;
        createButton.disabled = false;
      }
    }
  }

  function showApkQr() {
    elements.pairingNotice.textContent = "";
    try {
      const url = buildApkUrl();
      paintQr(elements.apkQrCanvas, elements.apkQr, url);
      elements.apkUrlOutput.textContent = url;
    } catch (error) {
      elements.apkQr.hidden = true;
      elements.apkUrlOutput.textContent = "";
      elements.pairingNotice.textContent = error instanceof Error ? error.message : "Не вдалося створити QR для завантаження.";
    }
  }

  function validateManualInput() {
    const agentId = document.getElementById("agentId").value.trim();
    const sessionId = document.getElementById("sessionId").value.trim();
    if (!isSafeIdentifier(agentId) || !isSafeIdentifier(sessionId)) {
      throw new Error("ID мають містити 1–64 символи: латиницю, цифри, крапку, дефіс або підкреслення.");
    }
    return { agentId, sessionId };
  }

  function nextSequence(agentId, sessionId) {
    const latest = currentSnapshot?.sessions
      .filter((item) => item.agent_id === agentId && item.session_id === sessionId)
      .reduce((maximum, item) => Math.max(maximum, item.sequence), 0) || 0;
    const sequence = latest + 1;
    if (!Number.isSafeInteger(sequence)) throw new Error("Для цієї сесії вичерпано лічильник сигналів.");
    return sequence;
  }

  function setMutationPending(pending) {
    mutationInFlight = pending;
    document.getElementById("setState").disabled = pending;
    document.getElementById("clearSessions").disabled = pending;
  }

  async function sendManualState() {
    if (mutationInFlight || document.visibilityState === "hidden") return;
    elements.controlNotice.textContent = "";
    let ids;
    let sequence;
    try {
      ids = validateManualInput();
      sequence = nextSequence(ids.agentId, ids.sessionId);
    } catch (error) {
      elements.controlNotice.textContent = error instanceof Error ? error.message : "Перевірте поля.";
      return;
    }
    setMutationPending(true);
    const operationEpoch = uiEpoch;
    abortPoll();
    const timeout = timeoutSignal(3000);
    mutationController = timeout.controller;
    const requestStartedAt = performance.now();
    try {
      const response = await fetch("/api/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: ids.agentId, session_id: ids.sessionId, state: document.getElementById("manualState").value, sequence }),
        cache: "no-store",
        signal: timeout.controller.signal,
      });
      if (operationEpoch !== uiEpoch || document.visibilityState === "hidden") return;
      if (response.status === 409) {
        elements.controlNotice.textContent = "Сигнал змінився в іншій вкладці. Оновлюємо дані — спробуйте ще раз.";
        schedulePoll(0);
        return;
      }
      if (response.status === 401 || response.status === 403) {
        elements.controlNotice.textContent = "Керування доступне лише на комп’ютері, де запущено Agent Beacon.";
        return;
      }
      if (!response.ok) throw new Error("Сервер не прийняв тестовий сигнал.");
      const snapshot = await response.json();
      if (operationEpoch !== uiEpoch || document.visibilityState === "hidden") return;
      if (!applySnapshot(snapshot, requestStartedAt)) throw new Error("Сервер повернув некоректний стан.");
      elements.controlNotice.textContent = "Тестовий сигнал оновлено.";
      failureCount = 0;
    } catch (error) {
      if (operationEpoch === uiEpoch && document.visibilityState !== "hidden") {
        elements.controlNotice.textContent = error instanceof Error ? error.message : "Не вдалося оновити сигнал.";
        setUnavailable("Очікуємо підтвердження від сервера.");
      }
    } finally {
      timeout.clear();
      if (mutationController === timeout.controller) mutationController = null;
      setMutationPending(false);
      if (operationEpoch === uiEpoch && document.visibilityState !== "hidden") schedulePoll(150);
    }
  }

  async function clearTestSessions() {
    if (mutationInFlight || document.visibilityState === "hidden") return;
    elements.controlNotice.textContent = "";
    setMutationPending(true);
    const operationEpoch = uiEpoch;
    abortPoll();
    const timeout = timeoutSignal(3000);
    mutationController = timeout.controller;
    const requestStartedAt = performance.now();
    try {
      const response = await fetch("/api/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        cache: "no-store",
        signal: timeout.controller.signal,
      });
      if (operationEpoch !== uiEpoch || document.visibilityState === "hidden") return;
      if (!response.ok) throw new Error("Не вдалося очистити тестові сесії.");
      const snapshot = await response.json();
      if (operationEpoch !== uiEpoch || document.visibilityState === "hidden") return;
      if (!applySnapshot(snapshot, requestStartedAt)) throw new Error("Сервер повернув некоректний стан.");
      elements.controlNotice.textContent = "Тестові сесії очищено.";
    } catch (error) {
      if (operationEpoch === uiEpoch && document.visibilityState !== "hidden") {
        elements.controlNotice.textContent = error instanceof Error ? error.message : "Не вдалося очистити сесії.";
        setUnavailable("Очікуємо підтвердження від сервера.");
      }
    } finally {
      timeout.clear();
      if (mutationController === timeout.controller) mutationController = null;
      setMutationPending(false);
      if (operationEpoch === uiEpoch && document.visibilityState !== "hidden") schedulePoll(150);
    }
  }

  document.getElementById("copyLink").addEventListener("click", () => { void copyPairingLink(); });
  document.getElementById("createApkQr").addEventListener("click", showApkQr);
  document.getElementById("createNativePairing").addEventListener("click", () => { void createNativePairingLink(); });
  document.getElementById("saveViewerToken").addEventListener("click", () => {
    const token = elements.viewerTokenInput.value.trim();
    if (!setViewerToken(token)) elements.recoveryNotice.textContent = "Вставте ключ перегляду з посилання на комп’ютері.";
  });
  elements.viewerTokenInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") document.getElementById("saveViewerToken").click();
  });
  document.getElementById("setState").addEventListener("click", () => { void sendManualState(); });
  document.getElementById("clearSessions").addEventListener("click", () => { void clearTestSessions(); });
  document.getElementById("refreshDevices").addEventListener("click", () => { void loadDevices(); });

  document.addEventListener("visibilitychange", () => {
    uiEpoch += 1;
    abortPoll();
    if (document.visibilityState === "hidden") {
      deviceListEpoch += 1;
      nativePairingEpoch += 1;
      nativePairingInFlight = false;
      document.getElementById("createNativePairing").disabled = false;
      if (mutationController) mutationController.abort();
      if (viewerToken) {
        setUnavailable("Вкладка була неактивна. Очікуємо перевірку сигналу.");
        setConnection("Очікуємо сигнал", false);
      } else {
        showMissingTokenState();
      }
      return;
    }
    currentSnapshot = null;
    expiresAt = 0;
    if (viewerToken) {
      setUnavailable("Повернулися до вкладки. Перевіряємо свіжий сигнал.");
      setConnection("Перевіряємо сигнал", false);
    } else {
      showMissingTokenState();
    }
    void loadLocalControl();
    if (viewerToken) schedulePoll(0);
  });

  window.setInterval(expireIfNeeded, 250);
  setUnavailable(viewerToken ? "Очікуємо підключення до сервера." : MISSING_TOKEN_MESSAGE);
  if (!viewerToken) {
    elements.stateTitle.textContent = "Підключіть цей пристрій";
    elements.caption.textContent = "Потрібне посилання з комп’ютера";
  }
  setConnection(viewerToken ? "Підключення…" : "Очікуємо ключ", false);
  elements.tokenRecovery.hidden = Boolean(viewerToken);
  void loadLocalControl();
  if (viewerToken) schedulePoll(0);
})();
