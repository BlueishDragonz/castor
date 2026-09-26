# Castor — Feature Inventory

> Phase 1 deliverable. Two sources: live apollo container (legacy
> NiceGUI/Quasar), migration branch (`migration-to-shadcn-astro`).
> For each feature: who triggers it, where it lives now, what it
> does, what state it touches, what side-effects it has.

---

## A. Live (apollo) — legacy NiceGUI/Quasar

### A1. Routes — `@ui.page(...)` registrations

Source: `beaverhabits/routes/routes.py` (running container,
`/app/beaverhabits/routes/routes.py`, verified via `docker exec` +
`grep`). Total: 27 page routes + 2 endpoint routes.

| Route | Auth | Handler in repo | Purpose |
|---|---|---|---|
| `/demo` | none | `routes/routes.py:87` → `index_page_ui(days, habit_list)` | Session-storage demo (no DB) |
| `/demo/add` | none | `routes/routes.py:96` → `add_page_ui(habit_list)` | Session-storage demo add |
| `/demo/stats` | none | `routes/routes.py:103` → `stats_page_ui(today, habit_list)` | Session-storage demo stats |
| `/demo/order` | none | `routes/routes.py:111` → `order_page_ui(habit_list)` | Session-storage demo reorder |
| `/demo/habits/{habit_id}` | none | `routes/routes.py:118` → `habit_page_ui(today, habit)` | Session-storage demo detail |
| `/demo/habits/{habit_id}/streak` | none | `routes/routes.py:128` | Streak page redirect → heatmap |
| `/demo/habits/{habit_id}/heatmap` | none | `routes/routes.py:129` → `heatmap_page(today, habit)` | 53-week calendar heatmap |
| `/demo/completion-status` | none | `routes/routes.py:139` → `chip_sets_page()` | Demo chip set editor |
| `/demo/export` | none | `routes/routes.py:144` → `views.export_user_habit_list()` | Demo export download |
| `/gui` and `/` | user | `routes/routes.py:153-154` → `index_page_ui(days, habit_list)` | Home: 7-day grid + tags + streaks |
| `/gui/add` | user | `routes/routes.py:165` → `add_page_ui(habit_list)` | Add/list/star/delete habits |
| `/gui/stats` | user | `routes/routes.py:171` → `stats_page_ui(today, habit_list)` | Per-habit streak, date-range stats |
| `/gui/order` | user | `routes/routes.py:178` → `order_page_ui(habit_list)` | Drag-drop reorder |
| `/gui/habits/{habit_id}` | user | `routes/routes.py:184` → `habit_page_ui(today, habit)` | Detail: streak, history, heatmap, best streaks, notes |
| `/gui/habits/{habit_id}/streak` | user | `routes/routes.py:191` | Streak page → heatmap |
| `/gui/habits/{habit_id}/heatmap` | user | `routes/routes.py:192` → `heatmap_page(today, habit)` | 53-week heatmap (full) |
| `/gui/export` | user | `routes/routes.py:201` → `export_page(habit_list, user)` | JSON download + Telegram backup config + delete account |
| `/gui/import` | user | `routes/routes.py:210` → `import_ui_page(user)` | JSON/CSV upload → DictHabitList merge |
| `/settings` and `/gui/settings` | user | `routes/routes.py:215-216` → `settings_page(user)` | Theme + custom CSS + Help dialog trigger |
| `/gui/tokens` | user | `routes/routes.py:221` → `tokens_page(user)` | API token display/rotate |
| `/gui/security` | user | `routes/routes.py:226` → `security_page(user)` | Passkey list/add/remove + password change + recovery email |
| `/gui/completion-status` | user | `routes/routes.py:231` → `chip_sets_page(user)` | Per-habit completion status chip mapping |
| `/login` | none | `routes/routes.py:248` → NiceGUI login form | Email+password (Vanta.js bg) |
| `/register` | none | `routes/routes.py:655` → NiceGUI register form | Email+password ≥12 chars |
| `/reset-password` | token (reset-link) | `routes/routes.py:697` → NiceGUI reset form | Token-bound new password |
| `/assets` (POST) | user | `routes/routes.py:732` → `image_storage.save(file, user)` | Note image upload |
| `/assets/{image_id}` (GET) | user | `routes/routes.py:738` → `image_storage.get(image_id, user)` | Note image fetch |

### A2. Page → handler → business-logic trace

For each "important action" requested: the UI control → NiceGUI handler → business logic → storage → visible result.

#### A2.1 Daily tick (mark habit done/undone for a day)

