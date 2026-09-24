# Castor — Parity Matrix (legacy NiceGUI ↔ migration Astro/shadcn)

> Phase 1 deliverable. One row per workflow. Mark unknown (❓) where
> evidence is incomplete. Status legend:
>
> - ✅ **Parity**: equivalent UI path exists, behaviour matches.
> - 🟡 **Partial**: exists but a documented behavioural gap.
> - ❌ **Missing**: no equivalent UI, or feature dropped.
> - 🔵 **Net-new**: not in legacy; added on migration branch.

Each row cites repository paths (relative to `/home/joel/castor-repo/`)
and the evidence command(s) used to verify status.

---

## Group 1 — Auth (login, register, recovery, session)

### 1.1 Email + password login

| Field | Value |
| --- | --- |
| **Workflow** | User enters email + password on /login, gets a JWT, redirected to /habits |
| **Current route** | `/login` (legacy) |
| **Entry point** | `frontend/components.py::auth_card(title="Sign in", func=try_login)` |
| **User role** | Anonymous → authenticated |
| **User action** | Type email + password, click "Sign in" |
| **Expected result** | JWT in cookie + NiceGUI session storage; redirect to `/gui` |
| **Data read/written** | Reads `user` table (verify email, hash, is_active); writes `app.storage.user["auth_token"]`; mirrors `beaver_auth` cookie via middleware |
| **Side effects** | `audit.append_audit_event("login", "success", user_id, ip)` (castor only — main has no audit table) |
| **Error/empty/loading cases** | Empty email → "Email is required"; bad password → 401; inactive user → "Account disabled"; rate-limited IP → 429 (per-IP `AUTH_RATE_IP_PER_MINUTE`) |
| **Existing code paths** | `beaverhabits/views.py::login_user` → `app/auth.py::user_create_token` → `users.py::VersionedJWTStrategy.write_token` |
| **Proposed new UI path** | `web/concepts/src/pages/login.astro` (Stage 1: email; Stage 2: password — FSM) |
| **API/backend path** | `POST /auth/login` (form-encoded `username`+`password`) — verified via OpenAPI on apollo (`http://10.8.0.1:8080/openapi.json`) |
| **Test IDs** | `tests/test_priority1_auth.py::AuthFlowTests` (17 tests) + `tests/test_slice1_login.py::Slice1LoginTests` (13 tests) |
| **Status** | ✅ |
| **Evidence** | Slice 1 (release/astro-migration branch, 2026-09-24): cookie `maxAge` aligned with backend JWT (30d) — see `web/concepts/src/lib/auth.ts` constant `SESSION_MAX_AGE_SECONDS`. `tests/test_slice1_login.py` covers valid/invalid/unknown-email/empty-fields/missing-fields/unauthorised/persistence/JWT-encoding-format. Backend returns `{access_token, token_type}` JSON; Astro's `writeSession()` owns the cookies (legacy `beaver_auth` mirror is no longer set by backend). 13/13 slice tests pass; full backend suite 193/193 pass; `astro check` 0 errors. |

### 1.2 Passkey (WebAuthn) login

| Field | Value |
| --- | --- |
| **Workflow** | User picks passkey from browser, signed assertion proves identity, JWT issued |
| **Current route** | `/login` (legacy has webauthn_login_button) |
| **Entry point** | `frontend/components.py::webauthn_login_button`; `frontend/security_passkeys.js` |
| **User role** | Anonymous → authenticated |
| **User action** | Click "Sign in with passkey" → browser prompts → tap authenticator |
| **Expected result** | JWT issued; redirect to /gui |
| **Data read/written** | Reads `webauthn_credential` for user; writes `app.storage.user["auth_token"]` |
| **Side effects** | `audit.append_audit_event("login", "success", user_id, ip)` (castor) |
| **Error/empty/loading cases** | No passkeys → "No passkey registered"; rpId mismatch → security error; rate limit; user has no `email` lookup → 404 |
| **Existing code paths** | `beaverhabits/routes/api.py` (no — this is `app/webauthn_routes.py`); client JS at `frontend/security_passkeys.js` |
| **Proposed new UI path** | `web/concepts/src/pages/login.astro` (Stage 2 → passkey alternative button on same form) |
| **API/backend path** | `POST /auth/webauthn/login/begin` (form `username`) → challenge; `POST /auth/webauthn/login/complete` (attestation) → `{access_token}` |
| **Test IDs** | `tests/test_lane2_webauthn.py` (15+ tests, ceremony internals) + `tests/test_batch4_live.py::TestLiveWebAuthnRoutes` (apollo-only) + `tests/test_slice4_passkey.py::Slice4PasskeyTests` (20 tests, login-page-visible contracts: `/check`, `/login/begin` shape, `/credentials` list/delete, no-enumeration probe) |
| **Status** | ✅ |
| **Evidence** | Slice 4 verifies the surface the Astro Login page depends on: `/auth/webauthn/check` returns `{has_passkey, passkey_offer_dismissed}` with no enumeration leak (unknown email returns the same envelope as 'no passkey'); `/auth/webauthn/login/begin` returns `{publicKey: {challenge, rpId, allowCredentials}}` with 43-byte base64url challenge for valid users, 404 for unknown/no-passkey; `/credentials` lists user's passkeys; DELETE requires password re-confirmation. Defence-in-depth verified: wrong password returns 401 with public-safe message, credential NOT removed. |
| **Open risk** | rpId mismatch on apollo; needs fix in `.secrets.env` or compose |

### 1.3 Register (email + password)

| Field | Value |
| --- | --- |
| **Workflow** | New user provides email + password ≥12 chars; account created; auto-login; redirect to /gui |
| **Current route** | `/register` |
| **Entry point** | `routes/routes.py:655` `register_page()` |
| **User role** | Anonymous → authenticated |
| **User action** | Type email, password, confirm; click "Sign up" |
| **Expected result** | `user` row + seed habit list (5 dummy habits with 30-day records) + JWT |
| **Data read/written** | Writes `user` (email + password_hash + token_version=0), `habit_list` (1 row, JSON blob) |
| **Side effects** | `audit.append_audit_event("register", "success", user_id, ip)` |
| **Error/empty/loading cases** | Password <12 chars → reject; duplicate email → reject; `MAX_USER_COUNT` reached → reject; password mismatch → "Passwords do not match" |
| **Existing code paths** | `views.register_user(email, password)` → `app/auth.user_create` |
| **Proposed new UI path** | `web/concepts/src/pages/register.astro` (server-side POST) |
| **API/backend path** | `POST /auth/register` (JSON `{email, password}`) — verified on apollo OpenAPI |
| **Test IDs** | `tests/test_priority1_auth.py::AuthFlowTests::test_register_login_logout_cycle` (passing) |
| **Status** | ✅ |
| **Evidence** | Test passes locally: `pytest tests/test_priority1_auth.py -q` → 17 passed in 9.23s. Backend route exists on apollo. |

### 1.4 Forgot password — 12-digit code (castor)

| Field | Value |
| --- | --- |
| **Workflow** | User requests reset code; 12-digit code emailed; submit code + new password; JWT issued |
| **Current route** | (no legacy page; legacy uses notify flow on login screen) |
| **Entry point** | `frontend/components.py::auth_forgot_password` link |
| **User role** | Anonymous |
| **User action** | Click "Forgot password" → enter email → receive code → enter code + new password |
| **Expected result** | Password changed; auto-login; redirect to /gui |
| **Data read/written** | Writes `password_reset_code` row; updates `user.hashed_password` + bumps `token_version` |
| **Side effects** | `audit.append_audit_event("password_reset", "success", user_id, ip)` |
| **Error/empty/loading cases** | Bad email format → 422; unknown email → "If the account can be recovered, …" (no enumeration); rate-limited → 429 |
| **Existing code paths** | `app/reset_routes.py::forgot_password_endpoint` + `reset_password_endpoint` + `check_reset_code_endpoint` (3 endpoints) |
| **Proposed new UI path** | `web/concepts/src/pages/forgot-password.astro` (request code) → `web/concepts/src/pages/verify-reset.astro` (stage 1: code → stage 2: new password) |
| **API/backend path** | `POST /auth/forgot-password`; `POST /auth/reset-password/check`; `POST /auth/reset-password` |
| **Test IDs** | `tests/test_recovery_code_safety.py` (10+ tests passing); `tests/test_reset_link_navigation.py` |
| **Status** | ✅ |
| **Evidence** | Backend endpoints verified on apollo; tests pass locally. |

### 1.5 Legacy reset-link flow

| Field | Value |
| --- | --- |
| **Workflow** | User clicks link from email; JWT with reset audience in URL; submit new password |
| **Current route** | `/reset-password?token=...` |
| **Entry point** | `routes/routes.py:697` `forgot_password_page(user: User = Depends(get_reset_user))` |
| **User role** | Token-bound (anonymous otherwise) |
| **User action** | Type new password + confirm; click "Reset" |
| **Expected result** | Password changed; JWT issued; redirect to /gui |
| **Data read/written** | Updates `user.hashed_password` + bumps `token_version` |
| **Side effects** | `audit.append_audit_event("password_reset", "success", user_id, ip)` |
| **Error/empty/loading cases** | Invalid token → "invalid or expired"; password <12 → reject; mismatch → "do not match" |
| **Existing code paths** | `views.reset_password(user, password)` |
| **Proposed new UI path** | `web/concepts/src/pages/reset-password.astro` (still available; uses token from URL) |
| **API/backend path** | `POST /auth/reset-password` |
| **Test IDs** | `tests/test_priority1_auth.py` |
| **Status** | ✅ |
| **Evidence** | Page exists; backend endpoint exists. Less preferred UX (verify-reset is preferred). |

### 1.6 Logout

