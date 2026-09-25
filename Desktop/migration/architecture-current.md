# Castor — Current Architecture (as deployed on 2026-09-23)

> Phase 1 deliverable. Companion to `feature-inventory.md` and
> `parity-matrix.md`. Evidence recorded with commands; verify by
> re-running them.

## 0. Scope of "current"

The phrase "current" is ambiguous in this repo. Two systems exist:

| System | Branch / path | Live status | Source-of-truth |
|---|---|---|---|
| **Legacy NiceGUI/Quasar app** | `main` branch, `beaverhabits/` package | **Live on apollo** (castor-beaverhabits image, `castor:custom-2026-09-14`, branch `main`, commit `4f91ace` "fix: castor security review — wave 1/2/3 fixes") | The container is running this; `/opt/castor/` on apollo mirrors the fork |
| **Astro + shadcn migration** | `migration-to-shadcn-astro` branch, `castor/` (Python) + `web/concepts/` (Astro) | **NOT live anywhere.** Untracked commits ahead of `origin/migration-to-shadcn-astro` by 46. Local-only Docker-compose (`docker-compose.yml`) and image build (`docker/Dockerfile`) exist but no deployed instance. The migration branch has accumulated significant drift from the existing docs (`ASTRO_MIGRATION_GAP_REPORT.md`, `AUDIT-2026-09-23.md`, `web/concepts/docs/architecture/00-overview.md`) — most of the gaps those docs flag are now closed in code. |

A local mirror of the legacy source also lives at `~/beaverhabits-backup-2026-09-12-COPY2/bh-app-20260912/` (from a 2026-09-12 snapshot). Treat the live container as authoritative; treat the laptop backup as a stale read-only reference.

Evidence:
```bash
$ cd /home/joel/castor-repo && git status
On branch migration-to-shadcn-astro
Your branch is ahead of 'origin/migration-to-shadcn-astro' by 46 commits.

$ ssh apollo 'cd /opt/castor && git log --oneline -3 && git branch --show-current'
4f91ace fix: castor security review — wave 1/2/3 fixes
8cb7f3c style: remove card glow, compact habit rows (padding -6px, row gap 2px)
e106ed3 style: promote security page design tokens to app-wide system (Phases 1-7)
main

$ ssh apollo 'sudo /usr/bin/docker inspect beaverhabits --format "{{.Config.Image}} {{.Created}}"'
castor-beaverhabits 2026-09-18T17:04:01.596807381Z
```

The castor skill (`apollo-beaverhabits`) confirms the live tag is `castor:custom-2026-09-14` and the active container's source is `/opt/castor/` on apollo — both true today.

## 1. Live system: NiceGUI/Quasar on apollo

### 1.1 Repository layout (`main` branch)