- **UI**: `HabitCheckBox` cell in `frontend/index_page.py::habit_row(days)` → renders a checkbox per (habit, day) pair in the home grid.
- **Click**: NiceGUI `@ui.refreshable` callback `habit_list_ui.refresh` after toggling.
- **Handler**: `frontend/components.py::HabitCheckBox` calls `habit.tick(day, done, text)` directly on the Habit domain object (`storage/storage.py::Habit.tick`).
- **Business**: `Habit.tick(day, done, text)` mutates `habit.records` (list of `{day, done, text, timestamp}`), preserving latest text per date. Updates `habit_list.data` JSON blob.
- **Storage**: SQLite write to `habit_list.data` column via `user_storage` (one row per user).
- **Result**: page refresh re-renders the grid; `HabitCheckBox` shows ticked state for that day; streak recomputes on next render.
- **Side effect**: publishes `HabitListChanged(user_id, payload)` event → fans out to other connected WebSocket clients via `realtime.manager`.
- **Server endpoints**: equivalent REST: `POST /api/v1/habits/{id}/completions` body `{done, date, text, date_fmt}` → same `habit.tick` path. Mobile clients use this.

#### A2.2 Add habit

- **UI**: `frontend/add_page.py::add_page_ui(habit_list)` renders HabitAddButton + list of existing habits (with name input, star, delete).
- **Action**: `HabitAddButton.on_click` → `habit_list.add(name)` → returns the new habit id (6-char hex).
- **Storage**: SQLite `habit_list.data` JSON updated; new habit appended with empty `records: []`.
- **Result**: page refresh; new habit row appears in add page; navigate to `/gui` to see it in the grid.
- **Constraint**: `MAX_HABIT_COUNT` (default 5) enforced server-side in `views.get_or_create_user_habit_list` and at API level in `POST /api/v1/habits`.

#### A2.3 Login (email + password)