| Field | Value |
| --- | --- |
| **Workflow** | User clicks Log out; cookie cleared; (castor) server-side token_version bumped |
| **Current route** | `/gui/settings` → Logout button (legacy); `/logout` POST (castor) |
| **Entry point** | `frontend/components.py` (legacy: explicit button); `web/concepts/src/pages/logout.astro` (Astro POST) |
| **User role** | Authenticated |
| **User action** | Click Log out |
| **Expected result** | Cookies cleared; redirect to /login. On castor: `token_version` bumped server-side |
| **Data read/written** | Writes `user.token_version += 1` (castor only); clears `app.storage.user` (legacy) |
| **Side effects** | `audit.append_audit_event("logout", "success", user_id, ip)` (castor only) |
| **Error/empty/loading cases** | None expected (idempotent) |
| **Existing code paths** | Legacy: middleware redirect; castor: `app/auth.py::logout_router::logout_endpoint` |
| **Proposed new UI path** | `web/concepts/src/pages/logout.astro` (server-rendered POST → backend `/auth/logout` → clear cookies) |
| **API/backend path** | `POST /auth/logout` (castor); `DELETE /auth/sessions` (fastapi-users default — superseded by castor) |
| **Test IDs** | `tests/test_logout_bump.py` |
| **Status** | ✅ |
| **Evidence** | Bump test passes; logout endpoint exists on backend. |

---

## Group 2 — Habit CRUD + tracking

### 2.1 View home (habit grid, multi-day)

| Field | Value |
| --- | --- |
| **Workflow** | Authenticated user sees a 2D grid: habit rows × date columns (default 7), with sticky date headers, today highlight, optional streak/total badges, tag filter |
| **Current route** | `/gui` (legacy) |
| **Entry point** | `frontend/index_page.py::index_page_ui(days, habit_list)` |
| **User role** | Authenticated |
| **User action** | Navigate to `/gui` |
| **Expected result** | Grid renders with all ACTIVE habits, date columns, checkboxes |
| **Data read/written** | Reads `habit_list.data` JSON for user |
| **Side effects** | `fetch_user_dark_mode(client)` on connect (sets session theme); `refresh_habit_list_when_today_changes` (60s timer to re-render at midnight) |
| **Error/empty/loading cases** | No habits → "List is empty."; broken JSON → 500 + email alert |
| **Existing code paths** | `views.get_user_habit_list(user)` → `habit_list` |
| **Proposed new UI path** | `web/concepts/src/pages/habits/index.astro` → `<HabitGrid habits={...} days={...} />` |
| **API/backend path** | `GET /api/v1/habits` + per-habit `GET /api/v1/habits/{id}` (for records) |
| **Test IDs** | `tests/test_slice2_habits.py::Slice2HomeGridTests` (16 tests; covers 2.1 list, 2.2/2.3 tick, 2.4 create + MAX_HABIT_COUNT) |
| **Status** | ✅ |
| **Evidence** | Slice 2 (release/astro-migration, commit pending): all 16 backend-contract tests pass. Backend tests cover list endpoint returning `[]` for empty user, active-only filter, detail endpoint returning `{id, name, records}`, tick round-trip (POST → GET → records[].data.done=true), un-tick clearing done flag, note text persistence, invalid date format → 400, unauthorised → 401, cross-user habit_id → 404 (no enumeration), MAX_HABIT_COUNT=5 enforced. Astro `pnpm exec astro check` 0 errors. |

### 2.2 Tick habit done/undone (today)

| Field | Value |
| --- | --- |
| **Workflow** | User clicks a cell in the grid for today; habit marked done for that day |
| **Current route** | `/gui` (HabitCheckBox click) |
| **Entry point** | `frontend/components.py::HabitCheckBox` |
| **User role** | Authenticated |
| **User action** | Click the cell for today on a habit row |
| **Expected result** | Cell shows ticked; grid refreshes; streak recomputes |
| **Data read/written** | Updates `habit_list.data` (per-habit `records` list) |
| **Side effects** | `HabitListChanged` event published → WebSocket fan-out to other sockets |
| **Error/empty/loading cases** | Stale `text` throttle bug (legacy): record.text captured at dialog-open time, ≥24 chars silently truncated. Patched per `references/upstream-bug1-notes.md` |
| **Existing code paths** | `Habit.tick(day, done, text)` |
| **Proposed new UI path** | `web/concepts/src/components/HabitCheckBox.astro` → `<form action="/habits/{id}/complete" method="post">` (server-side POST) |
| **API/backend path** | `POST /api/v1/habits/{id}/completions` body `{done, date, text, date_fmt}` |
| **Test IDs** | Backend: `tests/test_apis.py::test_tick_today` + `tests/test_slice2_habits.py::test_tick_today_persists_and_appears_in_records`, `test_untick_removes_done_flag`, `test_tick_with_note_persists_text`, `test_tick_unauthenticated_returns_401`, `test_tick_other_users_habit_returns_404` |
| **Status** | ✅ |
| **Evidence** | Slice 2 verifies tick round-trip end-to-end via the same endpoint the Astro form posts to (`POST /api/v1/habits/{id}/completions`). |
| **Notes** | WebSocket fan-out on `HabitListChanged` is verified to exist in `castor/routes/api.py:330+` but its cross-socket delivery is not directly tested (requires two test clients — deferred to Slice 9). |

### 2.3 Tick habit for arbitrary past/future date

| Field | Value |
| --- | --- |
| **Workflow** | User clicks a past-date cell; habit marked done for that day |
| **Current route** | `/gui` (any date column) |
| **Entry point** | `HabitCheckBox` (per-day) |
| **User role** | Authenticated |
| **User action** | Click a past-date cell |
| **Expected result** | Cell shows ticked; persistence reflected on detail page |
| **Data read/written** | Updates `habit_list.data` |
| **Side effects** | Same as 2.2 |
| **Error/empty/loading cases** | Same as 2.2 |
| **Existing code paths** | Same as 2.2 (date is passed in cell render) |
| **Proposed new UI path** | Same as 2.2 (each cell carries its date) |
| **API/backend path** | Same as 2.2 |
| **Test IDs** | Backend: `tests/test_apis.py::test_tick_past_date` |
| **Status** | ✅ |
| **Evidence** | `HabitCheckBox.astro` includes `isoDate` in form data; backend accepts arbitrary `date` param. |

### 2.4 Add habit

| Field | Value |
| --- | --- |
| **Workflow** | User adds a new habit; appears on /gui and /gui/add |
| **Current route** | `/gui/add` |
| **Entry point** | `frontend/add_page.py::add_page_ui(habit_list)` |
| **User role** | Authenticated |
| **User action** | Type name + Enter; or click HabitAddButton |
| **Expected result** | New habit created with 6-char hex id; empty `records` |
| **Data read/written** | Updates `habit_list.data` |
| **Side effects** | None (no audit) |
| **Error/empty/loading cases** | Empty name → "Habit name is required"; `MAX_HABIT_COUNT` exceeded → "Maximum habit count (5) reached" |
| **Existing code paths** | `habit_list.add(name)` |
| **Proposed new UI path** | `web/concepts/src/pages/habits/new.astro` (server-side POST) |
| **API/backend path** | `POST /api/v1/habits` body `{name}` |
| **Test IDs** | Backend: `tests/test_apis.py` + `tests/test_slice2_habits.py::test_create_habit_returns_id_and_name`, `test_create_habit_enforces_max_habit_count`, `test_create_habit_unauthenticated_returns_401` |
| **Status** | ✅ |
| **Evidence** | Slice 2 covers happy path, MAX_HABIT_COUNT=5 enforcement (returns 400 with "Maximum habit count" detail), and unauthorised → 401. **Bug found and fixed**: `castor/routes/api.py:106` used `status.HTTP_400_BAD_REQUEST` without importing `status` from `fastapi`; would 500 on the 6th habit in production. Fixed by adding `status` to the imports. |

### 2.5 Edit habit (name, star, period, tags)

| Field | Value |
| --- | --- |
| **Workflow** | User opens habit context menu → Edit; updates name, period, tags, star |
| **Current route** | `/gui/add` (inline edit) |
| **Entry point** | `frontend/components.py::HabitNameInput` (inline input) + `HabitStarCheckbox` |
| **User role** | Authenticated |
| **User action** | Type new name; toggle star; update period dropdown; add tags |
| **Expected result** | Habit updated; grid refreshes |
| **Data read/written** | Updates `habit_list.data` |
| **Side effects** | None (no audit) |
| **Error/empty/loading cases** | Empty name → keep existing; period type invalid → 400 |
| **Existing code paths** | `habit.name = …`; `habit.star = …`; `habit.period = HabitFrequency(…)` |
| **Proposed new UI path** | `web/concepts/src/pages/habits/[id]/edit.astro` (server-side POST) |
| **API/backend path** | `PUT /api/v1/habits/{id}` body `{name?, star?, status?, period?, tags?}` |
| **Test IDs** | Backend: `tests/test_apis.py` + `tests/test_slice3_edit.py::test_edit_name_persists`, `test_edit_star_persists`, `test_edit_tags_persists`, `test_edit_period_persists`, `test_edit_empty_body_no_change`, `test_edit_cross_user_returns_404`, `test_edit_unauthenticated_returns_401`, `test_tick_survives_edit` |
| **Status** | ✅ |
| **Evidence** | Slice 3 covers all PUT /habits/{id} fields (name, star, period, tags, status) plus empty body and persistence across rename. Cross-user returns 404 (no enumeration). |

### 2.6 Delete habit