```
/opt/castor/                                       on apollo, owner ubuntu
├── beaverhabits/                                  ← Python package
│   ├── app/
│   │   ├── app.py                FastAPI app factory; mounts fastapi-users routers at /auth, /users
│   │   ├── auth.py               authenticate, token, password reset
│   │   ├── crud.py               DB read/write helpers
│   │   ├── db.py                 SQLAlchemy async models (user, webauthn_credential, habit_list,
│   │   │                          user_api_tokens, user_configs, user_images, customer,
│   │   │                          password_reset_code)
│   │   ├── dependencies.py       current_active_user, current_admin_user, get_reset_user
│   │   ├── http_security.py      BrowserOriginMiddleware (CSRF), security header middleware
│   │   ├── rate_limits.py        IPRateLimitMiddleware + per-key async consume()
│   │   ├── reset_routes.py       12-digit code password reset (/auth/forgot-password + /auth/reset-password + /auth/reset-password/check)
│   │   ├── webauthn_routes.py    /auth/webauthn/{register,login,change-password,credentials,recovery-email}
│   │   ├── users.py              UserManager + VersionedJWTStrategy (token_version enforcement)
│   │   └── middelwares.py        empty (placeholder)
│   ├── core/                     Domain: completions.py, backup.py
│   ├── frontend/                 NiceGUI pages — 23 modules + 2 JS files
│   │   ├── index_page.py         Home grid (date columns, sticky headers, badges)
│   │   ├── add_page.py           Add/list/star/delete habit
│   │   ├── habit_page.py         Habit detail (streak, history, heatmap, best streaks, notes)
│   │   ├── stats_page.py         Per-habit + date-range stats
│   │   ├── order_page.py         Drag-drop reorder
│   │   ├── security_page.py      Passkey list/add/remove + password change + recovery email
│   │   ├── settings_page.py      Theme + custom CSS + Help dialog trigger
│   │   ├── import_page.py        JSON/CSV upload + DictHabitList merge
│   │   ├── export_page.py        JSON download + Telegram backup config + delete account
│   │   ├── tokens_page.py        API token management (display/rotate)
│   │   ├── chip_sets_page.py     Per-habit completion status chip mapping
│   │   ├── admin.py              Superuser-only user list + Paddle promote/demote
│   │   ├── pricing_page.py       Paddle pricing page (commercial-only)
│   │   ├── paddle_page.py        Paddle webhook handler (commercial-only)
│   │   ├── bottom_nav.py         Fixed mobile bottom nav (Home/Stats/More)
│   │   ├── more_sheet.py         Sectioned bottom sheet (Account/Data/Session)
│   │   ├── layout.py             PWA headers, SEO meta, Umami, custom CSS injection, layout shell
│   │   ├── components.py         bh_card, HabitCheckBox, HabitAddButton, HabitDeleteButton,
│   │   │                          HabitNameInput, HabitStarCheckbox, HabitTickDialog,
│   │   │                          tag_filter_component, habit_history, habit_notes,
│   │   │                          habit_heat_map, CalendarHeatmap, note_tick, redirect
│   │   ├── css.py                YOUTUBE_CSS, CHECK_BOX_CSS, NOTE_CSS, etc.
│   │   ├── javascript.py         PADDLE_JS, Vanta.js, date picker JS
│   │   ├── icons.py              sym_o_* icon shorthand
│   │   ├── design_tokens.py      Colour palette + spacing scale
│   │   ├── device.py             is_mobile(user_agent)
│   │   ├── intersection_observer.py
│   │   ├── menu.py               Desktop menu (Add, Tools, Security, Help, Logout)
│   │   ├── streaks.py            Calendar heatmap (53 weeks)
│   │   ├── security_passkeys.py  WebAuthn client JS injection
│   │   └── security_passkeys.js  WebAuthn navigator.credentials helpers
│   ├── plan/                     Paddle integration (plan gating)
│   ├── routes/
│   │   ├── routes.py             @ui.page(...) — 27 NiceGUI pages, 2 endpoints
│   │   ├── api.py                /api/v1/* REST router (habits, completions, account, sync_ws)
│   │   ├── metrics.py            Prometheus /metrics
│   │   └── google_one_tap.py     Google OAuth callback
│   ├── storage/                  SQLite HabitList JSON, file-based and dict-based implementations
│   ├── views.py                  Cross-cutting helpers: forgot_password, reset_password,
│   │                              export_user_habit_list, register_user, login_user,
│   │                              set_user_cookies, apply_theme_style, cache_user_configs
│   ├── css_sanitizer.py          tinycss2-based allowlist for user CSS
│   ├── integrity.py              Daily SQLite integrity check
│   ├── realtime.py               WebSocket connection manager
│   ├── events.py                 Domain event publisher (HabitListChanged)
│   ├── scheduler.py              daily_backup_task (optional, disabled by default)
│   ├── metrics_app.py            Prometheus FastAPI app
│   ├── configs.py                pydantic-settings (Settings)
│   └── main.py                   App factory, lifespan, middleware, gunicorn entrypoint
├── tests/                        36 pytest modules (lane1, lane2, priority1, batch1-4)
├── docker/Dockerfile             python:3.14-slim, uv-managed venv, strips nicegui/[mermaid|plotly|vanilla-jsoneditor]
├── docker-compose.yml            beaverhabits service: image=castor:custom-2026-09-14
├── start.sh                      gunicorn launcher
├── healthcheck.py                /health probe (host python: requests.get → 200)
├── pyproject.toml                uv-managed; nicegui[redis], fastapi-users[sqlalchemy], webauthn
└── statics/                      PWA manifest, icons, vanta libs, three.js
```

