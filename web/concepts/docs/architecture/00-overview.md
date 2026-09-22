# Castor — Source of Truth Architecture (Phase 0)

Companion to the migration plan at `docs/plan/migration-plan.md`. Both
documents are the canonical reference for what Castor is, what it was,
and how to extend it without referring back to the upstream
`daya0576/beaverhabits` code.

> Status legend: ✅ already shipped on the migration branch · 🟡 in
> flight (file exists, behaviour incomplete or type errors remain) ·
> ❌ not started.

---

## 1. The two histories

| | Upstream `daya0576/beaverhabits` | Castor (`BlueishDragonz/castor`) |
|---|---|---|
| Repo role | Public origin (Apache 2.0, archived lineage) | Active fork, default branch `main` |
| Local mirror | `/opt/castor/` on apollo (cloned `BlueishDragonz/castor`) | Same — the upstream code lives in this checkout under `beaverhabits/` |
| Front-end | NiceGUI server-rendered + Quasar/Vue components | Astro 7 SSR + shadcn/ui (React islands) — `web/concepts/` |
| State management | NiceGUI `app.storage` + per-page `ui.refreshable` | Astro `Astro.locals` per request + per-page `Astro.props` |
| Data layer | SQLite (habits in `habit_list.data` JSON blob), SQLAlchemy `User`/`WebAuthnCredential` tables | **Unchanged** — the Python backend stays. The migration is front-end only |
| Auth | `fastapi-users` JWT, `VersionedJWTStrategy` (castor addition) | Bearer JWT in `castor_token` httpOnly cookie + same backend |
| Deployment | Docker image baked from fork, served through wg-easy netns on apollo | Backend stays on apollo; Astro front-end can be deployed alongside it via the same reverse proxy |

The strategic decision (per the migration plan) is to decouple Castor
from upstream tracking. The fork on apollo is now the canonical
source: changes are committed, pushed, built, deployed as a custom
Docker tag (`castor:custom-YYYY-MM-DD`) on the `/opt/wg-net`
compose project. Never `:latest` from Docker Hub.

---

## 2. Repository layout (Castor, post-castor main)

```
castor/                            ← /opt/castor/ on apollo
├── beaverhabits/                  ← Python backend (unchanged from castor main)
│   ├── app/                       ← FastAPI app, auth, DB, webauthn routes
│   ├── core/                      ← Domain models (habit, completions)
│   ├── frontend/                  ← NiceGUI pages — LEGACY, frozen
│   ├── plan/                      ← Plan tier gating
│   ├── routes/                    ← /api/v1, /auth, /metrics
│   ├── storage/                   ← SQLite repo, HabitList JSON
│   ├── configs.py                 ← pydantic-settings
│   ├── css_sanitizer.py           ← Hardened CSS allowlist
│   ├── integrity.py               ← habits.db.integrity.json check
│   ├── main.py                    ← App factory, gunicorn entrypoint
│   ├── metrics_app.py             ← Prometheus /metrics
│   ├── realtime.py                ← WebSocket sync
│   └── events.py                  ← Domain events
├── web/concepts/                  ← NEW Astro + shadcn front-end
│   ├── astro.config.mjs
│   ├── package.json               ← typescript@^5.9.3 (pinned)
│   ├── tsconfig.json              ← extends astro/tsconfigs/base
│   ├── src/
│   │   ├── layouts/Layout.astro
│   │   ├── lib/{auth,utils}.ts
│   │   ├── middleware.ts
│   │   ├── components/{BottomNav,DesktopMenu,MoreSheet,HabitGrid,HabitCheckBox,Heatmap,HabitContextSheet}.astro
│   │   ├── components/{SecurityContent,SettingsClient,DesktopMenu,DesktopMenuContent,MoreSheet,HabitContextSheet}.tsx
│   │   ├── components/ui/         ← shadcn primitives
│   │   ├── pages/{login,register,forgot-password,reset-password,settings,stats,security,import,logout,index,habits/{index,[id],new,order,[id]/edit,[id]/complete,api/...}}.astro
│   │   ├── env.d.ts
│   │   └── styles/tokens.css      ← oklch() tokens, light/dark
│   └── scripts/
├── tests/                         ← Pytest suite (castor main)
├── docker/Dockerfile
├── docker-compose.yml
├── pyproject.toml                 ← uv-managed; uv.lock committed
└── healthcheck.py
```

---

## 3. Front-end (Astro) — current state on `migration-to-shadcn-astro`

Already committed on the migration branch (six commits, plus ~17
in-flight modifications in the working tree). What exists:

