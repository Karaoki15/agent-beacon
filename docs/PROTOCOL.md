# Локальний HTTP протокол

**Версія HTTP схеми:** 1. **Стан:** ручні HTTP маршрути лишаються demo contract; snapshot містить provider sessions і question-reminder projection. Provider lifecycle надходить у core через opt-in status-only local hook adapter; окремого публічного provider HTTP route немає. Hook configuration preview потребує явного встановлення та trust. Клієнт не передає prompt/response text, transcripts чи довільні metadata.

Windows 0.2.0-beta.1 додає локальний майстер. `GET /api/control` містить `setup_available`. `GET /api/setup` та `POST /api/setup/{plan,apply,startup,firewall,stop}` доступні тільки через loopback Host/client, з bearer-токеном саме локального viewer; токен телефона не підходить. Для POST обов’язковий matching Origin. План містить тільки paths/пояснення, opaque plan_id, прив’язку до вибору та snapshot поточних конфігурацій; діє 300 секунд і використовується один раз. Невідповідність конфігурацій вимагає нового перегляду. Мережа, автозапуск, правила firewall та імпорт у порожній реєстр телефонів мають окремі явні дії в майстрі. Деталі: [Windows beta](WINDOWS_BETA.md).

## Адресація та доступ

- Loopback режим за замовчуванням: `127.0.0.1:8765`.
- LAN bind вимагає явного запуску, наприклад `--host 0.0.0.0 --port 8765 --ttl 30 --question-reminder-seconds 120`; допустимий manual TTL — понад 0 і до 86400 секунд. Question reminder має окремий інтервал 0–86400 секунд, default 120; `0` вимикає тільки post-resume hold.
- `/api/control` дозволений лише коли клієнт підключився з loopback і в Host вказав loopback адресу або `localhost` на порту сервера. Він повертає секрет, тому його відповідь не можна журналювати чи кешувати; `lan_enabled` є лише підказкою, чи ввімкнений LAN bind.
- `/api/status` вимагає `Authorization: Bearer <viewer_token або paired device_token>` і повертає тільки snapshot. Native device token дозволяє лише читання status.
- `/api/events` має таку саму bearer-авторизацію і передає status snapshots як SSE (`data: <JSON>` плюс порожній рядок). Потік обмежений 10 секундами та вісьмома одночасними клієнтами; далі клієнт перепідключається. `X-Beacon-Battery-Saver: 1` зменшує частоту keep-alive з 4 до 8 секунд. Додаткове поле `stream_elapsed_ms` (0–10000) описує monotonic час очікування на сервері, щоб клієнт віднімав його, але враховував мережеву затримку у freshness lease. Відкликання device token закриває потік під час наступної перевірки. Запити без токена не займають event slots; перевищення ліміту повертає 503 для тимчасового fallback на `/api/status`.
- `/api/manual` та `/api/clear` дозволені тільки для loopback клієнта з Host на loopback і точним `Origin: http://<той самий host>:<порт>`.
- Статичні файли подаються лише з allowlist: /, /index.html, /styles.css, /app.js, /vendor/qr.js та /setup.html, /setup.css, /setup.js. Дані статусу без токена недоступні. Майстер працює лише на локальному комп’ютері.
- `GET /downloads/AgentBeacon-debug.apk` віддає тільки фіксований dist/AgentBeacon-debug.apk як APK attachment, максимум 32 MiB. Поточні версія та хеш записані в dist/BUILD-INFO.txt. QR завантаження не містить bearer-токена; одноразовий QR pairing є окремим кроком.
- `GET /downloads/AgentBeacon-Setup.exe` віддає тільки фіксований Windows beta setup, максимум 64 MiB: dist/windows/AgentBeacon-Setup.exe у source-запуску або ідентичну копію Uninstall-AgentBeacon.exe у встановленій папці frozen-програми. Довільні download paths не приймаються; обидва download endpoints публічні в межах доступної локальної мережі.
- Старий browser viewer link `http://192.168.1.10:8765/#token=<viewer_token>` підтримується для canonical RFC1918 IPv4 адрес. Нове native pairing видає одноразове посилання `agentbeacon://pair?host=192.168.1.10&port=8765#code=<43-character-base64url-code>`. Код живе 120 секунд і може бути використаний один раз. Детальний flow та приклади наведені в [PAIRING.md](PAIRING.md).
- `viewer_token` випадковий і чинний лише до завершення процесу. Native device token можна зберегти в opt-in `--device-store` registry у вигляді SHA-256 digest; такий токен переживає рестарт і залишається read-only. `instance_id` змінюється після рестарту. Локальні `/api/devices` і `/api/revoke` керують переліком paired devices.