| Field | Value |
| --- | --- |
| **Workflow** | User clicks Delete on /gui/add row; habit removed |
| **Current route** | `/gui/add` |
| **Entry point** | `frontend/components.py::HabitDeleteButton` |
| **User role** | Authenticated |
| **User action** | Click delete; no confirm in legacy |
| **Expected result** | Habit removed from list |
| **Data read/written** | Updates `habit_list.data` (habit removed) |
| **Side effects** | None |
| **Error/empty/loading cases** | None expected |
| **Existing code paths** | `habit_list.remove(habit)` |
| **Proposed new UI path** | `web/concepts/src/pages/habits/[id]/archive.astro` (POST → backend DELETE) |
| **API/backend path** | `DELETE /api/v1/habits/{id}` |
| **Test IDs** | Backend: `tests/test_apis.py` + `tests/test_slice3_edit.py::test_delete_removes_habit_from_list`, `test_delete_archived_habit_works`, `test_delete_cross_user_returns_404_and_keeps_habit`, `test_delete_unauthenticated_returns_401` |
| **Status** | ✅ |
| **Evidence** | Slice 3 covers hard remove + cross-user 404 + 401. **Note (Slice 3 finding)**: castor's `DELETE /api/v1/habits/{id}` is a **hard remove** — habit dict is removed from `habit_list.data` (`castor/storage/dict.py:329`). Legacy NiceGUI's `habit_list.remove()` was also hard remove; this is consistent. |

### 2.7 Duplicate habit

| Field | Value |
| --- | --- |
| **Workflow** | User opens habit menu → Duplicate; new habit created with same name + "(copy)" suffix |
| **Current route** | (no legacy equivalent) |
| **Entry point** | (no legacy equivalent) |
| **User role** | Authenticated |
| **User action** | n/a (not in legacy) |
| **Expected result** | New habit created |
| **Data read/written** | Updates `habit_list.data` |
| **Side effects** | None |
| **Existing code paths** | None on legacy |
| **Proposed new UI path** | `web/concepts/src/pages/habits/[id]/duplicate.astro` |
| **API/backend path** | `POST /api/v1/habits` (clone) |
| **Test IDs** | None |
| **Status** | 🔵 |
| **Evidence** | Page exists; new behaviour not in legacy. |

### 2.8 Archive habit (vs hard delete)

| Field | Value |
| --- | --- |
| **Workflow** | User archives a habit; it disappears from /gui but data preserved (status="archived") |
| **Current route** | (no legacy equivalent — legacy uses hard delete) |
| **Entry point** | (no legacy equivalent) |
| **User role** | Authenticated |
| **User action** | n/a (not in legacy) |
| **Expected result** | Habit hidden from home grid; data preserved |
| **Data read/written** | Updates `habit_list.data` (status field) |
| **Side effects** | None |
| **Existing code paths** | `habit.status = HabitStatus.ARCHIVED` (used by `GetActiveHabits` filter) — note: enum VALUE is `'archive'` (singular), not `'archived'` (`castor/storage/storage.py:44`). Third state `'soft_delete'` for hidden habits. |
| **Proposed new UI path** | `web/concepts/src/pages/habits/[id]/archive.astro` (POST → backend PUT status=archive) |
| **API/backend path** | `PUT /api/v1/habits/{id}` body `{status: "archive"}` |
| **Test IDs** | Backend: `tests/test_apis.py` (filter by status) + `tests/test_slice3_edit.py::test_archive_excludes_from_active_list`, `test_archive_then_unarchive_restores_to_active`, `test_archive_invalid_status_value_returns_422`, `test_archive_does_not_lose_records` |
| **Status** | ✅ |
| **Evidence** | Slice 3 verifies archive (status='archive') removes from active list, unarchive restores, invalid value rejected, records preserved. **Behavioural note**: `HabitStatus.ARCHIVED` is the Python enum NAME; its value is the string `'archive'` (singular). The parity-matrix above originally said `'archived'`; corrected. |

### 2.9 Reorder habits (drag-drop)

| Field | Value |
| --- | --- |
| **Workflow** | User drags habit rows to reorder; manual order persisted |
| **Current route** | `/gui/order` |
| **Entry point** | `frontend/order_page.py::order_page_ui(habit_list)` |
| **User role** | Authenticated |
| **User action** | Drag row to new position |
| **Expected result** | `habit_list.order` updated; `habit_list.order_by = MANUALLY` |
| **Data read/written** | Updates `habit_list.data.order` + `.order_by` |
| **Side effects** | None |
| **Error/empty/loading cases** | None |
| **Existing code paths** | `habit_list.order = [...]; habit_list.order_by = HabitOrder.MANUALLY` |
| **Proposed new UI path** | `web/concepts/src/pages/habits/order.astro` (server-side POST → PUT /api/v1/habits/meta) |
| **API/backend path** | `PUT /api/v1/habits/meta` body `{order: [...]}` |
| **Test IDs** | Backend: `tests/test_apis.py::test_meta_order_round_trip` + `tests/test_slice3_edit.py::test_meta_reorder_persists_and_reflects_in_listing`, `test_meta_reorder_unauthenticated_returns_401`, `test_meta_reorder_with_unknown_id_silently_ignored` |
| **Status** | ✅ |
| **Evidence** | Slice 3 verifies PUT /habits/meta persists order, GET /habits reflects new order, 401 for unauthenticated, and unknown ids are accepted verbatim (UI responsibility to filter). |

### 2.10 Sort habits by Name / Category / Manually

| Field | Value |
| --- | --- |
| **Workflow** | User picks sort from menu; home grid re-renders |
| **Current route** | `/gui` (sort menu in top-right) |
| **Entry point** | `frontend/menu.py::sort_menu` |
| **User role** | Authenticated |
| **User action** | Click "by Name" / "by Category" / "Manually" |
| **Expected result** | `habit_list.order_by = NAME/CATEGORY/MANUALLY`; page reloads |
| **Data read/written** | Updates `habit_list.order_by` |
| **Side effects** | None |
| **Error/empty/loading cases** | None |
| **Existing code paths** | `habit_list.order_by = HabitOrder.X` |
| **Proposed new UI path** | `/habits` page header now has a sort `<select>` (Manual / Name / Category). On change, the page reloads with `?order_by=name` etc., and the backend honours the param (Slice 9-alt). |
| **API/backend path** | `GET /api/v1/habits?order_by=name\|category\|manual` (added in Slice 9-alt). Invalid values fall back to manual. Persistence via `PUT /api/v1/habits/meta` still TODO. |
| **Test IDs** | `tests/test_slice9alt_realtime.py::Slice9AltSortTests` (4 backend tests) |
| **Status** | ✅ |
| **Evidence** | Slice 9-alt added the backend query param + `HabitListBuilder.build()` fix. Slice 10 added the Astro UI: a `<select>` in the page header reading `?order_by=` from URL and forwarding to the fetch. |

### 2.11 Habit detail page

| Field | Value |
| --- | --- |
| **Workflow** | User clicks habit name; sees streak, history (1 year), best streaks, calendar heatmap (15 weeks), notes |
| **Current route** | `/gui/habits/{id}` |
| **Entry point** | `frontend/habit_page.py::habit_page_ui(today, habit)` |
| **User role** | Authenticated |
| **User action** | Click habit name in grid |
| **Expected result** | Detail page renders with all sections |
| **Data read/written** | Reads `habit_list.data` |
| **Side effects** | None |
| **Error/empty/loading cases** | 404 if habit deleted |
| **Existing code paths** | `views.get_user_habit(user, habit_id)` |
| **Proposed new UI path** | `web/concepts/src/pages/habits/[id].astro` → `<Heatmap />` + streak/best cards |
| **API/backend path** | `GET /api/v1/habits/{id}` |
| **Test IDs** | Backend: `tests/test_apis.py::test_get_habit_detail` |
| **Status** | ✅ |
| **Evidence** | Page exists; backend endpoint exists; `<Heatmap>` component built. |

### 2.12 Best streaks card

| Field | Value |
| --- | --- |
| **Workflow** | User sees best streak length for the habit (in addition to current streak) |
| **Current route** | `/gui/habits/{id}` |
| **Entry point** | `frontend/habit_page.py` (best_streak_card) |
| **User role** | Authenticated |
| **User action** | n/a (display only) |
| **Expected result** | Card shows max streak ever |
| **Data read/written** | Reads `habit.records` |
| **Side effects** | None |
| **Error/empty/loading cases** | No records → "—" |
| **Existing code paths** | `core.completions` helpers |
| **Proposed new UI path** | Computed inline in `pages/habits/[id].astro` |
| **API/backend path** | Not a separate endpoint (computed from records) |
| **Test IDs** | None |
| **Status** | ✅ |
| **Evidence** | Page renders best streak; plan §3 P1 marked shipped. |

### 2.13 Daily note on habit (long-press)

| Field | Value |
| --- | --- |
| **Workflow** | User long-presses a cell; textarea opens; saves note for that (habit, day) |
| **Current route** | `/gui` (HabitCheckBox long-press) |
| **Entry point** | `frontend/components.py::habit_notes` |
| **User role** | Authenticated |
| **User action** | Long-press (~600ms) cell → textarea opens → type → save |
| **Expected result** | Note saved; visible on detail page |
| **Data read/written** | Updates `habit_list.data` (records[i].text) |
| **Side effects** | None |
| **Error/empty/loading cases** | Note > `DAILY_NOTE_MAX_LENGTH` (1024) → truncated |
| **Existing code paths** | `habit.tick(day, done, text)` — `text` field of the record |
| **Proposed new UI path** | HabitNoteDialog + HabitNoteRow exist (`components/HabitNoteDialog.tsx`, `HabitNoteRow.tsx`) — wired to HabitGrid cells? |
| **API/backend path** | `POST /api/v1/habits/{id}/completions` body `{done, date, text, ...}` |
| **Test IDs** | None |
| **Status** | 🟡 |
| **Evidence** | `HabitNoteDialog.tsx` exists; `HabitNoteRow.tsx` renders per-day notes on detail page. **Long-press on grid cells**: plan §3 P2 explicitly says "Long-press → notes textarea deferred to P2 — needs the long-press helper to differentiate from context sheet". Not yet wired. |

---

## Group 3 — Stats

### 3.1 Stats page (per-habit streak, date range)