### Routes (Astro pages)

| Route | Status | Notes |
|---|---|---|
| `/` | ✅ | Redirects to `/login` or `/habits` based on session |
| `/login` | 🟡 | Type error: `userHandle`/`signature`/`authenticatorData` not on `AuthenticatorResponse`. Backend path is `/auth/login`, body `username`+`password` form-encoded. Needs WebAuthn passkey path (login form has the button but it calls wrong fields). |
| `/register` | 🟡 | Type error: `attestationObject` not on `AuthenticatorResponse`. Otherwise working. |
| `/forgot-password` | ✅ | Server-side POSTs to backend `/auth/forgot-password` |
| `/reset-password` | ✅ | Server-side POSTs to `/auth/reset-password`; 12-digit code |
| `/logout` | ✅ | POST clears cookies, redirects to `/login` |
| `/account/delete` | ✅ | Standalone delete flow (no longer requires dialog from menu) |
| `/habits` | 🟡 | Renders `HabitGrid` with 7-day column default. Grid renders, but date cell toggle is wired only via a single form post; see "open work" below. |
| `/habits/[id]` | 🟡 | Streak, history, best streak. Some long-press affordances missing. |
| `/habits/new` | 🟡 | 3 TS errors: `<input class=…>` should be `className=…` |
| `/habits/order` | 🟡 | Drag-drop reorder UI present; not yet wired to `/api/v1/habits` re-order endpoint |
| `/stats` | 🟡 | Heatmap per habit, no aggregate yet |
| `/settings` | 🟡 | Theme + custom CSS; missing Help/PWA/Import/Export entries (now moved to More sheet) |
| `/security` | 🟡 | **Stub**: 22-line file that mounts `SecurityContent` React island, which hits `/api/v1/auth/webauthn/credentials` — a path that **does not exist**. Real backend mount is `/auth/webauthn/credentials`. A 342-line `security.astro.bak` exists from an earlier attempt (probably also wired wrong). |
| `/import` | 🟡 | Page exists (262 lines), import logic untested against backend |

### Components