LAN режим — це HTTP без TLS. Використовуйте його тільки у довіреній приватній мережі та не відкривайте порт в інтернет. Process-local browser viewer tokens стають недійсними після перезапуску сервера. Native device token залишається чинним до відкликання або втрати device store; за замовчуванням registry зберігається лише в пам’яті й очищується після restart, а `--device-store` зберігає token digests для відновлення paired devices.

## Схеми повідомлень

### `GET /api/control`

Лише loopback. Відповідь `200 application/json`:

```json
{
  "viewer_token": "<random per-process bearer token>",
  "instance_id": "<uuid>",
  "lan_enabled": false
}
```

`lan_enabled` reports whether the server was started with a non-loopback bind. Older clients may ignore it; the browser uses it only to decide whether to show the phone APK-download QR.

### `GET /api/status`

Потрібен bearer token. Відповідь `200 application/json`:

Для native paired client токен передається так само, як viewer token. Він дозволяє тільки цей GET route.

```json
{
  "schema_version": 1,
  "instance_id": "<uuid>",
  "revision": 2,
  "ttl_seconds": 30.0,
  "valid_for_seconds": 14.42,
  "available": true,
  "state": "working",
  "task_state": "working",
  "reason": null,
  "partial": false,
  "sessions": [
    {
      "agent_id": "demo-agent",
      "session_id": "demo-session",
      "source": "manual",
      "sequence": 1,
      "state": "working",
      "available": true,
      "updated_at": "2026-10-16T12:00:00.000Z"
    }
  ],
  "sources": [
    {"source": "codex", "healthy": true, "inventory_complete": true, "coverage": "observed_sessions"}
  ],
  "server_time": "2026-10-16T12:00:00.000Z"
}
```

`updated_at` і `server_time` — UTC ISO 8601 timestamp. `state` у верхньому рівні є display aggregate; `task_state` є aggregate фактичного lifecycle state до question-reminder overlay. `ttl_seconds` обмежує тільки manual demo signal. `valid_for_seconds` — найменший залишок freshness lease серед manual TTL та 15-second observer lease, обмежений `ttl_seconds`; він не завершує observed task. Question-reminder deadline є незалежним monotonic display timer; він не є новим task TTL чи observer heartbeat.

Observed session fields додають `turn_id`, `project_name`, `parent_session_id`, `active`, `display_state`, `question_reminder_active` і `question_reminder_remaining_seconds`. Для observed records `state` — фактичний session lifecycle state; `display_state` — стан після reminder projection. Manual records лишаються без reminder fields. `sources` містить `source`, `healthy`, `inventory_complete` та необов’язковий `coverage`; якщо поле відсутнє, це сумісно трактувати як `all_sessions`. `inventory_complete` повертається як false, коли observer lease протермінований.

`coverage` може бути `all_sessions` або `observed_sessions`. `all_sessions` — суворий режим: provider має reconciled inventory усіх сесій у своєму заявленому охопленні. `observed_sessions` визначає лише enrolled cohort: активні session rows, зареєстровані цим observer після запуску bridge. Це не inventory усіх процесів хоста. Порожній source не блокує інші джерела, але не створює ready самостійно; якщо жодне джерело не має відстежуваної активної сесії або іншого підтвердженого стану, aggregate unavailable. У відстежуваній cohort будь-яка active unknown, retired-generation активність, неповна reconciliation або втрата health блокує ready. Рестарт generation не приховує раніше відому active session: вона лишається unknown до явної reconciliation/закриття.