### 1.2 Deployment (apollo)

```yaml
# /opt/wg-net/docker-compose.yml (live)
services:
  wg-easy:    { image: ghcr.io/wg-easy/wg-easy:15, network wg, ports 51820/udp + 51821/tcp }
  pihole:     { image: pihole/pihole:latest, network_mode: service:wg-easy, env_file: /opt/pihole/pihole.env }
  beaverhabits:
    image: castor:custom-2026-09-14
    container_name: beaverhabits
    depends_on: [wg-easy]
    network_mode: service:wg-easy
    environment:
      - HABITS_STORAGE=DATABASE
      - WEBAUTHN_RP_ID=10.8.0.1       # declared in compose
      - WEBAUTHN_ORIGIN=http://10.8.0.1:8080
    volumes:
      - beaver_data:/app/.user/
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "python healthcheck.py"]
      interval: 30s
      timeout: 10s
      retries: 3
```

Runtime env (from `docker exec beaverhabits env`, redacted):
```
HABITS_STORAGE=DATABASE
WEBAUTHN_RP_ID=localhost           # ⚠ overrides compose — env file wins
WEBAUTHN_ORIGIN=http://localhost:8080
VENV_PATH=/opt/pysetup/.venv
```
The image's own `.user/.secrets.env` (mounted at `/app/.user/.secrets.env`) is loaded by `configs.py` and sets:
```
ENV=production
JWT_LIFETIME_SECONDS=2592000
JWT_SECRET=<64-char hex>
RESET_PASSWORD_TOKEN_SECRET=<64-char hex>
NICEGUI_STORAGE_SECRET=<64-char hex>
TOKEN_ENCRYPTION_KEY=<fernet key>
SMTP_EMAIL_USERNAME=jota.rodrigues.89@gmail.com
SMTP_EMAIL_PASSWORD=<google app pwd>
INVITE_REQUIRED=true
INVITE_CODE=beaver-4619c8a4dd4cb2d3
```

The compose `WEBAUTHN_RP_ID=10.8.0.1` is overridden because the env file declares it as `localhost`. **This is a known open issue** (apollo-beaverhabits skill + AUDIT-2026-09-23.md) — rpId mismatch means passkey login from the browser breaks against VPN origin. New account registrations would fail rpId validation; existing registered credentials may still work in some browsers.

### 1.3 Network exposure

- **No public port.** The app shares `wg-easy`'s netns and is reachable only at `http://10.8.0.1:8080` from VPN peers.
- OCI ingress rule for TCP 51821 is open (admin UI), but TCP 8080 is NOT exposed.
- DNS via Pi-hole (10.8.0.1:53).

Reachability from homelab2 (verified 2026-09-23):
```bash
$ /usr/bin/docker exec wg-easy wget -q -O - --timeout=10 http://10.8.0.1:8080/health
OK

$ timeout 10 python3 -c "import urllib.request; print(urllib.request.urlopen('http://10.8.0.1:8080/openapi.json', timeout=5).read()[:300])"
{"openapi":"3.1.0","info":{"title":"FastAPI","version":"0.1.0"},"paths":{"/auth/...
```

23 API paths in OpenAPI spec — auth, users, webauthn (8 paths), habits, metrics, etc.

### 1.4 Persistence