| Field | Value |
| --- | --- |
| **Workflow** | User opens /stats; sees per-habit streak cards; picks date range |
| **Current route** | `/gui/stats` |
| **Entry point** | `frontend/stats_page.py::stats_page_ui(today, habit_list)` |
| **User role** | Authenticated |
| **User action** | Navigate; pick date range from menu |
| **Expected result** | Streak cards rendered; date range respected |
| **Data read/written** | Reads `habit_list.data`; reads/writes `app.storage.user["stats_start_date"]` / `"stats_end_date"` |
| **Side effects** | None |
| **Error/empty/loading cases** | Bad date format → "Invalid date range"; future start > end → "cannot be after" |
| **Existing code paths** | `frontend/stats_page.py`, `frontend/menu.py::stats_date_pick_menu` |
| **Proposed new UI path** | `web/concepts/src/pages/stats.astro` |
| **API/backend path** | `GET /api/v1/habits/{id}/completions?date_start=...&date_end=...` |
| **Test IDs** | Backend: `tests/test_apis.py::test_get_completions_date_range` |
| **Status** | ✅ |
| **Evidence** | Page exists with 15-week heatmap per habit. |

### 3.2 Date range picker

| Field | Value |
| --- | --- |
| **Workflow** | User picks "Last 3/6/12 months" or custom range; page reloads with new range |
| **Current route** | `/gui/stats` |
| **Entry point** | `frontend/menu.py::stats_date_pick_menu` |
| **User role** | Authenticated |
| **User action** | Click menu item; or custom range dialog |
| **Expected result** | `app.storage.user["stats_start_date"]` set; page reloads |
| **Data read/written** | Writes `app.storage.user["stats_start_date"]` + `stats_end_date` |
| **Side effects** | None |
| **Error/empty/loading cases** | Bad format → "Invalid date range" |
| **Existing code paths** | Same |
| **Proposed new UI path** | `web/concepts/src/pages/stats.astro` URL query params `?start=YYYY-MM-DD&end=YYYY-MM-DD` |
| **API/backend path** | n/a (filtering is server-side in the Astro page render) |
| **Test IDs** | `tests/test_slice8_stats.py::Slice8StatsPageTests` (6 SSR contract tests; require running dev server) |
| **Status** | ✅ |
| **Evidence** | Slice 8: stats.astro gained a preset dropdown (3M/6M/12M/24M) + two `<input type="date">` fields + Apply/Clear controls. Filtering happens server-side in the page render via `filterDay()`. Invalid date format / start > end renders "Invalid date range" alert. URL query state matches the legacy `app.storage.user` semantics (stateless, shareable by link). |

### 3.3 Calendar heatmap on detail (15 weeks)

| Field | Value |
| --- | --- |
| **Workflow** | User sees 15-week heatmap of habit completions on detail page |
| **Current route** | `/gui/habits/{id}` |
| **Entry point** | `frontend/habit_page.py::CalendarHeatmap.build()` |
| **User role** | Authenticated |
| **User action** | n/a (display only) |
| **Expected result** | 15-week grid renders |
| **Data read/written** | Reads `habit.ticked_days` |
| **Side effects** | None |
| **Existing code paths** | `frontend/components.py::CalendarHeatmap` |
| **Proposed new UI path** | `<Heatmap />` component (`web/concepts/src/components/Heatmap.astro`) |
| **API/backend path** | n/a (computed from records) |
| **Test IDs** | None |
| **Status** | ✅ |
| **Evidence** | Component exists; mounted on `/habits/[id].astro` and `/stats.astro`. |

### 3.4 Full 53-week heatmap

| Field | Value |
| --- | --- |
| **Workflow** | User opens `/gui/habits/{id}/heatmap`; sees 53-week heatmap |
| **Current route** | `/gui/habits/{id}/heatmap` |
| **Entry point** | `frontend/streaks.py::streaks(today, habit)` |
| **User role** | Authenticated |
| **User action** | Navigate |
| **Expected result** | 53-week heatmap; 1 row per year |
| **Data read/written** | Reads `habit.ticked_days` |
| **Side effects** | None |
| **Existing code paths** | `CalendarHeatmap.build(today, 53, ...)` |
| **Proposed new UI path** | Not a separate Astro page; merged into `/habits/[id]` with 15-week view (no 53-week view) |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | 🟡 |
| **Evidence** | Detail page has 15-week; 53-week view is omitted (collapsed to 15 weeks). Functional but smaller. |

### 3.5 1-year habit history

| Field | Value |
| --- | --- |
| **Workflow** | User sees 1-year tick history (date list) |
| **Current route** | `/gui/habits/{id}` |
| **Entry point** | `frontend/components.py::habit_history` |
| **User role** | Authenticated |
| **User action** | n/a (display) |
| **Expected result** | List of ticked dates over the past year |
| **Data read/written** | Reads `habit.ticked_days` |
| **Side effects** | None |
| **Existing code paths** | `habit_history(habit)` |
| **Proposed new UI path** | Rendered inline in `pages/habits/[id].astro` |
| **API/backend path** | `GET /api/v1/habits/{id}/completions?date_start=...&date_end=...` |
| **Test IDs** | None |
| **Status** | ✅ |
| **Evidence** | Page renders history section. |

---

## Group 4 — Settings

### 4.1 Theme (light/dark)

| Field | Value |
| --- | --- |
| **Workflow** | User picks light/dark; choice persists across reloads |
| **Current route** | `/gui/settings` |
| **Entry point** | `frontend/settings_page.py::settings_page(user)` |
| **User role** | Authenticated |
| **User action** | Click "Light" / "Dark" button |
| **Expected result** | `app.storage.user["dark_mode"]` set; client emits JS to set localStorage |
| **Data read/written** | Writes NiceGUI storage |
| **Side effects** | `fetch_user_dark_mode` propagates to next connection |
| **Error/empty/loading cases** | None |
| **Existing code paths** | `fetch_user_dark_mode(client)` in `routes/routes.py::init_gui_routes` |
| **Proposed new UI path** | `web/concepts/src/pages/settings.astro` (theme form → cookie) |
| **API/backend path** | n/a (cookie-only on Astro) |
| **Test IDs** | None |
| **Status** | ✅ |
| **Evidence** | `castor-theme` cookie; `Layout.astro` reads + applies `data-theme`. |

### 4.2 Custom CSS

| Field | Value |
| --- | --- |
| **Workflow** | User edits custom CSS; gets sanitised by tinycss2; applied to layout |
| **Current route** | `/gui/settings` |
| **Entry point** | `frontend/settings_page.py` + CodeMirror editor |
| **User role** | Authenticated |
| **User action** | Edit CSS in editor; save |
| **Expected result** | CSS applied; persisted in `user_configs.css` |
| **Data read/written** | Writes `user_configs.css` (column) |
| **Side effects** | `apply_theme_style()` injects `<style>` on next page render |
| **Error/empty/loading cases** | Unsupported CSS rules → rejected by `css_sanitizer.sanitize_css()`; partial stylesheets rejected as a whole |
| **Existing code paths** | `views.update_custom_css(user, css)` → `css_sanitizer.sanitize_css(css)` |
| **Proposed new UI path** | `web/concepts/src/pages/settings.astro` + `<SettingsClient />` (raw textarea, sends to backend) |
| **API/backend path** | (not yet landed) `POST /api/v1/settings/custom-css` — TODO per settings.astro header |
| **Test IDs** | None |
| **Status** | 🟡 |
| **Evidence** | UI exists; backend endpoint per settings.astro header docstring is "not yet shipped". |

### 4.3 Help dialog (4 links)

| Field | Value |
| --- | --- |
| **Workflow** | User clicks Help; dialog opens with Wiki / Supporter / YouTube / Issues links |
| **Current route** | `/gui/settings` |
| **Entry point** | `frontend/layout.py::show_help_dialog` |
| **User role** | Authenticated |
| **User action** | Click Help menu item |
| **Expected result** | Dialog with 4 links opens |
| **Data read/written** | None |
| **Side effects** | None |
| **Error/empty/loading cases** | None |
| **Existing code paths** | `layout.py::show_help_dialog()` |
| **Proposed new UI path** | `<HelpDialog />` (`web/concepts/src/components/HelpDialog.tsx`) |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ✅ |
| **Evidence** | Component exists, mounted in `/settings`. Also exposed via "MoreSheet" `closeAndNavigate('/help')` BUT `/help` page does not exist — sheet link dead-ends at 404. |

### 4.4 `/help` page

| Field | Value |
| --- | --- |
| **Workflow** | User navigates to `/help` (from mobile More sheet); sees help content |
| **Current route** | (no legacy equivalent — was a dialog, not a page) |
| **Entry point** | `web/concepts/src/components/MoreSheet.tsx:133` → `closeAndNavigate('/help')` |
| **User role** | Authenticated |
| **User action** | Tap "Help" in mobile More sheet |
| **Expected result** | Help page renders |
| **Data read/written** | None |
| **Side effects** | None |
| **Existing code paths** | None on legacy (legacy is a dialog) |
| **Proposed new UI path** | **MISSING** — `/help` page does not exist; `MoreSheet` line 133 404s |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ❌ |
| **Evidence** | `ls web/concepts/src/pages/help.astro` → not found. `AUDIT-2026-09-23.md` row 1 marks this as the visible 404. |

### 4.5 Sign out from settings

| Field | Value |
| --- | --- |
| **Workflow** | User clicks Sign out; cookies cleared; (castor) token_version bumped |
| **Current route** | `/gui/settings` |
| **Entry point** | Logout button |
| **User role** | Authenticated |
| **User action** | Click Sign out |
| **Expected result** | Redirect to /login |
| **Data read/written** | (castor) bumps token_version |
| **Side effects** | audit |
| **Existing code paths** | Same as 1.6 |
| **Proposed new UI path** | `<form action="/logout" method="post">` in settings.astro |
| **API/backend path** | `POST /auth/logout` |
| **Test IDs** | `tests/test_logout_bump.py` |
| **Status** | ✅ |
| **Evidence** | Form posts to `/logout`; backend bumps. |

---

## Group 5 — Security (passkey + password)

### 5.1 View passkey list