- Під час фактичного `awaiting_user` `display_state` лишається `awaiting_user`, навіть коли reminder deadline минув.
- Automatic/unknown resume з фактичним `working` може проєктуватись як `awaiting_user` до первісного question deadline. Нове `awaiting_user` оновлює deadline.
- Доведена відповідь користувача прибирає reminder і відображає фактичний working червоним. Неоднозначний resume не очищує deadline.
- Підтверджений `response_completed` відображає цю session як `ready` одразу; explicit Codex `Interrupt` on a known current turn maps to `turn_stopped` and also means ready for the next task. Green is not proof that the preceding work succeeded or was evaluated. Aggregate becomes green only with complete current inventory and no active unknown. Late events from an older turn are ignored; process loss, unknown turns and unhealthy observers remain dim.
- `question_reminder_active` позначає чинний post-resume hold; `question_reminder_remaining_seconds` — його залишок у snapshot. Для відсутнього/простроченого hold значення false/0. Поля описують UI projection, а не provider lifecycle transition.

Для нового процесу: `available=false`, `state=null`, `task_state=null`, `reason="unavailable"`, доки lifecycle state не зареєстрований і не reconciled. Після `POST /api/clear` видаляються тільки manual demo sessions; provider observations зберігаються. Свіжа manual active session може дати `partial=true` поруч зі stale manual session. Нездоровий source/session показується як unknown; інша окрема відома waiting/working session може лишатися видимою як partial. Будь-який active unknown або incomplete enrolled source блокує aggregate ready. Для `all_sessions` ready вимагає повного заявленого inventory; для `observed_sessions` ready стосується лише повністю reconciled enrolled cohort і не є твердженням про всі процеси хоста.

## Core in-process lifecycle API

Цей API викликається адаптером напряму у тому самому процесі. Він не є HTTP маршрутом і не створює автоматичне підключення до Codex чи Claude Code.

```python
core.register_source(source, generation, *, coverage="all_sessions")
core.update_source(source, generation, sequence, healthy, inventory_complete)
core.observe_session(
    source, generation, session_id, turn_id, event, sequence,
    project_name=None, parent_session_id=None, *, resume_reason="unknown",
)
```

- `source` — `codex` або `claude`. `generation` ідентифікує incarnation observer bridge для цього provider і змінюється тільки при рестарті bridge. Один bridge може відстежувати багато CLI sessions; старт/рестарт/закриття окремої сесії не ротують generation. Застаріла або нерегистрована generation відхиляється. `coverage` приймає `all_sessions` (default, зберігає сувору попередню семантику) або `observed_sessions` (тільки enrolled active cohort, без заяви про host-wide inventory).
- `update_source` оновлює transport/observer health і повноту session inventory; для одного generation source sequence має зростати. Observer lease — 15 секунд. Це лише контроль зв’язку з observer, не heartbeat агента чи task activity.
- `observe_session` приймає events `session_started`, `turn_started`, `awaiting_user`, `resumed`, `response_completed`, `turn_stopped`, `interrupted`, `session_closed`. Turn events вимагають `turn_id`; sequence зростає в межах session/generation. `resume_reason` — `unknown`, `automatic` або `user`; default `unknown` зберігає reminder, а `user` дозволений лише adapter-доказом конкретної людської відповіді.
- `session_started` лишає session у невідомому стані до першого актуально reconciled turn. Воно може повторно відкрити раніше closed chat ID за вищої sequence. `turn_started` і `resumed` означають task state `working`, `awaiting_user` означає фактичне waiting, `response_completed` завершує тільки цей turn, а `turn_stopped` завершує відомий поточний turn і показує ready for next task (не твердження про успіх). Втрата процесу під час активної або невизначеної роботи переводить стан у unknown; якщо terminal event уже підтвердив завершення поточного turn, подальший вихід worker сам по собі не скасовує ready. Загальний `interrupted` без підтвердженого terminal mapping також лишається unknown. Після питання per-session deadline за замовчуванням 180 секунд утримує лише display projection у yellow після automatic/unknown resume; фактичний waiting лишається yellow безстроково. Доведена human reply негайно знімає overlay. `session_closed` виключає session з активної агрегації.
- `session_id` і `turn_id` захищають від того, щоб late end event старого turn закрив новіший. `parent_session_id` є структурним зв’язком: child completion не завершує parent автоматично. Adapter має коректно відстежувати, який child turn належить parent turn, коли це доступно з офіційного джерела.
- `project_name` — basename довжиною до 160 символів без `/`, `\\`, `:`, control або format символів; заборонено передавати повний шлях, prompt, response, transcript чи довільні metadata.
- Після observer loss, неповної reconciliation або Agent Beacon server restart наявний стан стає unknown. Новий heartbeat сам не відновлює старий green: adapter має подати авторитетний актуальний lifecycle/resync для всього обраного coverage. У `all_sessions` це повний inventory; у `observed_sessions` — кожен відомий enrolled active session, включно з unresolved active rows попередньої generation. Restart/resume однієї CLI session не ротують bridge generation і не скидають інші sessions.
- Task activity не має TTL. Кількагодинна/нічна робота лишається working за наявності підтверджених lifecycle events та здорового observer; якщо спостерігач утрачає стан і не може reconcile, індикатор переходить в unknown/unavailable.