- `Layout.astro` — global shell, theme cookie, slots, mounts `BottomNav` + `DesktopMenu` always
- `BottomNav.astro` — fixed mobile bar with Home/Stats/More, hidden ≥641px, safe-area inset
- `MoreSheet.astro/.tsx` — sectioned sheet (Account/Data/Session), uses shadcn `Sheet` (side=bottom)
- `DesktopMenu.tsx` — top-right desktop menu (Add, Tools, Security, Help, Logout)
- `HabitGrid.astro` — 484-line component (the migration's centrepiece); date headers sticky, 7-day column default, tag filtering supported
- `HabitCheckBox.astro` — single-day toggle cell
- `HabitContextSheet.astro/.tsx` — long-press drawsheet for Edit/Duplicate/Archive
- `Heatmap.astro` — 15-week heatmap for `/stats` and habit detail
- `SecurityContent.tsx` — client-side WebAuthn flows (currently broken: wrong endpoint, wrong types)
- `SettingsClient.tsx` — theme toggle + custom CSS
- shadcn/ui primitives: `button`, `card`, `sheet`, `separator`, `input`, `label`, `dialog`, `dropdown-menu`, `tabs`, `tooltip`, `scroll-area`, `context-menu`

### Lib

- `src/lib/auth.ts` — `readToken`, `writeSession`, `clearSession`, `readSession`, `backendFetch`. Cookie name `castor_token`. `SameSite=Lax`, `httpOnly=true`, `secure=false` (dev). **In prod behind HTTPS this must flip to `secure=true`** (see plan §1.3).
- `src/middleware.ts` — populates `Astro.locals.session` from cookies

---

## 4. Back-end (Python, unchanged) — for reference

Castor's hardening lives here. The Astro front-end MUST preserve it.

### 4.1 `beaverhabits/app/users.py` — auth strategy

- `UserManager.validate_password` enforces `len(password) >= 12` on register, change-password, reset, webauthn-credential-delete confirmation.
- `UserManager._update` **increments `User.token_version` in the same SQL UPDATE as the password hash** — this is the revocation anchor. The plain `JWTStrategy` would not provide it.
- `VersionedJWTStrategy.write_token` writes `{sub, aud, ver}` JWT claims.
- `VersionedJWTStrategy.read_token` re-SELECTs the user (`populate_existing=True`) and compares JWT `ver` to `user.token_version`. Mismatch → `None`. Legacy JWTs without `ver` are rejected.
- `get_cookie_settings()` returns `{httponly, samesite=strict, secure=APP_URL.startswith("https://")|TLS_TERMINATED}` — for the backend's own cookie if it ever sets one. The Astro side has its own `SameSite=Lax` (intentional — see plan §1.3).

### 4.2 `beaverhabits/app/webauthn_routes.py` — passkey endpoints

Mounted at `prefix="/auth/webauthn"` (router created at line 35):

- `POST /auth/webauthn/register/begin` → returns `{publicKey: …}` options
- `POST /auth/webauthn/register/complete` → accepts attestation, writes credential
- `POST /auth/webauthn/login/begin` → returns assertion challenge
- `POST /auth/webauthn/login/complete` → returns `{access_token: …}` (JWT)
- `POST /auth/webauthn/change-password` → bumps `token_version`
- `DELETE /auth/webauthn/credentials/{credential_id}` → deletes one credential
- `GET /auth/webauthn/credentials` → list user's credentials (used by `/security`)

> **The current `SecurityContent.tsx` calls `/api/v1/auth/webauthn/credentials` — that path does not exist.** Backend mount is `/auth/webauthn/credentials`. Fix in P1.

### 4.3 `beaverhabits/app/http_security.py` — `BrowserOriginMiddleware`

Cross-origin browser writes are rejected (CSRF defense). Native clients without `Origin` are allowed. Engine.IO socket handshakes guarded. Audit events written for `/auth/forgot-password`, `/auth/reset-password`, `/auth/logout`.

### 4.4 `beaverhabits/app/audit.py` — audit log

Every privileged action emits a row: `register`, `login`, `password_change`, `account_delete`, `passkey_register`, `passkey_delete`, `password_reset_request`, `password_reset`, `logout`. The Astro front-end should not bypass this — all writes go through the backend endpoints, which already record audit events.

### 4.5 `beaverhabits/app/middelwares.py` — empty

Empty file: `BrowserOriginMiddleware` was moved to `http_security.py`. Do not re-add a middleware here; the Astro front-end does not need a Python-side origin check (the proxy terminates).

### 4.6 `beaverhabits/routes/api.py` — habit endpoints

Mounted at `/api/v1` (from `main.py`):

- `GET/PUT /api/v1/habits/meta` — user preferences (INDEX_SHOW_HABIT_STREAK, INDEX_SHOW_HABIT_COUNT, INDEX_HABIT_NAME_COLUMNS, theme, custom CSS, etc.)
- `GET /api/v1/habits` — full list with completions
- `POST /api/v1/habits` — create
- `GET/PUT/DELETE /api/v1/habits/{id}` — read/update/delete
- `GET/POST /api/v1/habits/{id}/completions` — daily ticks
- `GET /api/v1/habits/export` — JSON
- `DELETE /api/v1/account` — delete account
- WebSocket `/api/v1/sync/ws` — real-time sync (still NiceGUI/WebSocket; not used by Astro)

### 4.7 `beaverhabits/configs.py` — settings (relevant subset)

- `JWT_SECRET`, `JWT_LIFETIME_SECONDS`
- `APP_URL` (used to flip `secure` on cookies and `WEBAUTHN_ORIGIN`)
- `WEBAUTHN_RP_ID` (must be `10.8.0.1` for VPN deploys, not `localhost`)
- `WEBAUTHN_ORIGIN` (`http://10.8.0.1:8080` in production)
- `WEBAUTHN_TIMEOUT` (ms)
- `INDEX_HABIT_NAME_COLUMNS`, `INDEX_SHOW_HABIT_STREAK`, `INDEX_SHOW_HABIT_COUNT` (consumed by grid column math)
- `ENABLE_TAG_FILTERS`, `ENABLE_IOS_STANDALONE`
- `UMAMI_ANALYTICS_ID`, `UMAMI_SCRIPT_URL` (consumed by the legacy `layout.py` `custom_headers`; Astro side has not ported this yet)
- `TLS_TERMINATED` (controls backend cookie `secure`)

---

## 5. Hardening catalog: what's already shipped on Castor main

Each item was added by the castor fork relative to upstream; the
migration must preserve it. Items marked **"TODO"** are present in
the fork's source but not actually wired up — the migration should
treat them as not-shipped until verified.

### 5.1 Auth & session

- ✅ `VersionedJWTStrategy` with `token_version` enforcement (users.py)
- ✅ Password policy ≥12 chars (users.py)
- 🟡 Token-version bump on password reset (reset_routes.py TODO; treat as not shipped)
- ✅ Browser-origin CSRF middleware (http_security.py)
- ✅ Audit log on register/login/change/delete/passkey (audit.py)
- ✅ WebAuthn self-binding (username must match signed-in email) (webauthn_routes.py:58)
- ✅ Stable browser cookie `beaver_webauthn` for concurrent tabs (webauthn_routes.py:71)

### 5.2 Operational

- ✅ `integrity.py` + `habits.db.integrity.json` post-deploy check
- ✅ `metrics_app.py` Prometheus `/metrics`
- ✅ `realtime.py` WebSocket sync
- ✅ `start.sh` healthcheck + restart loop
- ✅ Custom Docker build (`castor:custom-YYYY-MM-DD`)

### 5.3 UX (legacy NiceGUI)

- ✅ Sticky date headers on habit grid (index_page.py:41)
- ✅ Bottom nav for mobile (bottom_nav.py) + sectioned More sheet (more_sheet.py)
- ✅ WebAuthn enrollment UI with focus management + reduced-motion respect (security_page.py:54)
- ✅ Tag filter component (`filter_habits_with_tags`)
- ✅ Calendar heatmap (15 weeks) on stats + habit detail
- ✅ Custom CSS sanitizer (css_sanitizer.py)
- ✅ Long-press event for habit context menu
- ✅ Self-hosted Inter font (replaces Quasar Roboto)
- ✅ BH design tokens (BH_DESIGN_CSS)

The Astro port **needs** to carry forward every UX item in §5.3
without losing parity. See Phase 3.

---

## 6. Feature & flow catalogue (current NiceGUI behaviour to preserve)

Each row lists the screens/states a user touches. The Astro port
must reproduce each row with parity (functional, not necessarily
visual). `castor main` vs upstream differences are footnoted.

### Sign-up
1. `/register` — form: email + password (≥12 chars)
2. Client POST form-encoded to `/auth/register`
3. `UserManager.on_after_register` → audit `register`; auto-login
4. Redirect `/login` → `/habits`
   *Castor-only: 12-char password policy enforced server-side.*

### Login (password)
1. `/login` — form: email + password
2. POST form-encoded to `/auth/login` (NOT `/auth/jwt/login` — that path 404s)
3. Backend returns `{access_token, token_type}` JSON
4. Astro stores in `castor_token` httpOnly cookie (SameSite=Lax), redirects `/habits`
5. Every server-side `backendFetch` reads cookie and sets `Authorization: Bearer`
   *Castor-only: token_version checked on every read.*

### Login (passkey)
1. `/login` — click "Use passkey"
2. Client POSTs `{username}` to `/auth/webauthn/login/begin`
3. Backend sets `beaver_webauthn` cookie, returns assertion challenge
4. Browser calls `navigator.credentials.get(...)` → returns assertion
5. Client POSTs `{username, id, rawId, type, response}` to `/auth/webauthn/login/complete`
6. Backend verifies (rpId hash match, browser cookie match, challenge consumed), returns `{access_token}`
7. Astro stores same as password path

### Forgot / reset password
1. `/forgot-password` — form: email
2. POST to `/auth/forgot-password` (form-encoded)
3. Backend sends 12-digit code email
4. `/reset-password` — form: code + new password
5. POST to `/auth/reset-password`
6. On success: token_version bumped (TODO in fork); user re-logs in

### Habit CRUD
1. `/habits` — grid: sticky date headers (7 default), tag filter chips, habit rows
2. Click row → `/habits/[id]`
3. Click "+" or "Add" → `/habits/new` → POST `/api/v1/habits`
4. Long-press row → `HabitContextSheet` (Edit / Duplicate / Archive / Reorder)
5. Edit → `/habits/[id]/edit` → PUT `/api/v1/habits/{id}`
6. Delete from menu → DELETE `/api/v1/habits/{id}` (no confirm dialog yet)
7. Reorder → `/habits/order` drag-drop → PUT `/api/v1/habits/meta` with new order

### Daily tick
1. Click date cell in grid → optimistic toggle → POST `/api/v1/habits/{id}/completions?date=YYYY-MM-DD`
2. Notes: long-press cell → inline textarea → POST same endpoint with `text`

### Stats
1. `/stats` — one card per habit: name + 15-week heatmap + streak
2. No aggregate stats yet (intentional — kept minimal)

### Settings
1. `/settings` — theme toggle (light/dark), custom CSS editor, danger zone (delete account)
2. Theme toggle writes `castor-theme` cookie + PUTs `/api/v1/habits/meta`
3. Custom CSS goes through `css_sanitizer.py` on save
4. Delete account → confirm dialog → DELETE `/api/v1/account`

### Security
1. `/security` — passkey list + add/remove, password change, recovery email
2. Add passkey: nickname → POST `/auth/webauthn/register/begin` → `navigator.credentials.create` → POST `/auth/webauthn/register/complete`
3. Remove passkey: confirm password → DELETE `/auth/webauthn/credentials/{id}`
4. Change password: current + new + confirm → POST `/auth/webauthn/change-password` (or `/users/me/password/change` — both exist; pick one)

---

## 7. Where the Astro port diverges from castor main

Each divergence must be resolved before the migration can claim
"feature parity with castor main".

| # | Divergence | Owner | Severity |
|---|---|---|---|
| D1 | `SecurityContent.tsx` calls `/api/v1/auth/webauthn/credentials`; backend mounts `/auth/webauthn/credentials` | P1 | Critical (page broken) |
| D2 | `security.astro.bak` (342 lines) is dead code alongside the 22-line stub | P1 | Cleanup |
| D3 | `login.astro` / `register.astro` reference `userHandle`, `signature`, `attestationObject`, `authenticatorData` on `AuthenticatorResponse` — types are wrong | P1 | TS errors block `astro check` |
| D4 | `habits/new.astro` uses `class=` on React `<input>` (3 sites) | P1 | TS errors |
| D5 | `/import` page exists (262 lines) but no test against `/api/v1/habits/import` (or whatever endpoint exists) | ✅ Shipped — `/export` rewritten as server-side GET proxy with `Content-Disposition: attachment`; `/import` rewritten as server-side POST handler. Backend has only `GET /api/v1/habits/export`; the POST `/api/v1/habits/import` endpoint is a backend TODO and the Astro page surfaces that explicitly with a 404 message instead of a silent client-side fetch. `__IMPORT_TOKEN__` window global removed (XSS exposure — bearer now reaches backend only via httpOnly cookie). |
| D6 | `/settings` missing Help, Import/Export entries (now in More sheet — verify nav still works) | ✅ Shipped — Help card with shadcn Dialog + four links (Wiki, Supporter, YouTube, Issues) on `/settings`; Import/Export remain accessible via `MoreSheet → Data` per the migration plan |
| D7 | `auth.ts` cookie `secure=false` in dev; `secure=true` flip not yet done in any environment | ✅ Shipped — `isProd()` in `lib/auth.ts` reads `PUBLIC_BACKEND_URL` / `BACKEND_URL` / `TLS_TERMINATED` and flips `Secure` accordingly |
| D8 | No PWA meta tags ported from `layout.py::pwa_headers` | ✅ Shipped — `Layout.astro` carries apple-touch-icon, theme-color, manifest link; `public/manifest.webmanifest` written |
| D9 | Long-press event not ported from `intersection_observer.js`/`long-press-event.min.js` | P2 | UX gap |
| D10 | No Umami analytics port (`UMAMI_ANALYTICS_ID`) | P3 | Observability |
| D11 | Bridge `beaver_webauthn` → `castor_webauthn_browser` so WebAuthn ceremonies round-trip via Astro API routes; server-side `castor_token` httpOnly write (impossible from JS) | ✅ Shipped — `/api/auth/webauthn/login/complete.ts` and `/register/complete.ts`, `mirrorWebAuthnBrowserCookie()` in `lib/auth.ts` |
| D12 | No "Best streaks" UI on `/habits/[id]` (computed but not rendered) | P2 | Parity gap |
| D13 | No `tag_filter_component` chip UI in Astro grid (HabitGrid has props for it; chips not rendered) | P2 | Parity gap |
| D14 | `clearSession` doesn't invalidate JWT server-side (token_version gate is server-side anyway, so this is fine — but logout should still bump version for symmetry) | 🟡 Backend gap — `/auth/logout` HTTP endpoint doesn't exist yet; the only `user_logout()` helper is NiceGUI-side. Astro-side `clearSession` correctly drops cookies; the JWT remains technically valid until lifetime expires. Castor's `VersionedJWTStrategy` will reject any future password-change-bound token, which closes most of the risk. **Documented as Phase 3 backend TODO.** |
| D15 | `/habits/order` UI exists but no test of PUT to `/api/v1/habits/meta` | P2 | Behaviour unknown |
| D16 | `/account/delete` confirmation dialog — straight POST from `/settings` | ✅ Shipped — `/account/delete` now requires password + typed "DELETE" + new `/api/account/delete` server route that re-verifies via `/auth/login` before forwarding `DELETE /api/v1/account` |