- **UI**: `frontend/components.py::auth_card(title, func)` → email + password inputs + "Sign in" button.
- **Action**: `views.login_user(user)` (in `frontend/components.py::auth_redirect`).
- **Backend**: `app/auth.py::user_create_token(user)` → `VersionedJWTStrategy.write_token(user)` → JWT with `{sub, aud, ver}` claims, signed with `JWT_SECRET`.
- **Storage**: JWT stored in `app.storage.user["auth_token"]` (NiceGUI's per-session encrypted storage) AND mirrored into `beaver_auth` cookie by `AuthMiddleware` in `routes/routes.py` (`init_gui_routes`).
- **Result**: redirect to `/gui`. Subsequent requests carry the JWT.
- **Note**: the route registration is `POST /auth/login` (not `/auth/jwt/login`). Mobile clients use `/auth/login` form-encoded.

#### A2.4 Login (passkey)

- **UI**: `webauthn_login_button` in `frontend/components.py` — triggered by JS in `security_passkeys.js`.
- **Browser**: `navigator.credentials.get({publicKey})` → returns assertion.
- **Backend**: `POST /auth/webauthn/login/begin` (form-encoded `username=email`) → server generates challenge → returns `PublicKeyCredentialRequestOptions` (JSON).
- **Browser**: submits assertion to `POST /auth/webauthn/login/complete`.
- **Backend**: verifies signature; if success, creates JWT and returns `{access_token, token_type}`. Same as password login from here on.
- **Cookie**: `beaver_webauthn` (browser-binding cookie) set on begin/complete; `_browser_cookie(request)` gates concurrent-tab ceremonies.
- **rpId**: declared as `localhost` in running container — mismatch with VPN origin (`10.8.0.1:8080`) per AUDIT-2026-09-23 and apollo-beaverhabits skill; known risk.

#### A2.5 Register

- **UI**: `frontend/components.py::auth_card(title=Sign up, func=try_register)`.
- **Handler**: `views.register_user(email, password)`:
  1. `user_create(email=email, password=password)` from `app/auth.py` (fastapi-users UserManager.create) — enforces ≥12-char password policy.
  2. `dummy_habit_list(30 days)` seeded.
  3. `get_or_create_user_habit_list(user, habit_list)` writes the seed list.
- **Auto-login**: `views.login_user(user)` immediately after registration.
- **Redirect**: `/gui`.
- **Constraint**: `MAX_USER_COUNT` (default unlimited when 0 or negative) enforced.

#### A2.6 Forgot password

- **UI**: `frontend/components.py::auth_forgot_password` link on `/login` and `/register`.
- **Handler**: `views.forgot_password(email)`:
  1. Email normalize + lowercase.
  2. `consume("recovery", "forgot_email:{email}", 1, 900)` — persistent rate-limit budget (one request per 15 min per email).
  3. If admitted and user is active: `user_create_reset_token(user)` → JWT with audience `RESET_PASSWORD`.
  4. SMTP via `asyncio.to_thread(send_email, …)` with reset link `${APP_URL}/reset-password?token=${token}`.
- **UI feedback**: `ui.notify(...)` — never discloses whether the email exists ("If the account can be recovered, …").

#### A2.7 Reset password

- **UI**: `/reset-password?token=…` → `frontend/components.py::auth_card(title=Reset password)`.
- **Handler**: `views.reset_password(user, password)`:
  1. `user_reset_password(user, password)` (fastapi-users).
  2. On success: `views.login_user(new_user)` → new JWT issued.
- **Note**: token is JWT, audience = reset. Token must be unused and unexpired (`RESET_PASSWORD_TOKEN_LIFETIME_SECONDS = 3600`).
- **NEW on castor (P3)**: 12-digit code flow at `/auth/reset-password` (castor fork's `reset_routes.py`); legacy reset-link path still exists at `/reset-password`.

#### A2.8 Add passkey

- **UI**: `frontend/security_page.py::security_page(user)` → "Add passkey" dialog → nickname input → JS triggers `navigator.credentials.create({publicKey})`.
- **Browser**: client navigates with attestation.
- **Backend**: `POST /auth/webauthn/register/begin` → server fetches existing credentials (excludes them from exclude list), generates challenge, stores in `ChallengeStore` (in-memory dict keyed by `session_id` + browser cookie `beaver_webauthn`).
- **Browser**: submits attestation to `POST /auth/webauthn/register/complete`.
- **Backend**: verifies; on success writes a row to `webauthn_credential` table (`id`, `user_id`, `credential_id`, `public_key`, `sign_count`, `transports`, `name`, `created_at`).
- **Result**: passkey list on `/gui/security` shows new entry.

#### A2.9 Remove passkey

- **UI**: passkey row menu → Remove → password confirm dialog.
- **Handler**: `app/security_actions.py::remove_passkey_credential(user_id, credential_id, current_password, expected_version)` — not a separate function, inlined; checks current password, then `DELETE /auth/webauthn/credentials/{credential_id}` (or `DELETE FROM webauthn_credential WHERE id=?`).
- **Last-key guard**: if removing the only credential, requires current password AND preserves a session (P3 hardening).

#### A2.10 Change password

- **UI**: dialog → current + new + confirm.
- **Handler**: `app/webauthn_routes.py::change_password` (P3 centralization: `app/security_actions.py::change_password(user_id, current_password, new_password, expected_version)`).
- **Backend**: re-validates current password, validates new ≥12 chars, updates hash AND increments `token_version` in same SQL UPDATE (`UserManager._update` CAS pattern).
- **Side effect**: all existing JWTs invalidated (their `ver` claim no longer matches DB).

#### A2.11 Daily notes

- **UI**: long-press on a HabitCheckBox cell → opens `frontend/components.py::habit_notes` (textarea) for that (habit, day).
- **Storage**: `habit.records[day].text` (per-day note string).
- **Limit**: `DAILY_NOTE_MAX_LENGTH` (default 1024 chars).
- **API**: `POST /api/v1/habits/{id}/completions` body includes `text` field.

#### A2.12 Export JSON

- **UI**: `/gui/export` → `frontend/export_page.py::export_panel(habit_list, user)` → "Export JSON" button → `views.export_user_habit_list(habit_list, user.email)`.
- **Handler**: serializes `habit_list.data` to JSON `{user_email, exported_at, habits: [...]}`, calls `ui.download(bytes, filename)`.
- **File name**: `beaverhabits_{YYYY_MM_DD}.json`.

#### A2.13 Import (JSON or CSV)

- **UI**: `/gui/import` → upload file → `frontend/import_page.py::handle_upload`.
- **Handler**: dispatches on extension: `import_from_json` or `import_from_csv` → returns `DictHabitList`.
- **Merge**: existing habits preserved unless name collision (then appended with numeric suffix); new habits added.
- **Storage**: `user_storage.save_user_habit_list(merged)` — atomic SQLite write.

#### A2.14 Drag-drop reorder

- **UI**: `/gui/order` → `frontend/order_page.py::order_page_ui(habit_list)` — Quasar drag-drop list.
- **Handler**: on drop, updates `habit_list.order` (list of habit_id strings) AND `habit_list.order_by = HabitOrder.MANUALLY`.
- **Storage**: SQLite write.
- **API equivalent**: `PUT /api/v1/habits/meta` body `{order: [...]}`.

#### A2.15 Drag-drop reorder + calendar heatmap

- **UI**: `/gui/habits/{id}/heatmap` → `frontend/streaks.py::streaks(today, habit)` — 53-week `CalendarHeatmap.build()` (full year of weeks).
- **UI**: `/gui/stats` → `frontend/stats_page.py::stats_page_ui(today, habit_list)` — per-habit streak + date-range picker.

#### A2.16 API token management

- **UI**: `/gui/tokens` → `frontend/tokens_page.py::tokens_page(user)` → shows API token (or "No token") + Copy / Rotate buttons.
- **Handler**: `crud.get_user_api_token(user)` reads encrypted token from `user_api_tokens` table; `crud.rotate_user_api_token(user)` issues a new one.
- **Encryption**: `TOKEN_ENCRYPTION_KEY` (Fernet) — token at rest is encrypted.

#### A2.17 Telegram backup

- **UI**: `/gui/settings` → `frontend/export_page.py::backup_panel(habit_list)` → "Backup" button opens `habit_backup_dialog`.
- **User config**: stores `telegram_bot_token` + `telegram_chat_id` in `user_configs` table.
- **Trigger**: `core/backup.py::backup_to_telegram(token, chat_id, habit_list)` — POSTs JSON to `https://api.telegram.org/bot{token}/sendDocument`.
- **Schedule**: `scheduler.py::daily_backup_task` runs daily at midnight if `ENABLE_DAILY_BACKUP=true`.

#### A2.18 Account deletion

- **UI**: `/gui/settings` → "Delete account" button → confirm dialog.
- **Handler**: `views.delete_user_account(user)`:
  1. `user_storage.delete_user_habit_list(user)` — deletes the habit_list row.
  2. `crud.delete_user_api_token(user)`.
  3. `crud.delete_user_identity(user.email)` — customer/Paddle link cleared.
  4. `crud.delete_user_owned_data(user)` — clears user_api_tokens, user_images, user_configs.
  5. `user_archive(user)` — keeps an anonymous tombstone row (email removed, is_active=False).
- **Note**: `app/security_actions.py` (P3 hardening) re-checks fresh-password authentication before delete.

#### A2.19 Login as superuser → admin page

- **UI**: `/gui/admin` (only reachable if `user.is_superuser == True`) → `frontend/admin.py::admin_page(user)`.
- **Functions**: list users + customers (Paddle subscribers), promote/demote a user to Pro via email.
- **Paddle**: `plan/plan.py` and `plan/paddle.py` — webhook integration for subscription state. Disabled by default (`ENABLE_PLAN=false`).

#### A2.20 Mobile bottom nav

- **UI**: `frontend/bottom_nav.py` — fixed 3-item bar (Home / Stats / More), CSS-gated to ≤640px width via `is_mobile(user_agent)`.
- **More sheet**: `frontend/more_sheet.py` — sectioned bottom sheet (Account / Data / Session).

### A3. Background jobs

| Job | Schedule | Trigger | Effect |
|---|---|---|---|
| `daily_integrity_task` | every 24h | `castor/main.py` lifespan | Runs `PRAGMA integrity_check` on `habits.db`; writes `habits.db.integrity.json` ledger |
| `audit_retention_task` | every 24h | same | Purges `audit_event` rows older than retention window |
| `daily_backup_task` | every 24h | same, only if `ENABLE_DAILY_BACKUP=true` | Per-user backup to Telegram if configured |
| Demo seed | startup | `castor.demo_seed.init_demo_seed_task()` (castor branch only; legacy has no equivalent) | Idempotent — creates `demo@castor.example.com` with 5 dummy habits |
| DB wipe mystery | intermittent | UNKNOWN on homelab2 dev DB | Wipes `demo@castor.example.com` user row only. Mitigated by startup hook + `/dev/reseed-demo`. Root cause not yet identified (AUDIT-2026-09-23 §4) |

### A4. State surfaces (where does state live?)

| State | Storage | Survives restart | Survives logout | Notes |
|---|---|---|---|---|
| User accounts | SQLite `user` table | yes | yes | — |
| Habit list | SQLite `habit_list.data` (one row per user, JSON blob) | yes | yes | Atomic writes via `user_storage` |
| Passkeys | SQLite `webauthn_credential` table | yes | yes | Encrypted at rest (Fernet? No — public key only) |
| Recovery email + state | SQLite `recovery_email_challenge` (castor only) | yes | yes | Not on running apollo |
| Audit log | SQLite `audit_event` (castor only) | yes | yes | Not on running apollo |
| API tokens | SQLite `user_api_tokens` (encrypted with `TOKEN_ENCRYPTION_KEY`) | yes | yes | — |
| User configs (theme, custom CSS, chips, backup settings) | SQLite `user_configs` (JSON blob) | yes | yes | — |
| Per-session JWT | `app.storage.user["auth_token"]` (NiceGUI) + `beaver_auth` cookie (httpOnly) | session-only | cleared on logout | JWT has `ver` claim; server rejects stale tokens |
| WebAuthn browser cookie | `beaver_webauthn` (cookie, 43-char token-URL-safe) | yes (max-age 30d) | cleared on logout | Gates concurrent-tab ceremonies |
| NiceGUI per-session state | `app.storage.user` (general dict) | session-only | cleared on logout | Used for STATS_START_DATE / STATS_END_DATE date range, default_chips, default_chips_mapping |
| Per-tab browser state | localStorage: `bh_emails` (last 5 emails), NiceGUI internals | yes | yes | — |
| Theme cookie | `castor-theme` (castor only) / NiceGUI in-memory dark mode flag | yes (cookie) | yes | — |
| NiceGUI dark mode | `fetch_user_dark_mode(client)` runs on connect; sets `app.storage.user["dark_mode"]` | session-only | n/a | Persisted by browser localStorage via NiceGUI client |

### A5. External integrations

| Integration | Endpoint | Auth | Direction | Rate limit | Config |
|---|---|---|---|---|---|
| Google One Tap | `POST /auth/google_one_tap_login` callback | OAuth | inbound | n/a | `GOOGLE_ONE_TAP_CLIENT_ID`, `GOOGLE_ONE_TAP_ENABLED`, `GOOGLE_ONE_TAP_CALLBACK_URL` |
| SMTP (reset email) | `asyncio.to_thread(send_email, ...)` | app password | outbound | n/a | `SMTP_EMAIL_USERNAME`, `SMTP_EMAIL_PASSWORD` |
| Telegram backup | `https://api.telegram.org/bot{token}/sendDocument` | per-user bot token | outbound | n/a | per-user `user_configs.telegram_bot_token`, `telegram_chat_id` |
| Paddle | `POST /paddle/webhook` (legacy `plan/paddle.py`) | PADDLE_CALLBACK_KEY | inbound + outbound | n/a | `PADDLE_SANDBOX`, `PADDLE_CLIENT_SIDE_TOKEN`, `PADDLE_API_TOKEN`, `PADDLE_PRODUCT_ID`, `PADDLE_PRICE_ID`, `PADDLE_CALLBACK_KEY` |
| Sentry | DSN | n/a | outbound | n/a | `SENTRY_DSN` |
| Umami analytics | `https://cloud.umami.is/script.js` | n/a | outbound (analytics) | n/a | `UMAMI_ANALYTICS_ID`, `UMAMI_SCRIPT_URL` |
| WebSocket sync | `/api/v1/sync/ws` (castor) / Engine.IO socket (legacy) | JWT or API token | bidirectional | per-user + per-IP | in-process pub-sub via `castor.realtime.manager` |
| Health probe | `GET /health` | none | inbound | n/a | — |
| Metrics (Prometheus) | `GET /metrics` | internal port only in prod | inbound | n/a | `metrics_app.py` |
| Auth login (web) | `POST /auth/login` (form) | username + password | inbound | per-IP `AUTH_RATE_IP_PER_MINUTE` (default 60) | — |
| Auth login (mobile) | same as web | same | inbound | same | — |
| WebAuthn begin/complete | `POST /auth/webauthn/{login,register}/{begin,complete}` | email + bearer | inbound | per-IP | rpId = `WEBAUTHN_RP_ID` |
| API | `GET/POST/PUT/DELETE /api/v1/*` | bearer JWT or API token | inbound | `API_RATE_IP_PER_MINUTE` (300) + `API_RATE_USER_PER_MINUTE` (120) | `api_user_limit` dep |

### A6. Live notifications / real-time updates

- **WebSocket fan-out**: `castor/realtime.py::manager` per-user broadcast. On `HabitListChanged` event, all sockets for that user (except sender) receive the change. Single gunicorn worker (default `-w 1`) → in-process broadcast, no broker needed.
- **Daily-change refresh**: `frontend/index_page.py::refresh_habit_list_when_today_changes` — `ui.timer(60, refresh_if_needed, immediate=False)` — re-renders the grid when the calendar day rolls over (for users in different timezones).
- **Browser sync**: `app.storage.user` cross-tab via NiceGUI's encrypted broadcast.
- **Notifications**: `ui.notify(message, type=positive|negative|warning)` — toasts in NiceGUI; equivalent Radix `Toast`/Alert in Astro (not yet implemented on the migration branch).

---

## B. Migration branch — Astro + shadcn

### B1. Astro pages (35 total)

| Route | File | Auth | Mirrors legacy | Status |
|---|---|---|---|---|
| `/` | `index.astro` | n/a | `/gui` redirect | ✅ |
| `/login` | `login.astro` | none | `/login` | ✅ (4-stage FSM: email → password → passkey offer → skip) |
| `/register` | `register.astro` | none | `/register` | ✅ (auto-login after) |
| `/forgot-password` | `forgot-password.astro` | none | `/forgot-password` (legacy has no page; forgot-password is a `notify` flow) | ✅ |
| `/verify-reset` | `verify-reset.astro` | code | new (staged reset) | ✅ (stage 1: code → stage 2: new password) |
| `/reset-password` | `reset-password.astro` | token | `/reset-password` (token-bound, legacy) | ✅ (less-used; verify-reset is preferred) |
| `/logout` | `logout.astro` | session | (legacy uses middleware redirect) | ✅ (POST → /auth/logout bumps token_version → clear cookies) |
| `/account/delete` | `account/delete.astro` | session | `/gui/settings` delete account | ✅ (password + typed "DELETE" confirm) |
| `/habits` | `habits/index.astro` | session | `/gui` | ✅ (7-day grid, sticky headers, tag filter, draw-sheet context menu) |
| `/habits/new` | `habits/new.astro` | session | `/gui/add` | ✅ |
| `/habits/[id]` | `habits/[id].astro` | session | `/gui/habits/{id}` | ✅ (streak, history, best streaks, heatmap, notes) |
| `/habits/[id]/edit` | `habits/[id]/edit.astro` | session | `/gui/add` edit mode | ✅ |
| `/habits/[id]/complete` | `habits/[id]/complete.astro` | session | HabitCheckBox tick | ✅ (server-side POST → backend) |
| `/habits/[id]/archive` | `habits/[id]/archive.astro` | session | menu → archive | ✅ |
| `/habits/[id]/duplicate` | `habits/[id]/duplicate.astro` | session | menu → duplicate | ✅ |
| `/habits/order` | `habits/order.astro` | session | `/gui/order` | ✅ (server-side POST → backend PUT /habits/meta) |
| `/stats` | `stats.astro` | session | `/gui/stats` | ✅ (per-habit streak cards + 15-week heatmap) |
| `/settings` | `settings.astro` | session | `/gui/settings` | ✅ (theme + custom CSS + Help + account actions) |
| `/security` | `security.astro` | session | `/gui/security` | ✅ (mounts SecurityContent React island) |
| `/import` | `import.astro` | session | `/gui/import` | ✅ (POST multipart; backend endpoint not yet shipped — honest 404 message) |
| `/export` | `export.astro` | session | `/gui/export` | ✅ (server-side GET → backend /api/v1/habits/export) |
| `/admin` | `admin.astro` | ADMIN_EMAIL | `/gui/admin` | ✅ (user list + backup trigger) |
| `/circles` | `circles/index.astro` | session | n/a (NEW) | ✅ (Private Circles list) |
| `/circles/new` | `circles/new.astro` | session | n/a | ✅ |
| `/circles/[id]` | n/a (single file) | session | n/a | ✅ (detail: members + shared habits + feed + invites) |
| `/circles/[id]/leave` | n/a | session | n/a | ✅ |
| `/circles/[id]/members/[user_id]/remove` | n/a | session | n/a | ✅ |
| `/circles/[id]/share` | n/a | session | n/a | ✅ |
| `/terms` | `terms.astro` | none | (no legacy equivalent) | ✅ (replaces inline `<footer>` from layout.py) |
| `/privacy` | `privacy.astro` | none | (no legacy equivalent) | ✅ |

### B2. BFF endpoints (17)

All under `web/concepts/src/pages/api/`. Pattern: read `castor_token` cookie → if present, attach as `Authorization: Bearer <token>` → fetch backend.

| Endpoint | Backend target | Method |
|---|---|---|
| `/api/v1/habits` | `/api/v1/habits` | GET, POST |
| `/api/v1/habits/[id]` | `/api/v1/habits/{id}` | GET, PATCH |
| `/api/v1/habits/export` | `/api/v1/habits/export` | GET |
| `/api/v1/habits/import` | `/api/v1/habits/import` | POST (backend endpoint not yet shipped) |
| `/api/v1/habits/reorder` | `/api/v1/habits/meta` | PUT |
| `/api/v1/habits/[id]/notes` | `/api/v1/habits/{id}/notes` | GET, POST |
| `/api/v1/habits/[id]/duplicate` | `/api/v1/habits/{id}` | POST (clone) |
| `/api/account/delete` | `/api/v1/account` | DELETE |
| `/api/admin/backup` | `/api/v1/admin/backup` | POST |
| `/api/auth/reset-password/check` | `/auth/reset-password/check` | POST |
| `/api/auth/webauthn/check` | `/auth/webauthn/check` | POST |
| `/api/auth/webauthn/offer/dismiss` | `/auth/webauthn/offer/dismiss` | POST |
| `/api/auth/webauthn/recovery-email` | `/auth/webauthn/recovery-email` | POST |
| `/api/auth/webauthn/recovery-email/verify` | `/auth/webauthn/recovery-email/verify` | POST |
| `/api/auth/webauthn/login/complete` | `/auth/webauthn/login/complete` | POST |
| `/api/auth/webauthn/register/begin` | `/auth/webauthn/register/begin` | POST |
| `/api/auth/webauthn/register/complete` | `/auth/webauthn/register/complete` | POST |

### B3. Backend API surface (Python)

Total: 33 endpoints (castor branch); 10 on main. Mirrors legacy plus new admin/circle/security-action routes. See `architecture-current.md` §2.1 for full table.

### B4. New features the migration branch adds (not in legacy)

1. **Private Circles** — group habits with select users; share tick/streak/notes visibility tiers; invites (link or email). Backend: 4 SQLAlchemy tables + 13 endpoints. Frontend: 7 Astro pages.
2. **Audit log** — every privileged action emits a row (`register`, `login`, `password_change`, `account_delete`, `passkey_register`, `passkey_delete`, `password_reset_request`, `password_reset`, `logout`, plus 9 circle events).
3. **Passkey offer screen** — after first password login, one-time offer to register a passkey; `passkey_offer_dismissed` on User.
4. **Recovery email** — `recovery_email` + `recovery_email_verified` columns; `recovery_email_challenge` table for 12-digit codes; back-end `send_recovery_code` + `verify_recovery_code`.
5. **Logout bumps `token_version`** — `/auth/logout` (castor addition) makes the JWT invalid server-side, not just cookie-cleared.
6. **Staged password reset** — `/verify-reset` proves the user can read their email before disclosing the new-password field (anti-enumeration).
7. **Persistent demo account** — `castor/demo_seed.py` idempotent seeder; `POST /dev/reseed-demo` endpoint for dev workflow.
8. **`security_actions.py` centralization** — all sensitive actions (password change, passkey delete, account delete) re-validate fresh password + token_version + sensitive-action rate-limit budget.
9. **Cookie-based WebAuthn browser binding** — `castor_webauthn_browser` cookie mirrored by Astro middleware; same-origin, httpOnly.
10. **Pure JS client-side passkey login** — `navigator.credentials.get` flow works in Astro with cookie bridge.

### B5. State on the migration branch (where state lives)

Same storage layer (SQLite `castor_data` volume); same JWT-in-cookie approach. Differences:
- Per-page state lives in `Astro.locals` (per-request) and `Astro.props` (per-page render). No NiceGUI per-session dict.
- `castor_token` httpOnly cookie + `castor_user` (visible) + `castor_webauthn_browser` cookies; no `app.storage.user`.
- PWA preferences live in cookies (`castor-theme`, `habit_show_streak`, etc.). Backend user-configs write endpoint is not yet shipped — these are read-only from the server's perspective on the migration branch.

### B6. Background jobs (same as legacy)

`daily_integrity_task`, `audit_retention_task`, `daily_backup_task`, `init_demo_seed_task` (new). Triggered on lifespan startup.

### B7. Migration branch — what is NOT yet done

| # | Item | Source | Impact |
|---|---|---|---|
| 1 | Backend `POST /api/v1/habits/import` endpoint | migration-plan.md P1 Import/Export | `/import.astro` surfaces honest 404 |
| 2 | Backend user-configs write endpoint | migration-plan.md P1 Settings | habit-display toggles read-only cookies; persistence not yet wired |
| 3 | Long-press notes-textarea on habit rows | migration-plan.md P2 polish | Notes only via habit detail page (not from grid cell long-press) |
| 4 | `beaverhabits/` → `castor/` package rename | migration-plan.md P3 decoupling | Cosmetic; affects only `pyproject.toml` + import paths |
| 5 | CI gate (`pnpm audit` + `astro check` + `pytest`) | migration-plan.md §1.4 | Reference config exists at plan level but `.github/workflows/security.yml` not committed |
| 6 | CI typecheck coverage for `web/concepts/docs/**` | none | Docs-only; not blocking |
| 7 | PWA manifest at `web/concepts/public/manifest.webmanifest` | Layout.astro references it | Need to create the file |
| 8 | Last-key passkey removal flow (preserves session) | security_actions.py partial | Last-key guard exists in code but flow UX not yet Astro-tested |
| 9 | Audit-event retention task verification | castor/app/audit.py | Tests don't cover retention behaviour |
| 10 | Migration branch's CircleHabit visibility server enforcement | circle_routes.py | Computed server-side but no test covers tier downgrade mid-session |

### B8. Migration branch — where things live (route → file → component)

```
/login → pages/login.astro
  └─ 4-stage FSM: StageSelector (in-page)
     stage 1: email input → POST /auth/login (password prompt)
     stage 2: password input → POST /auth/login → success
     stage 3: (if no passkeys) passkey offer screen → skip | add passkey
     stage 4: (after 1st login) main app
  └─ components referenced: Avatar (shadcn/ui/avatar), Button, Input

/habits → pages/habits/index.astro
  ├─ fetches /api/v1/habits + /api/v1/habits/{id} for each (server-side)
  ├─ <HabitGrid habits={...} days={...} ... />
  └─ <BottomNav /> (mobile) + <DesktopMenu /> (desktop) from Layout.astro

/habits/[id] → pages/habits/[id].astro
  ├─ fetches /api/v1/habits/{id}
  ├─ <Heatmap /> (15-week)
  ├─ <HabitNoteRow /> (per-day note list)
  ├─ best streak card (computed inline)
  └─ long-press → HabitNoteDialog (P2 pending — currently absent)

/security → pages/security.astro (22-line stub)
  └─ <SecurityContent client:load /> (React island, ~326 lines)
     ├─ fetchData(): passkeys list + recovery email
     ├─ handleAddPasskey(): navigator.credentials.create flow
     ├─ handleRemovePasskey(): password confirm + DELETE
     ├─ handleChangePassword(): current + new + confirm → /auth/webauthn/change-password
     ├─ handleAddRecoveryEmail(): send 12-digit code
     └─ handleVerifyRecoveryEmail(): enter code → verify
```

---

## C. Cross-cutting concerns (live + migration)

### C1. Authentication

- **Live**: JWT in `app.storage.user["auth_token"]` (encrypted) + mirrored `beaver_auth` cookie by middleware. `VersionedJWTStrategy` rejects mismatched `ver`.
- **Migration**: `castor_token` httpOnly cookie directly (no middleware mirroring needed); `castor_user` cookie for UI greeting; `castor_webauthn_browser` for ceremony binding. Same `VersionedJWTStrategy` on the backend.
- **Both**: `/auth/login` form-encoded; same `/auth/webauthn/login/{begin,complete}` endpoints.

### C2. Session lifetime

- `JWT_LIFETIME_SECONDS=2592000` (30 days) on running apollo.
- `castor_token` cookie `maxAge=60*60*24*7` (7 days) on Astro.

**MISMATCH** — if migration branch is deployed behind the same backend, cookie clears at 7d but JWT remains valid for 30d if presented. Cookie is the only way to identify the user in Astro, so this is fine; mobile clients presenting the JWT directly would not be affected. Worth noting in cutover plan.

### C3. CSRF

- **Live**: `BrowserOriginMiddleware` rejects cross-origin browser writes. Native clients (no `Origin` header) pass.
- **Migration**: same middleware on the backend; Astro pages are server-rendered so all writes go through cookies (no Origin header on cookie-bearing requests). Client-side fetch via `<script>` would be cross-origin and would be rejected unless `Origin: ${same}`.
- **Caveat**: Astro's `form.action="/habits/{id}/complete"` issues a same-origin POST with `Origin: http://host`. Backend accepts.

### C4. Rate limits

- `IPRateLimitMiddleware` — per-IP sliding window.
- `api_user_limit` — per-user sliding window for `/api/v1/*`.
- `consume(key, identity, count, window, sessions=…)` — async persistent budget; uses Redis only if configured.
- `sensitive-action` budget — shared by password change + passkey delete (P3).

### C5. Logging / observability

- `Digest` middleware logs `method path status duration`.
- `audit.append_audit_event(event, outcome, user_id)` for sensitive actions.
- Prometheus `/metrics` on internal port (legacy) or main port in dev.
- Sentry if `SENTRY_DSN` is set (currently unset on apollo).

---

## D. Data model diff

| Table | Main | Migration branch |
|---|---|---|
| `user` | base columns + `token_version` (P1 hardening) + `passkey_offer_dismissed` + `recovery_email` + `recovery_email_verified` | same |
| `habit_list` | `id, user_id, data JSON, created_at, updated_at` | same |
| `webauthn_credential` | `id, user_id, credential_id, public_key, sign_count, transports, name, created_at` | same |
| `user_api_tokens` | `id, user_id, encrypted_token, created_at` | same |
| `user_configs` | `user_id, css, default_chips, default_chips_mapping, telegram_bot_token, telegram_chat_id` | same |
| `user_images` | `id, user_id, blob, mime_type, created_at` | same |
| `customer` | Paddle link (disabled) | removed (Paddle dropped) |
| `password_reset_code` | legacy reset-link flow (token-bound) | same; castor adds `/auth/reset-password` (12-digit code) |
| `audit_event` | **MISSING** on running apollo | NEW (castor addition): `id, user_id, event, outcome, ip, user_agent, created_at` |
| `recovery_email_challenge` | **MISSING** | NEW (castor): `id, user_id, code_hash, expires_at, used_at` |
| `circle` | **MISSING** | NEW (castor P2): `id, owner_id, name, created_at` |
| `circle_member` | **MISSING** | NEW: `(circle_id, user_id, joined_at, is_owner)` |
| `circle_habit` | **MISSING** | NEW: `(circle_id, habit_id, owner_email, visibility, share_notes)` |
| `circle_invite` | **MISSING** | NEW: `(circle_id, token_hash, invited_email, delivery, expires_at, used_at)` |

Migration impact for cutover: jrodux's existing `habit_list.data` JSON is **already in the shape the castor branch expects** (DictHabitList serialised). The 6 new tables are additive — `create_db_and_tables` will create them on first start. **No destructive schema change required.** Existing webauthn_credential rows remain valid; rpId remains whatever the running container has configured (`localhost` on the current image).