| Field | Value |
| --- | --- |
| **Workflow** | User opens /security; sees list of registered passkeys with name + created date |
| **Current route** | `/gui/security` |
| **Entry point** | `frontend/security_page.py::security_page(user)` |
| **User role** | Authenticated |
| **User action** | Navigate |
| **Expected result** | Cards for each passkey |
| **Data read/written** | Reads `webauthn_credential` table |
| **Side effects** | None |
| **Error/empty/loading cases** | 0 credentials → "No passkeys yet" |
| **Existing code paths** | `user_manager.get_webauthn_credentials(user)` |
| **Proposed new UI path** | `<SecurityContent client:load />` |
| **API/backend path** | `GET /auth/webauthn/credentials` |
| **Test IDs** | `tests/test_lane2_webauthn.py` |
| **Status** | ✅ |
| **Evidence** | Page exists; component fetches credentials; backend endpoint exists. |

### 5.2 Add passkey

| Field | Value |
| --- | --- |
| **Workflow** | User opens dialog; types nickname; browser prompts; passkey saved |
| **Current route** | `/gui/security` |
| **Entry point** | `security_page.py` → `add_security_passkeys_javascript()` |
| **User role** | Authenticated |
| **User action** | Type nickname + tap authenticator |
| **Expected result** | New row in `webauthn_credential` |
| **Data read/written** | Writes `webauthn_credential` row |
| **Side effects** | `audit.append_audit_event("passkey_register", "success", user_id, ip)` |
| **Error/empty/loading cases** | rpId mismatch → security error; nickname empty → "required" |
| **Existing code paths** | `webauthn_routes.py::registration_user` + `register/begin` + `register/complete` |
| **Proposed new UI path** | `SecurityContent.tsx::handleAddPasskey` |
| **API/backend path** | `POST /auth/webauthn/register/begin` (form) → `POST /auth/webauthn/register/complete` |
| **Test IDs** | `tests/test_lane2_webauthn.py::test_register_begin` |
| **Status** | ✅ |
| **Evidence** | Code path exists; tested manually (no playwright). |

### 5.3 Remove passkey

| Field | Value |
| --- | --- |
| **Workflow** | User opens passkey menu → Remove → confirm with current password → passkey deleted |
| **Current route** | `/gui/security` |
| **Entry point** | `security_page.py` (menu → Remove → dialog) |
| **User role** | Authenticated |
| **User action** | Click Remove, type password |
| **Expected result** | Row removed from list + DB |
| **Data read/written** | Deletes `webauthn_credential` row |
| **Side effects** | `audit.append_audit_event("passkey_delete", "success", user_id, ip)` |
| **Error/empty/loading cases** | Wrong password → reject; last-key removal requires password + session preserved |
| **Existing code paths** | `webauthn_routes.py::delete_passkey_credential` |
| **Proposed new UI path** | `SecurityContent.tsx::handleRemovePasskey` (password confirm via prompt()) |
| **API/backend path** | `DELETE /auth/webauthn/credentials/{credential_id}` |
| **Test IDs** | `tests/test_lane2_webauthn.py::test_delete_credential` |
| **Status** | ✅ |
| **Evidence** | Backend endpoint exists; SecurityContent wires to it. |

### 5.4 Change password

| Field | Value |
| --- | --- |
| **Workflow** | User opens Change Password dialog; types current + new + confirm; password changed, token_version bumped, sessions invalidated |
| **Current route** | `/gui/security` |
| **Entry point** | `security_page.py::change_password_dialog` |
| **User role** | Authenticated |
| **User action** | Submit form |
| **Expected result** | Password updated; token_version incremented; other sessions logged out |
| **Data read/written** | Updates `user.hashed_password` + `user.token_version` |
| **Side effects** | `audit.append_audit_event("password_change", "success", user_id, ip)`; all existing JWTs invalidated |
| **Error/empty/loading cases** | Wrong current password → reject; new password <12 → reject; mismatch → reject |
| **Existing code paths** | `app/security_actions.py::change_password(user_id, current_password, new_password, expected_version)` |
| **Proposed new UI path** | `SecurityContent.tsx::handleChangePassword` (uses prompt()s — UX is poor) |
| **API/backend path** | `POST /auth/webauthn/change-password` body `{current_password, new_password}` |
| **Test IDs** | `tests/test_sensitive_actions.py::test_password_change_cas_rejects_mutation_after_authorization` + `tests/test_slice5_security.py::test_change_password_succeeds_and_bumps_token_version`, `test_change_password_wrong_current_password_returns_401`, `test_change_password_unauthenticated_returns_401` |
| **Status** | ✅ |
| **Evidence** | Slice 5 verifies the full change-password round-trip: 200 on success, old JWT rejected (401) on next /api/v1/habits call, new password works, old password rejected. Wrong current password returns 401 with the public-safe `Fresh current-password...` message. **Audit-event claim removed**: the parity-matrix above listed `audit.append_audit_event("password_change", ...)` as a side effect; verified the actual backend in `castor/app/security_actions.py:117` does NOT emit this audit event. Will document in Phase 4 audit-reconciliation step. |

### 5.5 Recovery email (set + verify)

| Field | Value |
| --- | --- |
| **Workflow** | User adds recovery email; receives 6-digit code; verifies; email becomes verified |
| **Current route** | `/gui/security` |
| **Entry point** | `security_page.py::recovery_email_section` |
| **User role** | Authenticated |
| **User action** | Enter email → enter code |
| **Expected result** | `user.recovery_email_verified = True` |
| **Data read/written** | Updates `user.recovery_email` + `recovery_email_verified` + writes `recovery_email_challenge` row |
| **Side effects** | `audit.append_audit_event("recovery_email_set", ...)` |
| **Error/empty/loading cases** | Invalid email format → 422; bad code → reject; expired code → reject |
| **Existing code paths** | `webauthn_routes.py::set_recovery_email` + `verify_recovery_email` |
| **Proposed new UI path** | `SecurityContent.tsx::handleAddRecoveryEmail` + `handleVerifyEmail` |
| **API/backend path** | `POST /auth/webauthn/recovery-email` + `POST /auth/webauthn/recovery-email/verify` |
| **Test IDs** | `tests/security_recovery_browser_acceptance.py` (excluded from laptop run; apollo-only) + `tests/test_slice5_security.py::test_recovery_email_request_returns_pending_envelope`, `test_recovery_email_sends_email`, `test_recovery_email_verify_with_correct_code_succeeds`, `test_recovery_email_verify_invalid_code_format_returns_400`, `test_recovery_email_verify_code_too_short_returns_400`, `test_recovery_email_verify_remove_is_idempotent_when_no_email` |
| **Status** | ✅ |
| **Evidence** | Slice 5 verifies the full recovery-email flow: request returns `{pending, email, expires_at}`, code is sent via `send_email` (mocked), happy-path verify sets `recovery_email_verified=true`, non-digit / short codes return 400 "Code must be exactly 6 digits", remove=True is idempotent when no email is set. **Audit-event claim removed**: parity-matrix above listed `audit.append_audit_event("recovery_email_set", ...)`; verified `webauthn_routes.py::verify_recovery_email` only emits audit on the `remove=True` path (`audit.record("recovery_email_removed", ...)` line 862). The set path does NOT emit audit. |

### 5.6 Passwordless account has no usable state

| Field | Value |
| --- | --- |
| **Workflow** | A user without a password cannot login by typing empty |
| **Current route** | `/login` |
| **Entry point** | `views.login_user` |
| **User role** | Any |
| **User action** | n/a (defensive) |
| **Expected result** | Empty password rejected |
| **Data read/written** | Reads `user.hashed_password` (must be non-empty + valid format) |
| **Side effects** | None |
| **Error/empty/loading cases** | `passwordless_empty_and_malformed_hashes_do_not_authorize` test ensures this |
| **Existing code paths** | `security_actions.py::authorize` |
| **Proposed new UI path** | n/a (server-side enforcement) |
| **API/backend path** | Same |
| **Test IDs** | `tests/test_sensitive_actions.py::test_passwordless_empty_and_malformed_hashes_do_not_authorize` |
| **Status** | ✅ |
| **Evidence** | Test passes. |

### 5.7 Delete account (wipe personal data)

| Field | Value |
| --- | --- |
| **Workflow** | User opens /account/delete → confirms → backend wipes habit_list, API tokens, identity, owned data; keeps an anonymous disabled tombstone for collision-detection |
| **Current route** | `/account/delete` (legacy `/gui/delete_account`) |
| **Entry point** | `frontend/account_delete.py::confirm_delete_account` (legacy) |
| **User role** | Authenticated |
| **User action** | Type confirmation phrase; click "Delete account" |
| **Expected result** | 204 No Content; JWT invalidated; subsequent /api/v1/habits calls return 401 |
| **Data read/written** | Deletes `habit_list`, `api_token`, identity row, owned data; archives user as anonymous disabled |
| **Side effects** | `audit.append_audit_event("account_deletion", ...)` (verify in Phase 4) |
| **Error/empty/loading cases** | Missing bearer → 401; garbage bearer → 401 |
| **Existing code paths** | `castor/views.py::delete_user_account(user)` → `user_storage.delete_user_habit_list` + `crud.delete_user_api_token` + `crud.delete_user_identity` + `crud.delete_user_owned_data` + `user_archive` |
| **Proposed new UI path** | `web/concepts/src/pages/account/delete.astro` (Astro page calls backend) |
| **API/backend path** | `DELETE /api/v1/account` (returns 204) |
| **Test IDs** | `tests/test_slice5_security.py::test_delete_account_returns_204_and_clears_habit_list`, `test_delete_account_unauthenticated_returns_401`, `test_delete_account_with_garbage_bearer_returns_401`, `test_delete_account_blocks_relogin_with_same_email` |
| **Status** | ✅ |
| **Evidence** | Slice 5 verifies 204 on delete, JWT rejection on subsequent calls, no 500 on re-registration. |

---

## Group 6 — Import / Export

### 6.1 Export JSON (download)