Question reminder не має HTTP налаштування чи provider event route. Його задає окрема CLI option `--question-reminder-seconds` (default `180`); manual demo `--ttl` та 15-second source observer lease мають інше призначення. `0` вимикає тільки жовте утримання після resume, але не фактичний waiting.

Snapshot `sessions` містить обидва типи даних. Manual items використовують `(agent_id, session_id)` та `source="manual"`; observed items використовують `(source, session_id)` і додають `turn_id`, safe `project_name`, `parent_session_id` та `active`.

### `POST /api/manual`

Loopback + exact same-origin. `Content-Type: application/json`. Body має містити рівно такі поля:

```json
{
  "agent_id": "demo-agent",
  "session_id": "demo-session",
  "state": "awaiting_user",
  "sequence": 2
}
```

- `agent_id`, `session_id`: 1–64 ASCII символи з набору `A-Z a-z 0-9 . _ -`.
- `state`: `working`, `awaiting_user` або `ready`.
- `sequence`: додатне safe integer до `9007199254740991`; JSON `true`/`false` не є допустимим sequence.
- `source` задається сервером як `manual`; клієнт не може змінювати його.
- Сесія має ключ `(agent_id, session_id)`. Вона свіжа протягом `ttl_seconds` після нового event.
- Послідовність нижче останньої та повтор із тим самим sequence, але відмінним вмістом дають `409`. Точний повтор останнього event є ідемпотентним; він не змінює revision чи час TTL.
- Успіх: `200` і новий snapshot.

### `POST /api/clear`

Loopback + exact same-origin, `Content-Type: application/json`, body рівно `{}`. Успіх: `200` і новий snapshot без manual demo sessions. Provider observations не очищуються. Revision збільшується, лише якщо сервер справді очистив demo records.

## Обмеження й помилки

- JSON body: максимум 8192 bytes, потрібен коректний `Content-Length`; дубльовані JSON keys, зайві/відсутні поля, нечислові константи й невірний UTF‑8 відхиляються.
- Максимум 128 сумарних manual/observed sessions, 16 provider sources і 64 retired generations на source; manual TTL і question reminder — до 86400 секунд. Provider task progress не має TTL. Core sessions живуть у пам’яті процесу. Лише при opt-in hook integrations локальна `.beacon/` містить bounded status-only event spool і sequence/runtime metadata; bridge не replay-ить попередні server-instance events після рестарту. Сервер починає unknown до нової reconciliation.
- До 32 одночасних TCP клієнтів, socket read timeout — 5 секунд.
- `400` — помилковий шлях, Host, заголовки чи JSON; `401` — відсутній/невірний bearer token; `403` — порушена loopback/same-origin межа; `404` — шлях поза allowlist; `409` — конфлікт sequence або переповнення ліміту сесій; `413` — body завеликий; `415` — неправильний Content-Type; `503` — досягнуто ліміт одночасних з’єднань.
- Дубльовані Host/Origin/Authorization/Content-Length або наявність Transfer-Encoding відхиляються.
- Довільні запити з query string, absolute-form URL, інший Host, шляховий обхід та зовнішні ресурси не підтримуються. Статична відповідь містить CSP, `nosniff` і `no-store`.
- Запити не потрапляють у access log, бо request path/header можуть містити credentials.

## Важлива відмінність станів

`ready` means the enrolled scope has no active turn and is ready for the next task; it does not assert that the preceding result succeeded or was evaluated, and it makes no host-wide inventory claim for `observed_sessions`. A completed turn or an explicit Codex interrupt for a known current turn can produce ready. A generic stop, process loss before a confirmed terminal event, unknown turn, or stale event cannot. Process loss during active or unresolved work remains unknown; a later worker exit does not revoke a previously confirmed terminal result. Parent/child completion still requires proper correlation. Session list and project basename remain safe metadata.
