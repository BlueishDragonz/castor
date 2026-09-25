# Castor Astro Migration — Target Architecture & Cutover Plan (Phase 2)

> Decisions locked per user 2026-09-24:
>
> 1. Branch strategy: **cut `release/astro-migration` from `main` as `Castor Habits`**.
> 2. Cutover: **bump `user.token_version` for every user on cutover** (forced re-login, clean slate).
> 3. WebAuthn: **fix Apollo `WEBAUTHN_RP_ID=10.8.0.1` as part of cutover** (existing jrodux passkey invalidated; user must re-register).
> 4. Deployment: **monorepo Docker image (option A)** — Python FastAPI + Node Astro SSR in one container, sharing `network_mode: container:wg-easy`.
> 5. Cookie `maxAge`: **match backend JWT lifetime (30 days)**.
>
> This document covers: route-to-workflow map, API contracts, deployment
> diagram, test strategy, cutover plan, rollback plan, data-migration
> assessment, slice ordering. References `architecture-current.md`,
> `feature-inventory.md`, `parity-matrix.md` for the source evidence.

---

## 1. Branch strategy

```
main ─────────────────────────────────────────────────┐
                                                      │
   (cut here, 2026-09-24, tag v1.0.0-astro-preview)   │
   ▼                                                  ▼
release/astro-migration ──merge──► main (after cutover)
   │
   │  cherry-picks from migration-to-shadcn-astro:
   │  • Dockerfile change (Node + Astro build)
   │  • docker-compose.yml change (spawn astro + gunicorn)
   │  • .secrets.env on apollo: WEBAUTHN_RP_ID=10.8.0.1
   │  • migration_to_v1.py script (token_version bump + DB sanity)
   │  • release notes + cutover runbook
   │
   │  NOT included:
   │  • any change to castor/ business logic
   │  • any change to web/concepts/ (already merged into release branch)
   │  • deletion of beaverhabits/legacy imports (Phase 4)
   │
   ▼
v1.0.0-astro (tag) ──image──► castor:custom-2026-09-XX
                                  │
                                  └──► /opt/wg-net/docker-compose.yml
                                       image: castor:custom-2026-09-XX
                                       (replaces castor:custom-2026-09-14)
```

Brand: "Castor Habits" is the user-visible name. Source-of-truth code still lives under `castor/` package. No rename of code in v1 cutover; that's a Phase 4 cosmetic commit.

---

## 2. Architecture — what changes, what doesn't

### 2.1 What stays

- **Python backend at `/api/v1/*`, `/auth/*, /users/*, /webauthn/*`** — unchanged from migration branch.
- **SQLite volume** — `beaver_data:/app/.user/` mounted into the new container at the same path.
- **`wg-easy` network namespace** — `network_mode: container:wg-easy` retained.
- **Env file path** — `/var/lib/docker/volumes/beaver_data/_data/.secrets.env` (already loaded by `configs.py`).
- **Mobile clients (iOS, etc.)** — still use `/api/v1/sync/ws` and `/api/v1/habits/*` directly with bearer JWT. No mobile-client changes required for cutover.
- **All 192 pytest tests** — same set runs against the cutover image.

### 2.2 What changes

- **One container instead of one NiceGUI-only container** — the monorepo image runs both processes.
- **Port model** — internal Astro server on `:4321`; FastAPI/gunicorn on `:8080`. Both reachable inside the `wg-easy` netns; the user/browser hits `:8080` for `/health` and `:4321` for pages.
  - **Question flagged**: do we want users to hit `:8080` (existing URL pattern) or `:4321` (new Astro URL)?
  - **My recommendation**: keep `:8080` for `/health` and `/auth/*`, `/api/*` (the "API surface"); reverse-proxy all UI traffic to `:4321`. This means nginx/Caddy in front, OR a Python `ProxyMiddleware` mounted in `castor/main.py`.
  - **Simpler alternative**: bind Astro on `:8080` directly and proxy `/auth/*`, `/api/*` to the Python backend on a unix socket or `127.0.0.1:8090`. **Decided option below.**
- **`castor_token` cookie `maxAge`** — bumped from 7 days to 30 days (matches `JWT_LIFETIME_SECONDS=2592000`).
- **`WEBAUTHN_RP_ID` in `.secrets.env`** — flipped from `localhost` to `10.8.0.1`.
- **`/help` page** — created (the visible 404 in mobile MoreSheet).
- **DB `user.token_version`** — bumped for every user on cutover.

### 2.3 Deployment diagram (decided: monorepo, option A)

```
┌─────────────────────────────────────────────────────────────────┐
│  Apollo /opt/wg-net/docker-compose.yml                          │
│                                                                 │
│  services:                                                      │
│    wg-easy:                                                      │
│      image: ghcr.io/wg-easy/wg-easy:15                          │
│      network: wg_net  (10.42.42.42)                             │
│      ports: [51820/udp, 51821/tcp]                              │
│                                                                 │
│    pihole:                                                       │
│      image: pihole/pihole:latest                                 │
│      network_mode: container:wg-easy                            │
│      env_file: /opt/pihole/pihole.env                           │
│                                                                 │
│    castor:                                                       │
│      image: castor:custom-2026-09-XX  (NEW tag, monorepo build) │
│      container_name: castor                                     │
│      depends_on: [wg-easy]                                      │
│      network_mode: container:wg-easy                            │
│      environment:                                                │
│        - HABITS_STORAGE=DATABASE                                │
│        - WEBAUTHN_RP_ID=10.8.0.1        (NEW — was localhost)   │
│        - WEBAUTHN_ORIGIN=http://10.8.0.1:8080                  │
│        - ASTRO_PORT=4321                                        │
│        - ASTRO_HOST=0.0.0.0                                     │
│        - JWT_LIFETIME_SECONDS=2592000                           │
│      volumes:                                                    │
│        - castor_data:/app/.user/    (renamed from beaver_data   │
│                                       same mountpoint)          │
│      restart: unless-stopped                                    │
│      healthcheck:                                               │
│        test: ["CMD-SHELL", "python healthcheck.py"]            │
│        interval: 30s   timeout: 10s   retries: 3                │
│                                                                 │
│  volumes:                                                        │
│    castor_data: { external: true }    (was beaver_data)         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Inside the castor container (one PID namespace, two long-lived procs):

   ┌─ supervisor (start.sh) ─────────────────────────────────────┐
   │                                                             │
   │   gunicorn castor.main:app                                  │
   │     bind:  0.0.0.0:8090   (internal, not exposed)           │
   │     workers: 1   (required for in-process WebSocket fan-out)│
   │   ─────────────────────────────────────────────────────     │
   │                                                             │
   │   node /app/web/concepts/dist/server/entry.mjs               │
   │     listen: 0.0.0.0:4321                                    │
   │     (Astro SSR standalone; reads BACKEND_URL env var)        │
   │   ─────────────────────────────────────────────────────     │
   │                                                             │
   │   python healthcheck.py   (called by Docker HEALTHCHECK)    │
   │     GET http://127.0.0.1:4321/health  (NEW — was :8080)     │
   │                                                             │
   └─────────────────────────────────────────────────────────────┘

External traffic (browser → wg-easy netns → castor):

   10.8.0.1:8080/health      →  Astro SSR port 4321 (proxied by  │
                                Astro's own internal rewrite to    │
                                the FastAPI backend /health)       │
                                OR direct bind on 8080 + Astro     │
                                reverse-proxies API to 8090        │

   10.8.0.1:8080/auth/*      →  gunicorn :8090/auth/*             │
   10.8.0.1:8080/api/v1/*    →  gunicorn :8090/api/v1/*           │
   10.8.0.1:8080/users/*     →  gunicorn :8090/users/*            │
   10.8.0.1:8080/webauthn/*  →  gunicorn :8090/webauthn/*         │
   10.8.0.1:8080/            →  Astro SSR :4321                   │
   10.8.0.1:8080/login        →  Astro SSR                         │
   10.8.0.1:8080/habits       →  Astro SSR                         │
   ...all other Astro pages...                                    │
```