| Field | Value |
| --- | --- |
| **Workflow** | User clicks Export JSON; file downloads with current habit data |
| **Current route** | `/gui/export` |
| **Entry point** | `frontend/export_page.py::export_panel(habit_list, user)` → `views.export_user_habit_list()` |
| **User role** | Authenticated |
| **User action** | Click Export JSON |
| **Expected result** | File `beaverhabits_YYYY_MM_DD.json` downloads |
| **Data read/written** | Reads `habit_list.data` |
| **Side effects** | None |
| **Error/empty/loading cases** | No habits → "No habits to export" |
| **Existing code paths** | `views.export_user_habit_list(habit_list, user.email)` |
| **Proposed new UI path** | `web/concepts/src/pages/export.astro` (server-side GET → backend) |
| **API/backend path** | `GET /api/v1/habits/export` |
| **Test IDs** | `tests/test_apis.py::test_export_round_trip` + `tests/test_slice6_import_export.py::Slice6ImportExportTests` (19 tests covering empty, metadata, ticked records, order, user-scoping, content-type, JSON round-trip, archive preservation, record shape parity, BFF import loop with tags/period/status/records preservation) |
| **Status** | ✅ |
| **Evidence** | Slice 6.x fix: `_habit_list_export_data()` now guarantees `status` is in every exported habit (it lives only in the Python object until explicitly set, so a raw deepcopy would lose it). `tags`, `period`, `star` are already written by the PUT path. Records are now normalised to the canonical nested `{data: {day, done, timestamp, text?}}` shape matching `/habits/{id}` — verified by `test_export_record_shape_matches_habit_detail`. |
| **Schema fix** | Records shape unified between `/habits/export` and `/habits/{id}`. BFF import loop now does POST + PUT so tags/period/status round-trip — verified by `test_import_loop_preserves_tags_period_status`. Records now round-trip via the BFF's records POST loop — verified by `test_import_loop_preserves_records_with_nested_shape`. |

### 6.2 Export CSV

| Field | Value |
| --- | --- |
| **Workflow** | User clicks Export CSV; file downloads in CSV format |
| **Current route** | `/gui/export` |
| **Entry point** | (not exposed in legacy UI; only in `views.export_user_habit_list`) |
| **User role** | Authenticated |
| **User action** | n/a (legacy UI has no CSV button) |
| **Expected result** | CSV file |
| **Data read/written** | Reads `habit_list.data` |
| **Side effects** | None |
| **Existing code paths** | None (not implemented in legacy UI) |
| **Proposed new UI path** | Not in Astro either |
| **API/backend path** | Not exposed |
| **Test IDs** | None |
| **Status** | ❌ |
| **Evidence** | Legacy export_page.py only has JSON download; CSV import exists but no CSV export. Same on migration branch. |

### 6.3 Import JSON/CSV

| Field | Value |
| --- | --- |
| **Workflow** | User uploads JSON or CSV; habits merged into existing list |
| **Current route** | `/gui/import` |
| **Entry point** | `frontend/import_page.py::handle_upload` |
| **User role** | Authenticated |
| **User action** | Choose file → upload |
| **Expected result** | New habits added; collisions renamed |
| **Data read/written** | Reads uploaded file; writes `habit_list.data` |
| **Side effects** | None |
| **Error/empty/loading cases** | Bad JSON → 400; unsupported format → "Unsupported format" |
| **Existing code paths** | `import_page.py::import_from_json`, `import_from_csv` |
| **Proposed new UI path** | `web/concepts/src/pages/import.astro` (server-side POST → backend) |
| **API/backend path** | `POST /api/v1/habits/import` (backend endpoint **NOT YET SHIPPED**) |
| **Test IDs** | `tests/test_slice6_import_export.py::test_import_loop_creates_habits_from_export`, `test_import_loop_preserves_records_with_nested_shape`, `test_import_loop_preserves_tags_period_status`, `test_import_max_habit_count_enforced`, `test_import_loop_handles_missing_optional_fields`, `test_import_loop_rejects_empty_name`, `test_export_size_scales_with_habits` |
| **Status** | ✅ |
| **Evidence** | The Astro BFF import at `web/concepts/src/pages/api/v1/habits/import.ts` implements import as POST `{name}` → PUT `{tags, period, status, star}` → POST records loop. Slice 6.x verifies the full round-trip: export → wipe → import loop → all habits reappear with **all metadata preserved** (tags, period, status, star, records with text). BFF fix is the 3-line POST-then-PUT change. Records are flattened from the export's nested shape before posting to /habits/{id}/completions. |

### 6.4 Telegram backup (per-user)

| Field | Value |
| --- | --- |
| **Workflow** | User configures bot token + chat ID; backup triggered manually or daily; JSON sent to Telegram |
| **Current route** | `/gui/settings` → Backup panel |
| **Entry point** | `frontend/export_page.py::backup_panel(habit_list)` |
| **User role** | Authenticated |
| **User action** | Configure Telegram bot; click Backup |
| **Expected result** | JSON document in Telegram chat |
| **Data read/written** | Reads `habit_list.data`; reads/writes `user_configs.telegram_bot_token` + `telegram_chat_id` |
| **Side effects** | Telegram API call (outbound) |
| **Error/empty/loading cases** | Invalid bot token → Telegram 401; chat ID wrong → 400 |
| **Existing code paths** | `core/backup.py::backup_to_telegram(token, chat_id, habit_list)` |
| **Proposed new UI path** | Not in Astro (not ported) |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ❌ |
| **Evidence** | `grep -r "telegram" web/concepts/src/` returns nothing. Telegram backup config and trigger UI not migrated. |

---

## Group 7 — Admin (superuser)

### 7.1 Admin user list

| Field | Value |
| --- | --- |
| **Workflow** | Superuser opens /admin; sees list of all activated users + customers |
| **Current route** | `/gui/admin` |
| **Entry point** | `frontend/admin.py::admin_page(user)` |
| **User role** | Superuser (`is_superuser=True`) |
| **User action** | Navigate |
| **Expected result** | Table of users |
| **Data read/written** | Reads `user`, `customer` tables |
| **Side effects** | None |
| **Error/empty/loading cases** | Non-superuser → 403 |
| **Existing code paths** | `crud.get_user_list()`, `crud.get_customer_list()` |
| **Proposed new UI path** | `web/concepts/src/pages/admin.astro` (ADMIN_EMAIL-gated) |
| **API/backend path** | `GET /api/v1/admin/users` |
| **Test IDs** | `tests/test_slice8_admin.py::Slice8AdminTests::test_list_users_*` |
| **Status** | ✅ |
| **Evidence** | Page exists; backend endpoint exists; auth delegated to backend (ADMIN_EMAIL string gate). Slice 8 pinned: 4 contract tests cover unauth/regular-user/admin, response shape, no password leakage. |

### 7.2 Manual backup trigger (admin)

| Field | Value |
| --- | --- |
| **Workflow** | Admin clicks "Trigger backup now"; backup for all users runs |
| **Current route** | `/gui/admin` |
| **Entry point** | `frontend/admin.py` (button) |
| **User role** | Superuser |
| **User action** | Click button |
| **Expected result** | Backup runs for all activated users |
| **Data read/written** | Reads each user's habit list; sends Telegram |
| **Side effects** | Telegram API call per user; `audit.append_audit_event("backup", ...)` |
| **Error/empty/loading cases** | Per-user failure logged, doesn't stop loop |
| **Existing code paths** | `views.backup_all_users()` |
| **Proposed new UI path** | `admin.astro` button → POST `/api/admin/backup` → backend `/api/v1/admin/backup` |
| **API/backend path** | `POST /api/v1/admin/backup` |
| **Test IDs** | `tests/test_slice8_admin.py::Slice8AdminTests::test_trigger_backup_*` |
| **Status** | ✅ |
| **Evidence** | Page exists; backend endpoint exists. Slice 8 pinned: 5 contract tests cover unauth/regular-user/admin-success/audit-event/emits-telegram-if-configured. Audit event `backup` is emitted (verified by querying `castor.app.audit.AuditEvent`). |

### 7.3 Paddle promote/demote

| Field | Value |
| --- | --- |
| **Workflow** | Admin enters email, clicks Promote to Pro / Demote from Pro |
| **Current route** | `/gui/admin` |
| **Entry point** | `frontend/admin.py` (buttons) |
| **User role** | Superuser |
| **User action** | Type email + click |
| **Expected result** | `customer.activated` toggled |
| **Data read/written** | Updates `customer` table |
| **Side effects** | None |
| **Error/empty/loading cases** | Email not found → silent failure (background task) |
| **Existing code paths** | `views.promote_user_to_pro(email, pro)` |
| **Proposed new UI path** | Not in Astro (Paddle dropped) |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ❌ |
| **Evidence** | Paddle was commercial-only; intentionally dropped on migration branch per audit/plan. |

---

## Group 8 — Mobile chrome

### 8.1 Mobile bottom nav (Home / Stats / More)

| Field | Value |
| --- | --- |
| **Workflow** | Mobile user sees fixed bottom nav; taps icons to navigate |
| **Current route** | All pages |
| **Entry point** | `frontend/bottom_nav.py::bottom_nav()` |
| **User role** | Authenticated mobile user |
| **User action** | Tap Home / Stats / More |
| **Expected result** | Navigate to /gui, /gui/stats, or open More sheet |
| **Data read/written** | None |
| **Side effects** | None |
| **Error/empty/loading cases** | None |
| **Existing code paths** | `frontend/bottom_nav.py` + `frontend/device.py::is_mobile(user_agent)` |
| **Proposed new UI path** | `<BottomNav />` (`web/concepts/src/components/BottomNav.astro`) |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ✅ |
| **Evidence** | Component exists; hidden ≥641px; safe-area-inset bottom; mobile UA in browser_vision confirms visibility. |

### 8.2 More sheet (mobile hamburger menu)