- SQLite file: `/var/lib/docker/volumes/beaver_data/_data/habits.db` (mounted from `beaver_data` external volume).
- File size: 176 KB on 2026-09-22; `.secrets.env` (600) in same volume; `habits.db.integrity.json` (last good check 2026-09-23 18:59).
- Tables (read-only probe 2026-09-23):
  - `user` — 4 rows: `jrodux@gmail.com` (REAL, 7 habits 2779-byte JSON), `test@example.com`, `e2e@example.com` (1 token), `rotate-probe@example.com`.
  - `habit_list` — 2 rows: 2779-byte JSON for jrodux, 14-byte stub for e2e.
  - `webauthn_credential` — 1 row (jrodux's USB passkey).
  - `password_reset_code`, `user_api_tokens`, `user_configs`, `user_images`, `customer`.
  - **No `audit_event` table** — the running image predates the audit table addition.
- Schema migration: `create_db_and_tables()` does `create_all` + `ALTER TABLE` to add new columns idempotently.

### 1.5 Backups

- `ENABLE_DAILY_BACKUP` defaults to False; `daily_backup_task` is a no-op when disabled.
- Per-user backup is manual, via `/gui/export` (NiceGUI) which calls `views.export_user_habit_list()` → `ui.download(...)` for JSON, or via Telegram bot if user-configured (`/gui/settings` backup panel).
- **No automatic apollo-side backup job** for the SQLite file. The Apollo skill flags this as a known gap.

### 1.6 Dependencies (runtime)

```
nicegui 3.13.0       fastapi 0.138.0    fastapi-users 15.0.5
webauthn 3.0.0       sqlalchemy 2.0.51  aiosqlite 0.22.1
pydantic 2.13.4      pydantic-settings 2.14.2  gunicorn 26.0.0
uvicorn 0.49.0
sentry-sdk           MISSING  (env unset, ok)
paddle-python-sdk    MISSING  (Paddle disabled by default in ENV)
```

All deprecation warnings suppressed at boot via loguru / settings.

### 1.7 Tests (baseline 2026-09-23)

```
$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ -q \
    --ignore=tests/security_recovery_browser_acceptance.py \
    --ignore=tests/security_page_virtual_acceptance.py \
    --ignore=tests/check_auth_card_geometry.py \
    --ignore=tests/check_dialog_geometry_320.py \
    --ignore=tests/check_security_dom.py \
    --ignore=tests/check_security_summary_browser.py
192 tests collected; 12 failed in tests/test_batch4_live.py, 180 passed in 76.43s
```

The 12 failures are exclusively `test_batch4_live.py` — they probe `http://10.8.0.1:8080` (apollo VPN), runnable only from `/opt/castor` on apollo, not from the laptop. They are not regression failures; they are environment-gated.

## 2. Migration system: Astro + shadcn (NOT deployed)

### 2.1 Repository layout (`migration-to-shadcn-astro` branch, current `castor/` rewrite)

```
/home/joel/castor-repo/
├── castor/                              ← Python package (renamed from beaverhabits/)
│   ├── app/
│   │   ├── app.py              Same as legacy; mounts fastapi-users + webauthn_router
│   │   ├── admin_routes.py     NEW (P3-B): /api/v1/admin/{users,backup}
│   │   ├── audit.py            NEW: audit_event SQLAlchemy model + audit_retention_task
│   │   ├── auth.py             + user_bump_token_version helper
│   │   ├── challenges.py       NEW: WebAuthn challenge store (P1)
│   │   ├── circle_routes.py    NEW (P2): /api/v1/circles/* (13 endpoints)
│   │   ├── circles.py          NEW: 4-table SQLAlchemy schema (circle, circle_member,
│   │   │                        circle_habit, circle_invite) + CHECK constraints
│   │   ├── crud.py             + get_user_by_api_token, + get_or_create_user_identity
│   │   ├── db.py               + User.passkey_offer_dismissed, + recovery_email columns,
│   │   │                        + audit_event table, + recovery_email_challenge table
│   │   ├── dependencies.py     + current_admin_user (ADMIN_EMAIL single-string gate)
│   │   ├── http_security.py    Same BrowserOriginMiddleware; per-request audit for
│   │   │                       /auth/forgot-password, /auth/reset-password, /auth/logout
│   │   ├── rate_limits.py      + sensitive-action persistent budget
│   │   ├── reset_routes.py     NEW: 12-digit code reset, /auth/reset-password/check
│   │   ├── schemas.py          Pydantic user create/read/update
│   │   ├── security_actions.py NEW: Shared sensitive-actions service (P3-A) — central
│   │   │                        authorization for password change + passkey delete with
│   │   │                        token_version CAS, password policy, rate-limit budget
│   │   ├── users.py            Same UserManager + VersionedJWTStrategy
│   │   ├── webauthn_routes.py  Same endpoints + logout_router + recovery-email +
│   │   │                        passkey offer dismiss + offer screen
│   │   ├── middelwares.py      empty
│   ├── core/
│   │   ├── backup.py           Same: backup_to_telegram
│   │   ├── completions.py      Same
│   │   └── note.py             Same
│   ├── plan/                   Paddle removed
│   ├── routes/
│   │   ├── api.py              /api/v1/habits/* + WebSocket /api/v1/sync/ws
│   │   ├── astro.py            Old /astro mount (only used if ENABLE_PLAN)
│   │   └── metrics.py          Prometheus endpoint
│   ├── storage/                Same: dict, file, sqlite repo, HabitList JSON
│   ├── views.py                Same cross-cutting helpers
│   ├── css_sanitizer.py        Same
│   ├── demo_seed.py            NEW: idempotent demo account seeder (P2 demo replacement)
│   ├── events.py               Same HabitListChanged publisher
│   ├── integrity.py            Same
│   ├── realtime.py             Same WebSocket connection manager
│   ├── scheduler.py            Same
│   ├── metrics_app.py          Same Prometheus app
│   ├── configs.py              Same (byte-identical to beaverhabits/configs.py)
│   ├── const.py                Same page title constants
│   ├── logger.py               Same
│   ├── utils.py                Same
│   ├── accessibility.py        Same
│   ├── version.py              Same
│   └── main.py                 App factory; integrates demo_seed, audit_retention_task,
│                                admin_router; same middleware chain
├── web/concepts/                          ← Astro 7 + React 19 + Tailwind v4
│   ├── astro.config.mjs        output:'server', @astrojs/node standalone,
│   │                            Vite proxy /auth, /users, /webauthn, /health → BACKEND_URL
│   ├── package.json            typescript pinned to ^5.9.3; 18 shadcn primitives
│   ├── tsconfig.json           extends astro/tsconfigs/base (NOT strict — see
│   │                            migration-plan.md §Decisions)
│   ├── public/                 manifest.webmanifest, favicon.svg, icons/
│   ├── scripts/                dev/build/preview helpers
│   ├── src/
│   │   ├── env.d.ts            Ambient Window globals (__SECURITY_TOKEN__ etc.)
│   │   ├── middleware.ts       Populates Astro.locals.session from castor_token cookie
│   │   ├── layouts/Layout.astro    data-theme via cookie; mounts BottomNav + DesktopMenu
│   │   │                          when session present; PWA meta tags; Umami conditional
│   │   ├── lib/
│   │   │   ├── auth.ts         readToken/writeSession/clearSession/backendFetch/
│   │   │   │                    mirrorWebAuthnBrowserCookie (castor_token + castor_user
│   │   │   │                    + castor_webauthn_browser cookies; HttpOnly, SameSite=Lax,
│   │   │   │                    secure=isProd())
│   │   │   ├── circles.ts      NEW (P2): types + visibilityAllows()
│   │   │   ├── dates.ts        localISO(), pad2(), etc. — avoids toISOString() UTC drift
│   │   │   └── utils.ts        cn() helper
│   │   ├── components/
│   │   │   ├── ui/             shadcn primitives (button, card, sheet, dialog,
│   │   │   │                    dropdown-menu, tabs, scroll-area, context-menu, …)
│   │   │   ├── BottomNav.astro Fixed mobile nav (Home / Stats / More), hidden ≥641px
│   │   │   ├── DesktopMenu.tsx Right-side Sheet (Reorder, Import, Export, Stats,
│   │   │   │                    Security, Settings, Logout)
│   │   │   ├── MoreSheet.tsx   Bottom Sheet — Account (Security, Help, Circles),
│   │   │   │                    Data (Import, Export), Session (Log out)
│   │   │   ├── HabitGrid.astro 2D grid: habit rows × date columns (7 default),
│   │   │   │                    sticky date headers, today highlight, tag-filter
│   │   │   │                    chips, long-press context sheet
│   │   │   ├── HabitCheckBox.astro <form action="/habits/{id}/complete"> (no client
│   │   │   │                    bearer — server-side POST)
│   │   │   ├── HabitContextSheet.tsx Edit / Duplicate / Reorder / Archive
│   │   │   ├── HabitNoteDialog.tsx   Long-press → notes textarea (P2)
│   │   │   ├── HabitNoteRow.tsx      Per-day note row in detail
│   │   │   ├── Heatmap.astro         15-week calendar heatmap (replaces ECharts)
│   │   │   ├── SecurityContent.tsx   React island; passkeys + recovery email + password change
│   │   │   ├── SettingsClient.tsx    Custom CSS persistence
│   │   │   └── HelpDialog.tsx        Dialog with 4 links (Wiki / Supporter / YouTube / Issues)
│   │   ├── pages/
│   │   │   ├── index.astro           Redirect to /habits or /login
│   │   │   ├── login.astro           4-stage progressive disclosure FSM
│   │   │   ├── register.astro        Email + password ≥12
│   │   │   ├── forgot-password.astro
│   │   │   ├── verify-reset.astro    Stage 1 (verify 12-digit code) → Stage 2 (new pw)
│   │   │   ├── reset-password.astro  Legacy single-stage form (unused; verify-reset is preferred)
│   │   │   ├── logout.astro          POST → /auth/logout (bumps token_version) → /login
│   │   │   ├── account/delete.astro  Password + confirm DELETE
│   │   │   ├── habits/index.astro    Server-side render: reads habits + records + notes
│   │   │   ├── habits/new.astro
│   │   │   ├── habits/[id].astro     Streak, history, best streaks, heatmap, notes
│   │   │   ├── habits/[id]/edit.astro
│   │   │   ├── habits/[id]/complete.astro   POST → backend /api/v1/habits/{id}/completions
│   │   │   ├── habits/[id]/archive.astro
│   │   │   ├── habits/[id]/duplicate.astro
│   │   │   ├── habits/order.astro    Drag-drop reorder → PUT /api/v1/habits/meta
│   │   │   ├── stats.astro           Per-habit streak cards + 15-week heatmap
│   │   │   ├── settings.astro        Theme + custom CSS + Help + account actions
│   │   │   ├── security.astro        Stub that mounts <SecurityContent client:load />
│   │   │   ├── import.astro          POST multipart file → backend POST /habits/import
│   │   │   │                          (backend endpoint not yet shipped — graceful 404 message)
│   │   │   ├── export.astro          GET server-side → backend /api/v1/habits/export
│   │   │   ├── admin.astro           ADMIN_EMAIL-gated user list + backup trigger
│   │   │   ├── circles/index.astro
│   │   │   ├── circles/new.astro
│   │   │   ├── circles/[id]/{leave,members/[user_id]/remove,share}.astro
│   │   │   ├── terms.astro, privacy.astro
│   │   │   └── api/                  17 BFF endpoints (proxy → backend with cookie→Bearer)
│   │   │       ├── account/delete.ts
│   │   │       ├── admin/backup.ts
│   │   │       ├── auth/reset-password/check.ts
│   │   │       ├── auth/webauthn/{check,offer/dismiss,recovery-email/{index,verify}}.ts
│   │   │       ├── auth/webauthn/login/complete.ts
│   │   │       ├── auth/webauthn/register/{begin,complete}.ts
│   │   │       └── v1/habits{,/[id]}.ts
│   │   │       └── v1/habits/{export,import,reorder,[id]/notes,[id]/duplicate}.ts
│   │   └── styles/tokens.css     oklch() light/dark tokens
│   └── docs/
│       ├── architecture/00-overview.md        (STALE — predates current state)
│       ├── plan/migration-plan.md             (authoritative on slice ordering)
│       ├── security/{baseline,dogfood-findings,final-report}.md
│       └── design/system-spec.md
├── tests/                                  Same set as main, plus dropped upstream-specific
│                                              tests; 192 collect, 180 pass
├── docker/Dockerfile                       Same multi-stage as main; copies castor/ instead of beaverhabits/
├── docker-compose.yml                      service castor, image: castor (build context), volume castor_data
├── start.sh, healthcheck.py                Same
├── pyproject.toml, uv.lock                 Same deps (nicegui still required by package
│                                            since legacy auth.py imports it; this is a known
│                                            wart for P3 decoupling)
├── AUDIT-2026-09-23.md                     Recent audit (still mostly accurate)
├── ASTRO_MIGRATION_GAP_REPORT.md           Pre-Phase-0 gap report (mostly superseded)
└── README.md
```

### 2.2 Key architectural choices (from migration-plan.md §Decisions)

- **Astro 7 SSR + `@astrojs/node` standalone** — single Node process; SSR pages reachable from reverse proxy.
- **shadcn/ui (React) as the only component library** — per user preference, no per-surface best-of-breed.
- **Python backend untouched** — all hardening (token_version, audit, BrowserOriginMiddleware, security_actions) preserved.
- **JWT in `castor_token` httpOnly cookie** — `SameSite=Lax` (not Strict, for WebAuthn begin/complete redirects), `secure=isProd()`.
- **Server-side `backendFetch` for Astro pages**; Vite proxy only for client-side fetches.
- **TypeScript pinned to `^5.9.3`** — `@astrojs/check@0.9.x` requires TS 5.x/6.x programmatic API. TS 7 silently hides type errors (memory note: this was a 2025 incident).
- **`astro/tsconfigs/base` not `strict`** — `strict` enforces React-style prop names on React components in `.astro` files; `base` + `strict: true` inside `compilerOptions` avoids that footgun.
- **Domain layer in TypeScript** (`src/lib/`) — re-implement habit/streak/auth rules once.
- **No `:latest` from Docker Hub** — compose references local `castor:custom-…` tag. Apollo skill records this; do not undo.

### 2.3 Frontend test baseline

```bash
$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (92 files):
- 0 errors
- 0 warnings
- 58 hints
```

No browser E2E tests, no Playwright/Cypress in `web/concepts/` — only `astro check` for type safety.

### 2.4 What the migration branch still lacks (P3 work)

From the migration-plan.md "Phase 3 — Decoupling" and "Open Questions" sections:

1. **`beaverhabits/` → `castor/` package rename** — package still imports `beaverhabits` even though directory is renamed; `castor/app/auth.py` line 1: `from nicegui import app` (kept on purpose for legacy storage interface).
2. **Long-press notes-textarea on habit rows** — single remaining P2 polish item (~1 slice-hour).
3. **Backend `POST /api/v1/habits/import` endpoint** — `/import.astro` surfaces honest "endpoint not available" until this lands.
4. **Backend `POST /api/v1/account` cleanup** — D16 documented but not landed.
5. **Habit-display preference writes** (`habit_show_streak`, `habit_show_total`, `habit_first_day_of_week`, etc.) — currently read from cookies; backend has no user-configs write endpoint.
6. **PWA manifest in `web/concepts/public/manifest.webmanifest`** — referenced in `Layout.astro` but needs to exist.
7. **Decoupling ADRs** for removed commercial features (Paddle, `admin.py` in legacy, `chip_sets_page.py`).
8. **CI gate** (`pnpm audit` + `astro check` + `pytest`) referenced in plan §1.4 but not committed at `.github/workflows/security.yml`.

## 3. Live ↔ migration diff (file-level)

Counted via `git diff --stat main..migration-to-shadcn-astro`:
- 257 files changed, +23,413 / −5,279 lines.

Highlights (Python side):
| Old (`beaverhabits/...`) | New (`castor/...`) | Notes |
|---|---|---|
| `frontend/` (23 modules) | **removed** | Replaced by Astro pages |
| `routes/routes.py` | **removed** | @ui.page registrations gone |
| `routes/google_one_tap.py` | **removed** | Feature dropped |
| `metrics_app.py` | **kept** | |
| `plan/paddle.py` | **removed** | Commercial feature dropped |
| `app/audit.py` | **kept** | Already shipped on main |
| `app/webauthn_routes.py` | **kept** + new endpoints | recovery-email, offer dismiss |
| (new) | `app/admin_routes.py` | NEW: /api/v1/admin/* |
| (new) | `app/security_actions.py` | NEW: shared sensitive-actions service |
| (new) | `app/circle_routes.py`, `app/circles.py` | NEW: Private Circles |
| (new) | `demo_seed.py` | NEW: idempotent demo account |

Test file deletions (legacy tests no longer relevant):
```
tests/test_batch1_webauthn.py    tests/test_batch2_ux.py
tests/test_batch3_durability.py  tests/test_lane2_context_menu.py
tests/test_lane2_security_page.py
tests/test_legacy_recovery_safety.py  tests/test_recovery_logging.py
tests/test_security_dialogs.py        tests/test_security_passkeys.py
tests/test_security_recovery_card.py
tests/test_storage.py  tests/test_utils.py  tests/test_gui.py
tests/security_dom_harness.py  tests/security_virtual_acceptance.py
```

Test file additions: **none on the migration branch itself** — the same test set runs against the renamed package.

Highlights (Frontend side): 35 new Astro pages, 17 BFF endpoints, 18 shadcn primitives.

## 4. Open source-of-truth questions (must reconcile before cutover)

| Question | Where to look | Risk if wrong |
|---|---|---|
| What does the live container actually run? | `docker exec beaverhabits stat /app/beaverhabits/...` (per apollo-beaverhabits skill) | Writing against `/opt/castor` source vs the running image can silently diverge |
| Are the legacy `beaverhabits-backup-2026-09-12-COPY2/bh-app-20260912/` files current? | No — explicitly marked as 2026-09-12 snapshot | Mismatched import paths, missing tokens |
| Is the migration branch's `castor/` API contract stable? | `web/concepts/src/pages/api/**/*.ts` are the consumer; `castor/routes/api.py` is the producer | Drift = pages stop working |
| Did the migration branch ever get smoke-tested on a real device? | `AUDIT-2026-09-23.md` + `web/concepts/docs/security/dogfood-findings.md` | The UI may render but break on the iPhone real-credential flow |
| Does the WebAuthn rpId mismatch on apollo still exist? | `WEBAUTHN_RP_ID` in running container env vs `WEBAUTHN_ORIGIN` | New passkey registration will fail; existing may break per browser |
| What does `audit_event` look like in apollo's running SQLite? | **Doesn't exist yet on apollo** — `castor/app/audit.py` ships a new table; `create_db_and_tables` adds it via `ALTER TABLE`/`create_all` on first start | The audit infrastructure will silently activate after cutover; existing flows must still write audit rows |
| Is there a DB migration plan for jrodux's 7-habit JSON? | None documented | The DictHabitList shape (`{habits: [{id, name, records, ...}]}`) is the same on both branches, so a literal `UPDATE habit_list SET data=?` against jrodux's row is feasible |
| What about `circ_id.habits` (new feature) when the legacy user has none? | New tables only; no schema change to `habit_list` | None — circles is additive |
| Will Astro's `output:'server'` + `@astrojs/node` standalone run inside the existing `castor:custom-…` image? | Dockerfile currently only builds the Python side (`castor/`); the Astro build artefact isn't baked into the image | Need a multi-stage build that runs `pnpm install && pnpm build` and copies `dist/` into the image, OR a separate Astro image running alongside castor-beaverhabits |

## 5. Verification commands (re-run to confirm this doc is current)

```bash
# Live container state
ssh apollo 'sudo /usr/bin/docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" | grep -E "beaver|wg|pihole"'
ssh apollo 'sudo /usr/bin/docker inspect beaverhabits --format "{{.Config.Image}} {{.Created}}"'
ssh apollo 'sudo /usr/bin/docker exec -u root beaverhabits /opt/pysetup/.venv/bin/python -c "from importlib.metadata import version; print(version(\"nicegui\"))"'

# Live DB snapshot (read-only)
ssh apollo 'sudo python3 -c "import sqlite3; c=sqlite3.connect(\"file:/var/lib/docker/volumes/beaver_data/_data/habits.db?mode=ro\", uri=True); print(c.execute(\"SELECT email,length(id) FROM user\").fetchall())"'

# Live backend reachable
timeout 5 python3 -c "import urllib.request; print(urllib.request.urlopen('http://10.8.0.1:8080/health', timeout=3).read())"

# Migration branch parity baseline
cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ -q --ignore=tests/test_batch4_live.py 2>&1 | tail -3
cd /home/joel/castor-repo/web/concepts && pnpm exec astro check 2>&1 | tail -5

# Migration branch drift vs main
cd /home/joel/castor-repo && git log --oneline main..migration-to-shadcn-astro | wc -l
```