**Single-port model (recommended)**: Astro binds `:8080` and reverse-proxies the four backend prefixes to `gunicorn:8090` via Astro's `vite.proxy` (already configured for `/auth`, `/users`, `/webauthn`, `/health` — extend with `/api/v1`). This requires zero changes to the browser URL and preserves VPN URL stability.

**Implementation note**: Astro's `@astrojs/node` standalone adapter only exposes a single `http.createServer`. We add a thin Node `http-proxy` middleware that matches `/auth`, `/api/v1`, `/users`, `/webauthn` and forwards to `http://127.0.0.1:8090`. This is 20 lines in `web/concepts/src/server.ts` (or a small custom adapter).

### 2.4 Process supervision

`start.sh` already manages gunicorn. We extend it to:

```bash
#!/bin/sh
set -e

# 1. Start FastAPI backend (gunicorn)
exec gunicorn castor.main:app \
  --bind 127.0.0.1:8090 \
  --workers 1 \
  --worker-class uvicorn.workers.UvicornWorker \
  --access-logfile - \
  --error-logfile - &

# 2. Wait for backend health
for i in $(seq 1 30); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=1).read()" 2>/dev/null; then
    break
  fi
  sleep 1
done

# 3. Start Astro SSR
exec node /app/web/concepts/dist/server/entry.mjs &

# 4. Wait on both
wait -n
```