| Field | Value |
| --- | --- |
| **Workflow** | Mobile user taps ⋮; bottom sheet opens with Account / Data / Session sections |
| **Current route** | All pages |
| **Entry point** | `frontend/more_sheet.py` |
| **User role** | Authenticated mobile user |
| **User action** | Tap ⋮ → tap menu item |
| **Expected result** | Navigate or open dialog |
| **Data read/written** | None |
| **Side effects** | None |
| **Error/empty/loading cases** | None |
| **Existing code paths** | `frontend/more_sheet.py` |
| **Proposed new UI path** | `<MoreSheet />` (`web/concepts/src/components/MoreSheet.tsx`) |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | 🟡 |
| **Evidence** | Component exists; has all 8 menu items. **Dead link**: `closeAndNavigate('/help')` 404s because `/help` doesn't exist. |

### 8.3 Desktop hamburger menu

| Field | Value |
| --- | --- |
| **Workflow** | Desktop user taps top-right menu; side sheet opens with Tools / Account / Session |
| **Current route** | All pages |
| **Entry point** | `frontend/menu.py::menu()` |
| **User role** | Authenticated desktop user |
| **User action** | Tap menu icon |
| **Expected result** | Side sheet opens |
| **Data read/written** | None |
| **Side effects** | None |
| **Existing code paths** | `frontend/menu.py` |
| **Proposed new UI path** | `<DesktopMenu />` (`web/concepts/src/components/DesktopMenu.tsx`) |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ✅ |
| **Evidence** | Component exists, mounted in Layout.astro for `Astro.locals.session`. |

---

## Group 9 — Public + meta pages

### 9.1 `/` redirect

| Field | Value |
| --- | --- |
| **Workflow** | Unauthenticated user visits /; sees /login. Authenticated user visits /; sees /habits |
| **Current route** | `/` |
| **Entry point** | `routes/routes.py:153-154` `index_page` |
| **User role** | Any |
| **User action** | Visit / |
| **Expected result** | Same as /gui |
| **Data read/written** | None |
| **Side effects** | None |
| **Existing code paths** | `routes/routes.py::index_page` |
| **Proposed new UI path** | `web/concepts/src/pages/index.astro` |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ✅ |
| **Evidence** | Redirect logic exists. |

### 9.2 Terms page

| Field | Value |
| --- | --- |
| **Workflow** | User opens /terms; sees terms of service |
| **Current route** | (no legacy page) |
| **Entry point** | n/a |
| **User role** | Any |
| **User action** | Visit /terms |
| **Expected result** | Static terms page |
| **Data read/written** | None |
| **Side effects** | None |
| **Existing code paths** | `frontend/paddle_page.py::TERMS` constant (legacy) |
| **Proposed new UI path** | `web/concepts/src/pages/terms.astro` |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | 🔵 |
| **Evidence** | New page. |

### 9.3 Privacy page

| Field | Value |
| --- | --- |
| **Workflow** | User opens /privacy; sees privacy policy |
| **Current route** | (no legacy page) |
| **Entry point** | n/a |
| **User role** | Any |
| **User action** | Visit /privacy |
| **Expected result** | Static privacy page |
| **Data read/written** | None |
| **Side effects** | None |
| **Existing code paths** | None |
| **Proposed new UI path** | `web/concepts/src/pages/privacy.astro` |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | 🔵 |
| **Evidence** | New page. |

---

## Group 10 — Private Circles (castor-only)

### 10.1 List my circles

| Field | Value |
| --- | --- |
| **Workflow** | User opens /circles; sees circles they own or belong to |
| **Current route** | (no legacy equivalent) |
| **Entry point** | n/a |
| **User role** | Authenticated |
| **User action** | Navigate |
| **Expected result** | List of circles |
| **Data read/written** | Reads `circle`, `circle_member` |
| **Side effects** | None |
| **Error/empty/loading cases** | None → "No circles yet" |
| **Existing code paths** | (castor only) `circle_routes.py::list_circles` |
| **Proposed new UI path** | `web/concepts/src/pages/circles/index.astro` |
| **API/backend path** | `GET /api/v1/circles` |
| **Test IDs** | None |
| **Status** | 🔵 |
| **Evidence** | Page exists; backend endpoint exists. |

### 10.2 Create circle

| Field | Value |
| --- | --- |
| **Workflow** | User opens /circles/new; enters name; circle created; user becomes owner |
| **Current route** | (no legacy) |
| **Entry point** | n/a |
| **User role** | Authenticated |
| **User action** | Submit name |
| **Expected result** | `circle` row + `circle_member` row (owner) |
| **Data read/written** | Writes both |
| **Side effects** | `audit.append_audit_event("circle_create", ...)` |
| **Error/empty/loading cases** | Empty name → 422; name >80 chars → 422 |
| **Existing code paths** | (castor) `circle_routes.py::create_circle` |
| **Proposed new UI path** | `circles/new.astro` |
| **API/backend path** | `POST /api/v1/circles` |
| **Test IDs** | None |
| **Status** | 🔵 |
| **Evidence** | Page exists; backend exists. |

### 10.3 Share habit to circle

| Field | Value |
| --- | --- |
| **Workflow** | Owner opens circle detail → picks habit → shares with visibility tier |
| **Current route** | (no legacy) |
| **Entry point** | n/a |
| **User role** | Circle owner |
| **User action** | Select habit + visibility |
| **Expected result** | `circle_habit` row |
| **Data read/written** | Writes `circle_habit` |
| **Side effects** | `audit.append_audit_event("circle_habit_share", ...)` |
| **Error/empty/loading cases** | Non-owner → 403; habit not owned → 404 |
| **Existing code paths** | (castor) `circle_routes.py::share_circle_habit` |
| **Proposed new UI path** | `circles/[id]/share.astro` |
| **API/backend path** | `POST /api/v1/circles/{id}/habits` |
| **Test IDs** | None |
| **Status** | 🔵 |
| **Evidence** | Page exists; backend exists. |

### 10.4 Invite member (link or email)

| Field | Value |
| --- | --- |
| **Workflow** | Owner invites a user via shareable link or email; invitee accepts; membership created |
| **Current route** | (no legacy) |
| **Entry point** | n/a |
| **User role** | Owner |
| **User action** | Pick delivery; copy link OR send email |
| **Expected result** | `circle_invite` row; on accept, `circle_member` row |
| **Data read/written** | Writes both |
| **Side effects** | `audit.append_audit_event("circle_invite_create", ...)` / `circle_invite_accept` |
| **Error/empty/loading cases** | Expired token → 410; reused token → 409 |
| **Existing code paths** | (castor) `circle_routes.py::create_invite`, `accept_invite` |
| **Proposed new UI path** | (no separate page; inlined in `circles/[id].astro`) |
| **API/backend path** | `POST /api/v1/circles/{id}/invites`; `POST /api/v1/circles/{id}/join` |
| **Test IDs** | None |
| **Status** | 🔵 |
| **Evidence** | Backend exists; Astro UI exists for invite list/accept flow. |

### 10.5 Circle feed (per-day records)

| Field | Value |
| --- | --- |
| **Workflow** | Member opens circle detail; sees per-habit feed with ticks, optional streak, optional notes (gated by visibility) |
| **Current route** | (no legacy) |
| **Entry point** | n/a |
| **User role** | Circle member |
| **User action** | Navigate |
| **Expected result** | Feed rendered with visibility-tier-filtered data |
| **Data read/written** | Reads `circle_habit` + `habit_list.data` |
| **Side effects** | None |
| **Error/empty/loading cases** | Non-member → 403 |
| **Existing code paths** | (castor) `circle_routes.py::circle_feed` |
| **Proposed new UI path** | `circles/[id].astro` (feed section) |
| **API/backend path** | `GET /api/v1/circles/{id}/feed` |
| **Test IDs** | None |
| **Status** | 🔵 |
| **Evidence** | Page exists; backend exists. |

---

## Group 11 — Realtime

### 11.1 WebSocket fan-out of HabitListChanged

| Field | Value |
| --- | --- |
| **Workflow** | User A ticks a habit; User B (same account, other device) sees the tick live |
| **Current route** | All clients (mobile, browser) |
| **Entry point** | `habit.tick()` → `publish(HabitListChanged)` |
| **User role** | Authenticated, multiple devices |
| **User action** | Tick on device 1 |
| **Expected result** | Device 2 receives the change and applies |
| **Data read/written** | Same as 2.2 |
| **Side effects** | WebSocket broadcast |
| **Error/empty/loading cases** | WebSocket closed → reconnect (Engine.IO handles) |
| **Existing code paths** | `castor/realtime.py::manager.broadcast(user_id, payload)` |
| **Proposed new UI path** | Not used by Astro (no Astro client connects to WebSocket) |
| **API/backend path** | `WS /api/v1/sync/ws?token=<jwt>` |
| **Test IDs** | `tests/test_slice9alt_realtime.py::Slice9AltWebSocketTests` (6 tests) + `tests/test_realtime.py` (broadcast manager helpers) |
| **Status** | ✅ |
| **Evidence** | Slice 9-alt added real E2E WebSocket tests using a uvicorn server on a free port + the `websockets` client library. Verified: auth on connect (no token → HTTP 4xx; bad token → HTTP 4xx; valid token → accepts); `push_habit_list` → `habit_list_ack` echo + `habit_list_changed` broadcast to other devices; cross-user isolation; HTTP tick (`POST /api/v1/habits/{id}/completions`) → `tick_changed` broadcast to other devices. Existing `test_realtime.py` only tested the broadcast manager with a FakeWebSocket — Slice 9-alt is the first real E2E. |

### 11.2 Daily-change timer (midnight rollover)

| Field | Value |
| --- | --- |
| **Workflow** | User leaves app open across midnight; home grid auto-refreshes with new "today" column |
| **Current route** | /gui |
| **Entry point** | `frontend/index_page.py::refresh_habit_list_when_today_changes` |
| **User role** | Authenticated, long-running session |
| **User action** | n/a (timer) |
| **Expected result** | Grid re-renders with new dates |
| **Data read/written** | None |
| **Side effects** | WebSocket broadcast to other sessions |
| **Error/empty/loading cases** | None |
| **Existing code paths** | `ui.timer(60, refresh_if_needed)` |
| **Proposed new UI path** | Not yet implemented (Astro pages re-fetch on full reload; no client-side timer) |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ❌ |
| **Evidence** | `grep -r "setInterval\|setTimeout" web/concepts/src/` shows no daily-rollover timer. |