The existing `healthcheck.py` changes to probe `http://127.0.0.1:4321/health` (or directly through gunicorn — either works since Astro's reverse proxy will fall through to backend).

---

## 3. Route-to-workflow map

### 3.1 User-facing Astro pages

| Astro page | Auth | Method | Backend route(s) called | Notes |
|---|---|---|---|---|
| `/` | n/a | GET | (none) | Redirects to `/habits` or `/login` |
| `/login` | none | GET, POST | `/auth/login`, `/auth/webauthn/login/{begin,complete}`, `/auth/webauthn/check` | 4-stage FSM; sets `castor_token` cookie on success |
| `/register` | none | GET, POST | `/auth/register` | Auto-logs in (same response as `/auth/login`) |
| `/forgot-password` | none | GET, POST | `/auth/forgot-password` | Email → 12-digit code |
| `/verify-reset` | code | GET, POST | `/auth/reset-password/check`, `/auth/reset-password` | 2-stage: code → new password |
| `/reset-password` | token | GET, POST | (legacy reset-link flow, still works) | Less preferred; verify-reset is the standard path |
| `/logout` | session | POST | `/auth/logout` (bumps token_version), clears cookies | |
| `/account/delete` | session + password | POST | `/api/v1/account` DELETE | Password + typed "DELETE" confirm |
| `/habits` | session | GET | `/api/v1/habits`, `/api/v1/habits/{id}` (per habit, for records) | 7-day grid by default; cookie-driven display prefs |
| `/habits/new` | session | GET, POST | `/api/v1/habits` POST | |
| `/habits/[id]` | session | GET | `/api/v1/habits/{id}`, `/api/v1/habits/{id}/notes` | Streak, heatmap, history, notes |
| `/habits/[id]/edit` | session | GET, POST | `/api/v1/habits/{id}` PATCH | |
| `/habits/[id]/complete` | session | POST | `/api/v1/habits/{id}/completions` | Triggered by `<form>` from HabitCheckBox |
| `/habits/[id]/archive` | session | POST | `/api/v1/habits/{id}` PUT `{status: archived}` | |
| `/habits/[id]/duplicate` | session | POST | `/api/v1/habits` POST (clone) | |
| `/habits/order` | session | GET, POST | `/api/v1/habits` GET, `/api/v1/habits/meta` PUT | |
| `/stats` | session | GET | `/api/v1/habits`, `/api/v1/habits/{id}/completions` | Per-habit streak cards |
| `/settings` | session | GET, POST | (cookie-only theme; future: `/api/v1/settings/custom-css`) | |
| `/security` | session | GET | (mounted `<SecurityContent client:load />`) | Client island for WebAuthn |
| `/import` | session | GET, POST | `/api/v1/habits/import` POST | **Backend endpoint NOT yet shipped** — page surfaces honest 404 |
| `/export` | session | GET, POST | `/api/v1/habits/export` GET | Streams with `Content-Disposition` |
| `/admin` | ADMIN_EMAIL | GET | `/api/v1/admin/users` | 401/403 → "Access denied" |
| `/circles/*` | session | GET, POST | `/api/v1/circles/*` | Private Circles (castor net-new) |
| `/help` | none | GET | (none) | **NEW — was the 404** |
| `/terms`, `/privacy` | none | GET | (none) | Static |

### 3.2 BFF endpoints (Astro pages/api/)

All under `web/concepts/src/pages/api/`. Read `castor_token` from cookie → attach `Authorization: Bearer <token>` → forward to backend.

| BFF route | Backend target | Methods | Auth |
|---|---|---|---|
| `/api/v1/habits` | `/api/v1/habits` | GET, POST | Bearer |
| `/api/v1/habits/[id]` | `/api/v1/habits/{id}` | GET, PATCH | Bearer |
| `/api/v1/habits/[id]/notes` | `/api/v1/habits/{id}/notes` | GET, POST | Bearer |
| `/api/v1/habits/[id]/duplicate` | `/api/v1/habits` (POST) | POST | Bearer |
| `/api/v1/habits/export` | `/api/v1/habits/export` | GET | Bearer |
| `/api/v1/habits/import` | `/api/v1/habits/import` | POST | Bearer (backend TODO) |
| `/api/v1/habits/reorder` | `/api/v1/habits/meta` | PUT | Bearer |
| `/api/account/delete` | `/api/v1/account` | DELETE | Bearer + password body |
| `/api/admin/backup` | `/api/v1/admin/backup` | POST | Bearer + admin |
| `/api/auth/reset-password/check` | `/auth/reset-password/check` | POST | Bearer |
| `/api/auth/webauthn/check` | `/auth/webauthn/check` | POST | Bearer |
| `/api/auth/webauthn/offer/dismiss` | `/auth/webauthn/offer/dismiss` | POST | Bearer |
| `/api/auth/webauthn/recovery-email` | `/auth/webauthn/recovery-email` | POST | Bearer |
| `/api/auth/webauthn/recovery-email/verify` | `/auth/webauthn/recovery-email/verify` | POST | Bearer |
| `/api/auth/webauthn/login/complete` | `/auth/webauthn/login/complete` | POST | form-encoded |
| `/api/auth/webauthn/register/begin` | `/auth/webauthn/register/begin` | POST | form-encoded |
| `/api/auth/webauthn/register/complete` | `/auth/webauthn/register/complete` | POST | form-encoded |

---

## 4. API contracts (formalised)

### 4.1 Auth — login (email + password)

```yaml
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username: <email>            # OAuth2PasswordRequestForm convention
password: <password>

200 OK
Set-Cookie: beaver_auth=<jwt>; HttpOnly; SameSite=lax; Path=/; Max-Age=2592000
{ "access_token": "<jwt>", "token_type": "bearer" }

400 Bad Request — { "detail": "LOGIN_BAD_CREDENTIALS" }
422 Unprocessable — missing field
429 Too Many Requests — per-IP rate limit
```

**Astro equivalent**: `<form method="post" action="/auth/login">` posted via the BFF pattern OR direct backend POST through the Astro reverse proxy. The castor branch's `login.astro` already does this server-side.

### 4.2 Auth — passkey login

```yaml
POST /auth/webauthn/login/begin
Content-Type: application/x-www-form-urlencoded
username: <email>

200 OK
Set-Cookie: beaver_webauthn=<random>; HttpOnly; SameSite=strict; Path=/; Max-Age=86400
{
  "publicKey": {
    "challenge": "<base64url>",
    "rpId": "10.8.0.1",          # matches WEBAUTHN_RP_ID after cutover
    "allowCredentials": [...]
  }
}

404 Not Found — unknown email
```

```yaml
POST /auth/webauthn/login/complete
Content-Type: application/json

{ "credential": { "id": ..., "rawId": ..., "response": ..., "type": "public-key" } }

200 OK
Set-Cookie: beaver_auth=<jwt>; HttpOnly; SameSite=lax; Path=/; Max-Age=2592000
{ "access_token": "<jwt>", "token_type": "bearer" }

400 Bad Request — verification failed
401 Unauthorized — challenge expired / invalid signature
```

### 4.3 Habit CRUD

```yaml
GET /api/v1/habits?status=active
Authorization: Bearer <jwt>

200 OK
[ { "id": "c4758f", "name": "Morning run" }, ... ]
```

```yaml
POST /api/v1/habits
Authorization: Bearer <jwt>
Content-Type: application/json

{ "name": "Read 30 min" }

201 Created
{ "id": "a3b2c1", "name": "Read 30 min" }

400 Bad Request — MAX_HABIT_COUNT exceeded
422 Unprocessable — name empty
```

```yaml
PATCH /api/v1/habits/{habit_id}
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "name": "Read 30 min",        # optional
  "star": true,                  # optional
  "status": "active",            # optional: active | archived
  "period": {                    # optional
    "period_type": "D",          # D | W | M | Y
    "period_count": 1,
    "target_count": 1
  },
  "tags": ["morning"]            # optional
}

200 OK — { "id": ..., "name": ..., "star": ..., "status": ..., "period": ..., "tags": [...] }
404 Not Found — habit doesn't exist
```

### 4.4 Habit completion tick

```yaml
POST /api/v1/habits/{habit_id}/completions
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "done": true,
  "date": "2026-09-24",
  "text": "Pain au Lait",       # optional, daily note
  "date_fmt": "%Y-%m-%d"        # default "%d-%m-%Y"
}

200 OK — { "day": "2026-09-24", "done": true }
400 Bad Request — bad date format
404 Not Found — habit doesn't exist
```

### 4.5 Habit export (full snapshot)

```yaml
GET /api/v1/habits/export
Authorization: Bearer <jwt>

200 OK
{
  "habits": [
    {
      "id": "c4758f",
      "name": "Morning run",
      "star": false,
      "status": "active",
      "period": { "period_type": "D", "period_count": 1, "target_count": 1 },
      "tags": [],
      "records": [
        { "day": "2026-09-23", "done": true, "text": "", "timestamp": 1695456000000 }
      ]
    }
  ],
  "order": ["c4758f", ...]
}
```

### 4.6 Habit import (replace)

**Backend endpoint NOT YET SHIPPED**. Slice P3 vertical-slice spec:

```yaml
POST /api/v1/habits/import
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "habits": [
    { "id": "...", "name": "...", "records": [...] }
  ]
}

200 OK — { "imported": 5, "replaced": true }
400 Bad Request — schema invalid
422 Unprocessable — habit list shape invalid
```

Implementation: in `castor/routes/api.py` add `import_habit_list` that:
1. Validates each habit has `id`, `name`, `records`.
2. Creates a `DictHabitList` from the payload.
3. Replaces `user_storage.get_user_habit_list(user)` via `user_storage.save_user_habit_list(user, habit_list)` (atomic write).
4. Logs `audit_event("habit_import", "success", user_id)`.
5. Enforces `MAX_HABIT_COUNT` post-merge.

### 4.7 Account deletion

```yaml
DELETE /api/v1/account
Authorization: Bearer <jwt>
Content-Type: application/json

{ "password": "<current>" }

204 No Content
400 Bad Request — wrong password / stale token_version
401 Unauthorized — bearer invalid
```

### 4.8 Admin (superuser via ADMIN_EMAIL string gate)

```yaml
GET /api/v1/admin/users
Authorization: Bearer <jwt>          # bearer must be ADMIN_EMAIL user

200 OK
{
  "users": [
    { "email": "jrodux@gmail.com", "id": "...", "is_active": true, "is_verified": true },
    ...
  ]
}

401 Unauthorized — not ADMIN_EMAIL
```

```yaml
POST /api/v1/admin/backup
Authorization: Bearer <jwt>

202 Accepted
{ "status": "completed", "triggered_by": "jrodux@gmail.com" }
```

### 4.9 WebAuthn credentials list / add / remove

```yaml
GET /auth/webauthn/credentials
Authorization: Bearer <jwt>

200 OK
[ { "id": "...", "name": "iPhone", "created_at": "...", "last_used": null }, ... ]
```

```yaml
DELETE /auth/webauthn/credentials/{credential_id}
Authorization: Bearer <jwt>

204 No Content
404 Not Found
409 Conflict — last credential, requires password + session preserved
```

### 4.10 Change password

```yaml
POST /auth/webauthn/change-password
Authorization: Bearer <jwt>
Content-Type: application/json

{ "current_password": "...", "new_password": "..." }

204 No Content
400 Bad Request — wrong current / new <12 / mismatch
401 Unauthorized
409 Conflict — stale token_version
```

Bumps `user.token_version` in the same SQL UPDATE as the password hash (CAS pattern).

### 4.11 WebSocket realtime sync (mobile clients)

```yaml
WS /api/v1/sync/ws?token=<jwt_or_api_token>

# Incoming from client:
{ "type": "push_tick", "request_id": "...", "habit_id": "...", "day": "YYYY-MM-DD", "done": true, "text": "" }
{ "type": "push_habit_list", "request_id": "...", "habits": [...], "order": [...] }

# Outgoing from server:
{ "type": "tick_ack", "request_id": "...", "timestamp": 1695456000000 }
{ "type": "habit_list_ack", "request_id": "...", "timestamp": ... }
```

### 4.12 Auth — forgot password / reset

```yaml
POST /auth/forgot-password
Content-Type: application/json

{ "email": "user@example.com" }

202 Accepted
{ "detail": "If the account can be recovered, reset instructions will be emailed." }

429 Too Many Requests — recovery rate-limit (1 per 15 min per email)
```

```yaml
POST /auth/reset-password/check
Content-Type: application/json

{ "email": "...", "code": "123456789012" }

200 OK — code valid (does NOT consume)
400 Bad Request — invalid format
410 Gone — code expired
```

```yaml
POST /auth/reset-password
Content-Type: application/json

{ "email": "...", "code": "...", "new_password": "..." }

200 OK — { "access_token": "<new-jwt>", "token_type": "bearer" }
400 Bad Request — invalid code / password <12 / mismatch
```

### 4.13 Cookie contracts

```yaml
castor_token:
  HttpOnly: true
  SameSite: lax
  Secure: true                # when HTTPS terminates; env-driven via isProd()
  Path: /
  Max-Age: 2592000            # 30 days, matches JWT_LIFETIME_SECONDS

castor_user:
  HttpOnly: false              # visible to JS for UI greeting
  SameSite: lax
  Secure: true                # when HTTPS terminates
  Path: /
  Max-Age: 2592000

castor_webauthn_browser:
  HttpOnly: true
  SameSite: strict             # WebAuthn ceremonies don't navigate
  Secure: true                # when HTTPS terminates
  Path: /
  Max-Age: 2592000             # 30 days; mirrors backend's beaver_webauthn
```

`castor_theme`, `habit_show_streak`, `habit_show_total`, `habit_first_day_of_week`, etc.: client-readable cookies; not sensitive. Max-Age 1 year; same lax.

### 4.14 Auth/authorization model — every server-side data op

Per the brief: "Specify authentication/session handling and authorisation on every server-side data operation, not merely hidden buttons."

| Op | Auth check | Authz check | Failure mode |
|---|---|---|---|
| `GET /api/v1/habits` | Bearer valid + token_version matches | Implicit: bearer is the user_id | 401 |
| `POST /api/v1/habits` | Bearer valid | `MAX_HABIT_COUNT` enforced server-side | 401 / 400 |
| `PATCH /api/v1/habits/{id}` | Bearer valid | habit belongs to bearer.user_id | 401 / 404 (404 is correct; no enumeration) |
| `DELETE /api/v1/habits/{id}` | Bearer valid | habit belongs to bearer.user_id | 401 / 404 |
| `POST /api/v1/habits/{id}/completions` | Bearer valid | habit belongs to bearer.user_id | 401 / 404 |
| `GET /api/v1/habits/export` | Bearer valid | Implicit | 401 |
| `POST /api/v1/habits/import` | Bearer valid + `MAX_HABIT_COUNT` post-merge | Implicit | 401 / 400 |
| `PUT /api/v1/habits/meta` | Bearer valid | order IDs must belong to user | 401 / 400 |
| `DELETE /api/v1/account` | Bearer valid + token_version matches + password re-auth | Implicit | 401 / 400 |
| `GET /api/v1/admin/users` | Bearer valid | bearer.email == settings.ADMIN_EMAIL | 401 / 403 |
| `POST /api/v1/admin/backup` | Bearer valid | bearer.email == settings.ADMIN_EMAIL | 401 / 403 |
| `GET /auth/webauthn/credentials` | Bearer valid | Implicit | 401 |
| `DELETE /auth/webauthn/credentials/{id}` | Bearer valid + token_version matches + password re-auth | credential belongs to bearer.user_id | 401 / 404 / 409 |
| `POST /auth/webauthn/change-password` | Bearer valid + token_version matches + current password | Implicit | 401 / 400 / 409 |
| `POST /auth/webauthn/register/begin` | Bearer valid (registration is for self only) | bearer.email == username in body | 401 / 403 |
| `WS /api/v1/sync/ws` | token via `?token=` (JWT or API token) | token.user_id == broadcaster | 1008 |
| `POST /auth/forgot-password` | Anonymous + IP rate-limit | Implicit | 429 |
| `POST /auth/reset-password/check` | Anonymous + IP rate-limit | Code hash matches | 400 / 410 |
| `POST /auth/reset-password` | Anonymous + IP rate-limit + code validity | Code not consumed | 400 |

Every backend endpoint already enforces these checks in the migration branch. The Astro layer does **not** need to enforce them — it forwards via `backendFetch` with the bearer; the backend is the source of truth.

**The frontend additionally must not lie about authorization**:
- Hidden buttons are not the security gate. If a button is rendered, the backend still validates the request.
- An Astro page that renders "Admin" in the menu when `session.email == settings.ADMIN_EMAIL` is UX, not security. Backend's `current_admin_user` is the gate.

---

## 5. Data-migration assessment

### 5.1 What's already verified

- **Schema shape compatible**: `DictHabitList` JSON is identical between legacy and migration branches (same Python class).
- **User table**: `token_version`, `passkey_offer_dismissed`, `recovery_email*` columns added by `create_db_and_tables()` on first start of the new image. Additive; existing rows intact.
- **New tables** (`audit_event`, `recovery_email_challenge`, `circle*`, `webauthn_challenge` if added later) created on first start.
- **Existing data preserved**: 4 users, 2 habit_list rows, 1 webauthn_credential on apollo — none require schema changes.

### 5.2 Cutover-time DB script: `migration_to_v1.py`

A one-shot script run ONCE on apollo inside the new container before traffic switches. Sequence:

```python
# /app/migrations/migration_to_v1.py (run inside container)
import asyncio, datetime, sqlite3, sys
from castor.app.db import async_session_maker, User
from sqlalchemy import update

async def main():
    # 1. Open audit trail at cutover time
    cutover_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(f"[cutover] {cutover_at} — bumping token_version for all users")

    # 2. Bump token_version — invalidates all outstanding JWTs (forced re-login)
    async with async_session_maker() as session:
        await session.execute(update(User).values(token_version=User.token_version + 1))
        await session.commit()

    # 3. Snapshot DB before swap (idempotent; runs even if already done)
    src = "/app/.user/habits.db"
    dst = f"/app/.user/habits.db.pre-cutover-{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    if not Path(dst).exists():
        # SQLite backup API (WAL-safe, unlike cp)
        conn = sqlite3.connect(src)
        bck = sqlite3.connect(dst)
        conn.backup(bck)
        bck.close()
        conn.close()
        print(f"[cutover] snapshot at {dst}")

    # 4. Verify JSON shape for every user (rejects silently corrupt data)
    async with async_session_maker() as session:
        from sqlalchemy import select
        from castor.app.db import HabitList
        for hl in (await session.execute(select(HabitList))).scalars():
            try:
                # Castor's DictHabitList validates on construction
                from castor.storage.dict import DictHabitList
                DictHabitList(hl.data)
            except Exception as e:
                print(f"[cutover] WARN: user_id={hl.user_id} habit_list corrupt: {e}", file=sys.stderr)

    print("[cutover] complete")

if __name__ == "__main__":
    asyncio.run(main())
```

**Where it runs**: `docker exec castor /opt/pysetup/.venv/bin/python /app/migrations/migration_to_v1.py` BEFORE the new container accepts traffic.

**Why a script and not just `create_db_and_tables()`**:
- `create_db_and_tables` is idempotent and additive, but it does NOT bump token_version.
- The script also takes a pre-cutover snapshot using the SQLite backup API (WAL-safe, per the apollo-beaverhabits skill).

**Idempotency**: token_version bump is safe to run twice (becomes +2 instead of +1). Snapshot check `if not Path(dst).exists()` prevents overwrite.

### 5.3 Volumes: `beaver_data` → `castor_data`

The Docker volume name in `/opt/wg-net/docker-compose.yml` was `beaver_data`. We rename it to `castor_data` for brand consistency. **The volume itself stays the same** — Docker named volumes are referenced by name, not by hash. The path inside the container stays `/app/.user/`. No data copy required.

```yaml
volumes:
  castor_data:
    external: true
```

We update `/opt/wg-net/docker-compose.yml` to reference `castor_data` and explicitly run `docker volume inspect castor_data` to confirm it's the same as the old `beaver_data`. If the inspect returns empty (no volume by that name), we either:
- (a) Keep the existing volume name `beaver_data` for v1; cosmetic-only rename deferred to Phase 4.
- (b) Rename the volume: `docker volume create --name castor_data` is wrong (creates empty); the correct path is to update compose to keep `beaver_data` (no rename) and accept the cosmetic mismatch until Phase 4.

**Decision**: keep `beaver_data` for v1 (zero risk). Document the deferred rename.

### 5.4 Backup before cutover

```bash
# On apollo, BEFORE swap:
sudo cp -a /var/lib/docker/volumes/beaver_data/_data/habits.db \
          /opt/castor-backups/2026-XX-XX-pre-astro-v1/habits.db.live
sudo cp -a /var/lib/docker/volumes/beaver_data/_data/.secrets.env \
          /opt/castor-backups/2026-XX-XX-pre-astro-v1/secrets.env.live
sudo /usr/bin/docker exec beaverhabits /opt/pysetup/.venv/bin/python -c "
import sqlite3, datetime
src = sqlite3.connect('/app/.user/habits.db')
dst = sqlite3.connect('/app/.user/habits.db.pre-cutover')
src.backup(dst); dst.close(); src.close()
print('WAL-safe backup OK')
"
sudo cp -a /var/lib/docker/volumes/beaver_data/_data/habits.db.pre-cutover \
          /opt/castor-backups/2026-XX-XX-pre-astro-v1/habits.db.pre-cutover
```

This gives us:
- A live snapshot of the DB file (raw).
- A WAL-safe snapshot via SQLite backup API.
- The secrets env file (with all keys/secrets).
- All under `/opt/castor-backups/` (already managed by apollo skill as a rollback target).

---

## 6. Real-time update mechanism

### 6.1 What's used today

- **WebSocket fan-out** (`/api/v1/sync/ws`): broadcasts `HabitListChanged` events to all devices for a given user. Used by mobile clients (iOS app).
- **Daily-change timer** (legacy NiceGUI): `ui.timer(60, refresh_if_needed, immediate=False)` re-renders the home grid when the calendar day rolls over.
- **Engine.IO** (legacy NiceGUI): per-session broadcast between browser tabs.

### 6.2 What replaces each on Astro

| Mechanism | Replaced by |
|---|---|
| WebSocket fan-out (mobile ↔ backend) | **Unchanged** — `/api/v1/sync/ws` still serves mobile clients. Astro pages do not use it. |
| Daily-change timer (browser) | **None currently** — Astro pages re-fetch on full reload. Add a client-side `setInterval` only if dogfood shows it's a regression. Likely unnecessary because users naturally reload after midnight. |
| Engine.IO broadcast (browser tabs) | **StorageEvent on `localStorage`** — multiple tabs already share the storage event API; we can broadcast a custom `castor:invalidate` event on any POST that mutates state. Cheap; no broker. |
| Per-page re-render on action | **Astro's full-page reload** — every `<form>` action submits server-side and the response is the new page. No client-side fetch needed for state-changing flows. |

**Trade-off documented**: the Astro approach is a per-request full reload, vs NiceGUI's `ui.refreshable` partial re-render. Pages may feel slightly slower. If dogfood shows the regression is real, we add React island components for in-place updates.

### 6.3 Why we don't need SSE / WebSocket for browser

The Astro server-rendering pattern is: page renders with current state; on action, browser POSTs, server re-renders with new state. No client-server sync needed. Mobile clients keep WebSocket because they have multi-device live sync requirements (per `castor/realtime.py`).

---

## 7. Test strategy

### 7.1 What already passes (192 collected, 180 pass)

- `tests/test_priority1_auth.py` — 17 tests, auth flow
- `tests/test_apis.py` — habit CRUD + completions
- `tests/test_lane2_*.py` — lane2 hardening (HTTP, CSS, ops, webauthn, integration)
- `tests/test_sensitive_actions.py` — central security_actions service
- `tests/test_recovery_code_safety.py` — recovery flow
- `tests/test_reset_link_navigation.py` — reset token safety
- `tests/test_logout_bump.py` — token_version bump on logout
- `tests/test_realtime.py` — WebSocket sync
- `tests/test_health.py` — `/health` endpoint
- `tests/test_api_tokens.py` — API token rotation
- `tests/test_account_deletion.py` — delete flow
- `tests/test_batch4_live.py` — **apollo-only**, env-gated (12 expected failures from laptop)

### 7.2 What is NOT covered (gaps the matrix flagged)

Per `parity-matrix.md`, the following flows have zero test coverage and must be added BEFORE cutover:

| Flow | Test file target | Approach |
|---|---|---|
| Drag-drop reorder (`PUT /api/v1/habits/meta {order}`) | `tests/test_apis.py::test_meta_order_round_trip` | POST then PUT then GET, verify order persisted |
| Calendar heatmap rendering (15-week grid) | Browser E2E | Render `/habits/[id]`, snapshot DOM, verify 15 `<rect>` cells per row |
| Per-habit completion status chips (`/api/v1/user-configs` GET/PUT) | `tests/test_user_configs.py` (new) | Backend round-trip |
| Telegram backup happy + failure paths | `tests/test_backup.py` (new) | Mock `httpx` calls to `api.telegram.org`; assert POST shape |
| Notes-image upload (`POST /assets`) | `tests/test_assets.py` (new) | Multipart upload, round-trip GET `/assets/{id}` |
| Custom CSS persistence backend (when shipped) | `tests/test_custom_css.py` (new) | Round-trip POST/GET |
| Last-key passkey removal flow | `tests/test_lane2_webauthn.py::test_last_key_preserves_session` | Already exists per audit; verify |
| Long-press notes-textarea on grid cells | Browser E2E | Long-press on HabitCheckBox, dialog appears, save persists |
| `/help` page | Browser E2E | Visit `/help`, assert 4 links visible |
| Cutover boot: gunicorn + astro both healthy | `tests/test_smoke_docker.py` (new) | Run inside Docker image, hit both `/health` endpoints |

### 7.3 Browser E2E strategy

The migration branch has **zero browser E2E tests**. This is a gap. Recommendation:

- **Add Playwright** (TypeScript, same package manager as Astro) to `web/concepts/`.
- **`web/concepts/tests/e2e/`** holds the spec files.
- **Targets** (priority order):
  1. Login → land on `/habits` → tick a cell → reload → tick persisted
  2. Add habit → appears on grid → delete → disappears
  3. Open `/security` → add passkey (use Playwright's WebAuthn virtual authenticator) → list shows it
  4. Reorder habits → order persists
  5. Export JSON → file downloads with expected content
  6. `/help` page → 4 links visible
- **CI gate**: `pnpm exec playwright test` runs against a Docker-compose up of the full stack.
- **Cost**: ~1 day of slice work to scaffold + write the first 3 tests.

### 7.4 Test command catalog (re-runnable)

```bash
# Backend (Python)
cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ -q \
  --ignore=tests/test_batch4_live.py 2>&1 | tail -3
# Expected: 180 passed (or close — exact count depends on what new tests land)

# Frontend (Astro typecheck)
cd /home/joel/castor-repo/web/concepts && pnpm exec astro check 2>&1 | tail -5
# Expected: 0 errors, 0 warnings

# Frontend (build)
cd /home/joel/castor-repo/web/concepts && pnpm build 2>&1 | tail -10
# Expected: dist/server/entry.mjs + dist/client/* generated

# Docker build (full monorepo image)
cd /home/joel/castor-repo && sudo /usr/bin/docker build \
  -f docker/Dockerfile \
  -t castor:custom-2026-09-XX \
  . 2>&1 | tail -10
# Expected: image built; size ~250-300 MB

# Docker smoke (post-build)
sudo /usr/bin/docker run --rm --network none \
  -e HABITS_STORAGE=DATABASE \
  -e WEBAUTHN_RP_ID=10.8.0.1 \
  -e JWT_SECRET=test-jwt-secret-with-enough-length-for-tests \
  -e RESET_PASSWORD_TOKEN_SECRET=test-reset-secret-with-enough-length \
  -e NICEGUI_STORAGE_SECRET=test-nicegui-secret-with-enough-length \
  -e TOKEN_ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  -e INVITE_REQUIRED=false \
  castor:custom-2026-09-XX \
  /opt/pysetup/.venv/bin/python -c "
from castor.app.db import create_db_and_tables
import asyncio; asyncio.run(create_db_and_tables())
print('schema ok')
"
# Expected: "schema ok"

# Apollo live (after cutover)
ssh apollo '/usr/bin/docker exec wg-easy wget -q -O - --timeout=10 http://10.8.0.1:8080/health'
# Expected: OK
ssh apollo '/usr/bin/docker exec wg-easy wget -q -O - --timeout=10 http://10.8.0.1:8080/login'
# Expected: HTML page title "Castor"

# WebAuthn rpId (post-fix)
ssh apollo 'sudo /usr/bin/docker exec castor env | grep WEBAUTHN_RP_ID'
# Expected: WEBAUTHN_RP_ID=10.8.0.1

# Token version bump (post-cutover)
ssh apollo 'sudo python3 -c "
import sqlite3
c = sqlite3.connect(\"file:/var/lib/docker/volumes/beaver_data/_data/habits.db?mode=ro\", uri=True)
for row in c.execute(\"SELECT email, token_version FROM user\"):
    print(row)
"'
# Expected: token_version >= 1 for all users (was 0 or absent before cutover)
```

---

## 8. Cutover plan

### 8.1 Pre-cutover (T-7 days)

- [ ] **All Phase 3 vertical slices landed** (see §10).
- [ ] `/help` page created and tested (P0 — visible 404).
- [ ] All parity-matrix rows closed or explicitly approved as changed.
- [ ] `pnpm exec astro check` → 0 errors.
- [ ] `pytest tests/ --ignore=test_batch4_live` → all pass.
- [ ] New Playwright E2E suite added; first 3 tests green.
- [ ] `pnpm build` produces `web/concepts/dist/`.
- [ ] Updated `docker/Dockerfile` builds the monorepo image.
- [ ] Image built locally on apollo: `castor:custom-2026-09-XX`.
- [ ] `.secrets.env` updated: `WEBAUTHN_RP_ID=10.8.0.1` (was `localhost`).
- [ ] Cookie `maxAge` changed in `web/concepts/src/lib/auth.ts` from `60*60*24*7` to `60*60*24*30`.
- [ ] Backup taken (see §5.4).
- [ ] User announcement: "Maintenance window 2026-XX-XX 02:00–04:00 UTC. Re-login required; passkey re-registration required."

### 8.2 Pre-cutover (T-1 hour)

- [ ] `/opt/wg-net/docker-compose.yml` updated: `image: castor:custom-2026-09-XX`.
- [ ] `/opt/wg-net/docker-compose.yml` updated: `WEBAUTHN_RP_ID=10.8.0.1` (compose-env override, not secrets-env).
- [ ] `migration_to_v1.py` placed at `/app/migrations/` in the new image.
- [ ] Communication channel open (status page or pinned message).

### 8.3 Cutover (T-0)

```bash
# 1. Stop the old container
ssh apollo 'cd /opt/wg-net && sudo /usr/bin/docker compose stop castor'

# 2. Run the migration script in a one-off container against the same volume
ssh apollo 'sudo /usr/bin/docker run --rm \
  --network none \
  -v beaver_data:/app/.user \
  castor:custom-2026-09-XX \
  /opt/pysetup/.venv/bin/python /app/migrations/migration_to_v1.py'
# Expected output:
# [cutover] 2026-XX-XXT... — bumping token_version for all users
# [cutover] snapshot at /app/.user/habits.db.pre-cutover-...
# [cutover] complete

# 3. Pull up the new container
ssh apollo 'cd /opt/wg-net && sudo /usr/bin/docker compose up -d castor'

# 4. Wait for healthcheck
for i in $(seq 1 30); do
  if ssh apollo '/usr/bin/docker exec wg-easy wget -q -O - --timeout=2 http://10.8.0.1:8080/health 2>/dev/null | grep -q OK'; then
    echo "castor healthy after $((i*2)) seconds"
    break
  fi
  sleep 2
done

# 5. Verify Astro SSR is reachable
ssh apollo '/usr/bin/docker exec wg-easy wget -q -O - --timeout=5 http://10.8.0.1:8080/login | head -5'
# Expected: HTML with <title>Castor</title>

# 6. Verify backend API still works
ssh apollo '/usr/bin/docker exec wg-easy wget -q -O - --timeout=5 http://10.8.0.1:8080/openapi.json | head -c 200'
# Expected: {"openapi":"3.1.0",...}
```

### 8.4 Post-cutover (T+1 hour)

- [ ] jrodux logs in with password → 2779-byte habit list renders intact.
- [ ] jrodux re-registers a passkey → list shows it.
- [ ] Tick today → reload → tick persisted.
- [ ] `/admin` reachable by ADMIN_EMAIL.
- [ ] `audit_event` table growing.
- [ ] Mobile client connects → push_tick round-trip works.
- [ ] WebSocket auth works with new JWT.
- [ ] Old JWTs rejected (token_version bumped → `ver` mismatch → `None` from `read_token`).

### 8.5 Post-cutover (T+24 hours)

- [ ] No errors in `docker logs castor`.
- [ ] Memory footprint stable (~250 MB on the OCI VM, up from ~140 MB for the legacy image — both processes).
- [ ] `audit_event` row count matches expected login volume.
- [ ] No "rate-limited" 429s spiking from old cookies.

### 8.6 Rollback (anytime within T+24 hours if needed)

```bash
# 1. Stop the new container
ssh apollo 'cd /opt/wg-net && sudo /usr/bin/docker compose stop castor'

# 2. Restore the compose file to the previous image tag
ssh apollo 'cd /opt/wg-net && sudo sed -i "s|castor:custom-2026-09-XX|castor:custom-2026-09-14|" docker-compose.yml'
ssh apollo 'cd /opt/wg-net && sudo sed -i "s|WEBAUTHN_RP_ID=10.8.0.1|WEBAUTHN_RP_ID=localhost|" docker-compose.yml'
# OR: don't restore the env override (it was only an emergency brake)
# The .secrets.env in the volume still says WEBAUTHN_RP_ID=localhost, which is
# the legacy value. Reverting the compose change alone is sufficient.

# 3. Pull up the old container
ssh apollo 'cd /opt/wg-net && sudo /usr/bin/docker compose up -d castor'

# 4. Verify
ssh apollo '/usr/bin/docker exec wg-easy wget -q -O - --timeout=10 http://10.8.0.1:8080/health'
# Expected: OK
```

**DB rollback** (only if migration_to_v1.py corrupted data — extremely unlikely):

```bash
ssh apollo 'sudo /usr/bin/docker compose stop castor'
ssh apollo 'sudo cp -a /opt/castor-backups/2026-XX-XX-pre-astro-v1/habits.db.live \
                    /var/lib/docker/volumes/beaver_data/_data/habits.db'
ssh apollo 'cd /opt/wg-net && sudo /usr/bin/docker compose up -d castor'
# Note: token_version will revert to pre-cutover values; jrodux's pre-cutover JWT
# (if still within 30d) will work again. Pre-cutover passkey will work (rpId=localhost).
```

**Rollback duration**: ~3 minutes (image is small, restart is fast).

**Rollback data loss**: zero if backup taken correctly (snapshot is byte-identical to the live DB at T-1).

### 8.7 Pre-flight gates (must all pass)

```
[ ] pnpm audit clean at high/critical
[ ] astro check 0 errors 0 warnings
[ ] pytest tests/ --ignore=test_batch4_live → all pass
[ ] pnpm exec playwright test → all green
[ ] docker build -f docker/Dockerfile -t castor:custom-… . → image built
[ ] docker run castor:custom-… schema ok
[ ] migration_to_v1.py idempotent (run twice, second is no-op)
[ ] BE secrets updated: WEBAUTHN_RP_ID=10.8.0.1 in .secrets.env
[ ] FE: castor_token cookie maxAge = 60*60*24*30
[ ] Backup taken + verified restorable (test on a clone)
[ ] /help page renders
[ ] No production-critical workflow depends on fixtures or mocks
```

---

## 9. What is "UI replacement" vs "business-logic rewrite" vs "data migration"

The brief asks for these distinctions explicitly:

### 9.1 UI replacement

- All `web/concepts/src/pages/**/*.astro` — replaces legacy `beaverhabits/frontend/*.py` NiceGUI pages.
- All `web/concepts/src/components/**/*.astro` and `*.tsx` — replaces legacy `frontend/components.py` Quasar components.
- **Not touching**: Python business logic in `castor/`, except where refactoring is needed to expose a clean API contract.

### 9.2 Business-logic rewrite (the parts that had to change)

- **WebAuthn browser cookie handling**: `castor_webauthn_browser` cookie is a new abstraction; `mirrorWebAuthnBrowserCookie()` in `lib/auth.ts` is new code. The backend cookie (`beaver_webauthn`) is unchanged.
- **`castor/main.py` lifespan**: `init_demo_seed_task` added (replaces legacy `/demo/*` route group).
- **`/dev/reseed-demo`**: new endpoint, replaces demo workflow.
- **`security_actions.py`**: NEW shared service for password change / passkey delete / account delete. P3 hardening; not strictly required for cutover but already shipped.
- **Circle routes + circles.py schema + 9 audit events**: NEW Private Circle feature; no legacy equivalent.

### 9.3 Data migration

**Minimal**:
- New tables created by `create_db_and_tables()` on first start (additive).
- New columns on `user` (`token_version`, `passkey_offer_dismissed`, `recovery_email`, `recovery_email_verified`) added by ALTER TABLE if missing.
- `user.token_version` bumped for all users via `migration_to_v1.py`.

**No destructive changes**:
- jrodux's `habit_list.data` JSON is unchanged in shape.
- jrodux's `webauthn_credential` row is preserved BUT the existing passkey is invalidated by the rpId change (browser-side; row stays).

**No backup restore needed** unless migration_to_v1.py corrupts data.

---

## 10. Phase 3 vertical-slice ordering

Each slice: read, write, failure, permissions, tests, Docker smoke, commit separately.

### Slice 1: Login (email + password) — **START HERE**

**Why first**: foundational; sets the cookie/auth pattern every other slice depends on. Highest blast radius if wrong.

**Scope**:
- `/login` page (4-stage FSM is fine for v1)
- `/auth/login` backend → set `castor_token` cookie
- Middleware populates `Astro.locals.session` from cookie
- Cookie `maxAge=2592000` (30 days)
- Test: valid creds, invalid creds, missing fields, rate limit, persistence across reload

**Tests added**:
- `tests/test_priority1_auth.py` already covers backend; add Playwright E2E for the Astro form roundtrip
- Browser E2E: navigate to `/login`, fill form, submit, assert URL changes to `/habits`, cookie set

**Done when**:
- `astro check` clean
- Backend tests pass
- Playwright E2E green
- Docker smoke: `/login` returns 200, `/auth/login` with bad creds returns 400, with good creds returns 200

### Slice 2: Home grid (read + tick)

**Why second**: most-used page; exercises the Astro server-rendering pattern, BFF proxy, and tick write.

**Scope**:
- `/habits` page renders 7-day grid from `/api/v1/habits`
- HabitCheckBox `<form>` posts to `/habits/{id}/complete` (server-side)
- `HabitListChanged` event NOT propagated to browser (page reload picks up changes)

**Tests added**:
- Backend: existing `test_apis.py` covers tick
- Playwright E2E: load `/habits`, click a cell, reload, cell stays ticked

**Done when**:
- Tick persists across reload
- Streak badge updates after tick
- Empty state ("No habits") renders

### Slice 3: Add / edit / archive habit

**Why third**: habit CRUD; the third-most common action.

**Scope**:
- `/habits/new` (POST `/api/v1/habits`)
- `/habits/[id]/edit` (PATCH `/api/v1/habits/{id}`)
- `/habits/[id]/archive` (PUT status=archived)
- `MAX_HABIT_COUNT` enforced server-side

**Tests added**:
- Backend: existing
- Playwright E2E: add habit → appears on grid → edit name → reflects on grid → archive → disappears

### Slice 4: Passkey (WebAuthn) login

**Why fourth**: security-critical; complex flow; user-visible change.

**Scope**:
- `WEBAUTHN_RP_ID=10.8.0.1` env fix
- `/login` Stage 2: passkey button → begin → complete → JWT
- `/auth/webauthn/login/{begin,complete}` backend
- `beaver_webauthn` ↔ `castor_webauthn_browser` cookie mirror
- Playwright's `WebAuthnVirtualAuthenticator` for headless testing

**Tests added**:
- Backend: existing `test_lane2_webauthn.py`
- Playwright E2E: register a virtual passkey, log out, log in with passkey

**Done when**:
- Passkey registration flow works end-to-end
- rpId mismatch no longer occurs

### Slice 5: `/security` page (passkey list / add / remove / password change)

**Why fifth**: extends passkey; uses SecurityContent React island.

**Scope**:
- `/security` page mounts `<SecurityContent client:load />`
- List/add/remove passkeys
- Change password (current + new + confirm)
- Replace `prompt()` chains with shadcn `Dialog` (UX improvement)

**Tests added**:
- Backend: existing `test_sensitive_actions.py`
- Playwright E2E: add passkey, see in list, remove with password confirm

### Slice 6: Import / Export

**Why sixth**: data-portability; touches backend gap.

**Scope**:
- Ship `POST /api/v1/habits/import` backend (closes parity-matrix gap 6.3)
- `/export` page already wired; verify round-trip
- `/import` page: paste JSON OR upload file → POST → success

**Tests added**:
- Backend: `test_apis.py::test_import_round_trip`
- Playwright E2E: export → import → habits present

### Slice 7: Stats + heatmap

**Why seventh**: read-only; low-risk.

**Scope**:
- `/stats` page: per-habit streak cards, 15-week heatmap
- Date-range picker (closes parity-matrix gap 3.2)

**Tests added**:
- Backend: existing
- Playwright E2E: load `/stats`, verify cards render

### Slice 8: Settings + Help + `/help` page (the visible 404)

**Why eighth**: closes the user-visible 404.

**Scope**:
- Create `web/concepts/src/pages/help.astro` (closes parity-matrix gap 4.4)
- Update `web/concepts/src/components/MoreSheet.tsx` so its `/help` link works
- Custom CSS persistence backend endpoint
- Habit-display preference write endpoint

**Tests added**:
- Playwright E2E: tap Help in mobile MoreSheet → land on /help → 4 links visible

### Slice 9: Admin + cutover prep

**Why ninth**: superuser-only; needed before cutover for backup trigger.

**Scope**:
- `/admin` page final review
- `/api/v1/admin/users` + `/api/v1/admin/backup` confirmed working
- Cutover runbook dry-run on test data

### Slice 10: Cutover (T-0)

This is not a code slice; it's the operational cutover per §8.

---

## 11. Open items still flagged

These are decisions I made on the user's behalf during Phase 2 design, marked explicitly:

- **Single-port model (Astro binds 8080, reverse-proxies `/auth`, `/api/v1`, `/users`, `/webauthn` to gunicorn on 127.0.0.1:8090)** — simplest URL stability.
- **Volume name `beaver_data` keeps its name** for v1 cutover (cosmetic rename deferred).
- **Container name changes from `beaverhabits` to `castor`** — affects `docker ps`, `docker logs`, healthcheck probes. Apollo skill references `beaverhabits`; update the skill after cutover.
- **Image build uses Node 22 + pnpm** — matches `web/concepts/package.json:engines.node`.
- **Playwright for browser E2E** — TypeScript-native, fits the Astro stack.
- **Cookie `Max-Age` change requires a code patch in `lib/auth.ts`** before cutover (7d → 30d).

---

## 12. Phase 2 sign-off criteria

Before starting Phase 3 implementation:

- [ ] User signs off on this architecture
- [ ] Slice ordering approved (§10)
- [ ] Test strategy approved (§7)
- [ ] Cutover plan rehearsed on safe test data
- [ ] Backup + rollback rehearsed on safe test data

I'll hold here for your review before starting Slice 1.