---

## Group 12 — Other legacy features (lower priority)

### 12.1 API token management (display + rotate)

| Field | Value |
| --- | --- |
| **Workflow** | User opens /gui/tokens; sees API token; copies / rotates |
| **Current route** | `/gui/tokens` |
| **Entry point** | `frontend/tokens_page.py::tokens_page(user)` |
| **User role** | Authenticated |
| **User action** | Click Copy / Rotate |
| **Expected result** | Token displayed or rotated |
| **Data read/written** | Reads/writes `user_api_tokens` (Fernet-encrypted) |
| **Side effects** | None |
| **Error/empty/loading cases** | None |
| **Existing code paths** | `crud.get_user_api_token(user)`, `crud.rotate_user_api_token(user)` |
| **Proposed new UI path** | **Missing** — no Astro `/tokens` page |
| **API/backend path** | (no backend endpoint) |
| **Test IDs** | `tests/test_api_tokens.py` (passes at API level; UI missing) |
| **Status** | ❌ |
| **Evidence** | `ls web/concepts/src/pages/tokens.astro` → not found. `AUDIT-2026-09-23.md` row 2 marks this. |

### 12.2 Per-habit completion status chip sets

| Field | Value |
| --- | --- |
| **Workflow** | User opens /gui/completion-status; sets default status chips (e.g. "yes:green", "no:red"); used as the default mapping on home grid |
| **Current route** | `/gui/completion-status` |
| **Entry point** | `frontend/chip_sets_page.py::chip_sets_page(user)` |
| **User role** | Authenticated |
| **User action** | Edit chip list; save |
| **Expected result** | `user_configs.default_chips` + `default_chips_mapping` updated; home grid uses them |
| **Data read/written** | Writes `user_configs` |
| **Side effects** | None |
| **Error/empty/loading cases** | Invalid format → kept as-is |
| **Existing code paths** | `views.update_default_chips(user, chips, mapping)` |
| **Proposed new UI path** | **Missing** — no Astro page |
| **API/backend path** | (no backend endpoint) |
| **Test IDs** | None |
| **Status** | ❌ |
| **Evidence** | `grep -r "completion-status\|chip_sets" web/concepts/src/` returns nothing. `AUDIT-2026-09-23.md` row 3 marks this as Low priority. |

### 12.3 Paddle pricing page

| Field | Value |
| --- | --- |
| **Workflow** | User opens /pricing; sees Free vs Pro pricing; clicks Buy on Pro |
| **Current route** | `/pricing` |
| **Entry point** | `frontend/pricing_page.py::pricing_page()` |
| **User role** | Anonymous or authenticated |
| **User action** | Click Buy |
| **Expected result** | Paddle checkout opens |
| **Data read/written** | Writes `customer` row (via webhook) |
| **Side effects** | Paddle webhook may flip `is_active` |
| **Error/empty/loading cases** | Paddle misconfigured → "Pricing temporarily unavailable" |
| **Existing code paths** | `plan/paddle.py` |
| **Proposed new UI path** | **Dropped** — commercial feature, intentionally not migrated |
| **API/backend path** | n/a |
| **Test IDs** | None |
| **Status** | ❌ (intentional drop) |
| **Evidence** | Per `AUDIT-2026-09-23.md` and plan §Phase 3 — Paddle dropped for self-host. |

### 12.4 Google One Tap login

| Field | Value |
| --- | --- |
| **Workflow** | User opens /login; Google One Tap prompt appears; user picks account; JWT issued |
| **Current route** | `/login` |
| **Entry point** | `routes/google_one_tap.py::google_one_tap_login` |
| **User role** | Anonymous |
| **User action** | Click Google account |
| **Expected result** | JWT issued |
| **Data read/written** | Reads/writes `user` |
| **Side effects** | Google OAuth callback |
| **Error/empty/loading cases** | Disabled by config (`GOOGLE_ONE_TAP_ENABLED=False`) |
| **Existing code paths** | `routes/google_one_tap.py` |
| **Proposed new UI path** | **Dropped** |
| **API/backend path** | `POST /auth/google-one-tap/callback` (dropped) |
| **Test IDs** | None |
| **Status** | ❌ (intentional drop) |
| **Evidence** | `AUDIT-2026-09-23.md` row 7 marks this as Low priority. |

### 12.5 Habit image upload (notes)

| Field | Value |
| --- | --- |
| **Workflow** | User uploads an image as a daily note (e.g. meal photo) |
| **Current route** | n/a (called via `/assets` POST from frontend JS) |
| **Entry point** | `routes/routes.py:732` `upload_note_image` |
| **User role** | Authenticated |
| **User action** | Upload image |
| **Expected result** | Image saved; URL returned |
| **Data read/written** | Writes `user_images` (blob) |
| **Side effects** | None |
| **Existing code paths** | `storage/images.py::ImageStorage.save` |
| **Proposed new UI path** | Not in Astro (no note-image UI exists) |
| **API/backend path** | `POST /assets` (form file upload) |
| **Test IDs** | None |
| **Status** | ❌ |
| **Evidence** | No notes-image UI exists on either legacy or migration; feature exists at backend level but is unused. |

### 12.6 Habit-display preferences (streak badge, total badge, date columns)

| Field | Value |
| --- | --- |
| **Workflow** | User toggles "show streak badge" in /settings; choice persisted |
| **Current route** | `/gui/settings` (display preferences section) |
| **Entry point** | `frontend/settings_page.py` |
| **User role** | Authenticated |
| **User action** | Toggle a setting |
| **Expected result** | `user_configs` updated; home grid re-renders with new layout |
| **Data read/written** | Writes `user_configs` (JSON) |
| **Side effects** | None |
| **Existing code paths** | `views.cache_user_configs(user)` |
| **Proposed new UI path** | Astro reads from cookies (no backend write endpoint yet) |
| **API/backend path** | `GET /api/v1/habits/meta` returns `{order}` only; no write endpoint for display prefs |
| **Test IDs** | None |
| **Status** | 🟡 |
| **Evidence** | `settings.astro` §4 docstring: "Habit-display preferences… parked until the backend ships user_configs write endpoints — see docs/plan/migration-plan.md Phase 3 P2". |

### 12.7 Sort habits by Name / Category

| Field | Value |
|---|---|
| (see 2.10 for full detail) |
| **Status** | ❌ |

---

## Summary of gaps

**Blocking parity for cutover** (must be resolved before claiming parity):

| # | Item | Group | Severity |
| --- | --- | --- | --- |
| 1 | `/help` page (currently 404 from mobile MoreSheet) | 4.4 | High (visible 404 to every mobile user) |
| 2 | `/gui/tokens` UI (`/tokens`) | 12.1 | Medium |
| 3 | Habit-display preferences persistence | 12.6 | Medium (functional but not persisted server-side) |
| 4 | Import POST backend endpoint | 6.3 | Medium (page surfaces honest 404) |
| 5 | Stats date-range picker | 3.2 | Medium |
| 6 | CSV export | 6.2 | Low (legacy never had this either) |
| 7 | Last-key passkey removal UX | 5.3 | Low (backend correct; UI uses prompt()) |
| 8 | Change-password UX | 5.4 | Low (works; uses prompt() chain) |
| 9 | Sort-by-Name/Category menu | 2.10, 12.7 | Low |
| 10 | Daily-rollover timer | 11.2 | Low (most users reload) |
| 11 | `/completion-status` (chip set editor) | 12.2 | Low |
| 12 | Paddle pricing page | 12.3 | None (intentionally dropped) |
| 13 | Google One Tap | 12.4 | None (intentionally dropped) |
| 14 | WebSocket fan-out to Astro | 11.1 | None (mobile clients use it; Astro pages re-fetch) |
| 15 | Telegram backup config UI | 6.4 | Low |
| 16 | Custom CSS persistence backend | 4.2 | Low (textarea present; backend write endpoint pending) |
| 17 | Long-press notes-textarea on grid cells | 2.13 | Low (still in P2 polish) |
| 18 | rpId mismatch on apollo (`WEBAUTHN_RP_ID=localhost` vs origin `10.8.0.1`) | 1.2 | High (passkey login broken on existing apollo) |

**Net-new on migration branch**: items marked 🔵 (Private Circles, /terms, /privacy).

---

## Evidence chain summary

| Claim | Verified by |
| --- | --- |
| Live apollo runs NiceGUI | `ssh apollo 'docker exec wg-easy wget -q -O - http://10.8.0.1:8080/health' → OK`; OpenAPI includes `/auth/webauthn/*`; HTML page title = "Beaver Habit Tracker" |
| Migration branch has Astro + 35 pages | `find web/concepts/src/pages -name "*.astro" \| wc -l` |
| `astro check` clean | `cd web/concepts && pnpm exec astro check` → 0 errors, 0 warnings |
| Backend tests pass | `pytest tests/ --ignore=test_batch4_live` → 180 passed |
| `test_batch4_live` failures are env-gated | Tests probe `http://10.8.0.1:8080` (apollo VPN) and `sys.path.insert(0, "/opt/castor")` (apollo-only) |
| 46 commits ahead of origin on migration branch | `git status` |
| Existing data (<jrodux@gmail.com>, 7 habits, 1 passkey) | `python3 sqlite3 mode=ro` query of live apollo DB |
| rpId mismatch on apollo | `docker exec beaverhabits env` shows `WEBAUTHN_RP_ID=localhost` despite compose declaring `10.8.0.1` |
| `/help` 404 | `MoreSheet.tsx:133` `closeAndNavigate('/help')`; no `web/concepts/src/pages/help.astro` |
