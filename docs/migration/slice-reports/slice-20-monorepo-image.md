# Slice 20 — Monorepo Docker image build (Astro stage + production proxy)

**Branch**: `release/astro-migration` (parent `a8e5819` from slice 19)
**Date**: 2026-09-24

## Trigger

The slice-19 cutover runbook flagged that the existing
`docker/Dockerfile` (49 lines) **only contained the Python backend**
— no Astro build stage, no Node runtime, no production proxy. The
runbook's architectural choice was **monorepo image** (option A),
but the build artifacts were missing.

## Discovery during this slice

While surveying, found a **gap in the slice 15 audit**: it only
checked `/api/v1/*` paths. The client-side `SecurityContent.tsx`,
`login.astro` (passkey flow), and `register.astro` (passkey flow)
all use `fetch(\`${BACKEND_URL}/auth/webauthn/...\`)`. In dev this
works because `astro.config.mjs` has a Vite proxy for `/auth`,
`/users`, `/webauthn`, `/health`. **In production without a proxy,
these fetches would 404** — the page would render but the passkey
UI would be dead.

This is the same class of bug as slice 15 caught for `/api/v1/*`:
backend tests passed because they never went through the Astro
BFF / proxy layer. Slice 20's middleware proxy closes that gap.

## Changes

### 1. `docker/Dockerfile` (rewritten, +57 lines)

- New `FROM node:22.12-bookworm-slim AS astro-builder` stage that
  runs `pnpm install --frozen-lockfile` then `pnpm exec astro build`.
- Production stage now copies `/build/dist/` (Astro output) +
  `/build/node_modules/` + `/build/package.json` into
  `/app/web/concepts/`.
- CMD switched from `["sh", "start.sh", "prd"]` to a supervisor
  pattern that:
  - Launches `gunicorn castor.main:app --bind 127.0.0.1:8081` in
    the background.
  - Traps SIGTERM/SIGINT and forwards to gunicorn's PID.
  - `exec`s the Astro standalone entry on the public `:8080`,
    becoming PID 1.
- Node version pinned to ≥22.12.0 to match Astro 7.3.3's requirement
  and the project's `engines` field.
- Sharp (libvips) constraint forces `bookworm-slim` — Alpine would
  require a source build of sharp and was rejected.

### 2. `web/concepts/src/middleware.ts` — production proxy (+50 lines)

- `PROXY_PREFIXES = ['/auth/', '/users/', '/webauthn/', '/health']`
  (initial slice-20 commit; later narrowed to `'/auth/webauthn/'`
  in slice 21 after the user picked option B).
- Skips the proxy when `import.meta.env.DEV === true` — dev mode
  uses the Vite proxy in `astro.config.mjs` (no double-hop).
- Forwards method, headers (with hop-by-hop stripped: `connection`,
  `keep-alive`, `transfer-encoding`), and body (read once as
  `arrayBuffer()`).
- Mirrors the backend's WebAuthn browser cookie
  (`beaver_webauthn` → `castor_webauthn_browser`) so the upstream
  session survives the proxy hop.
- BFF routes (`/api/auth/webauthn/*`) are at a different prefix so
  they pass through naturally — no skip list needed (initially;
  ASTRO_HANDLED_AUTH_PATHS added in slice 21).

### 3. `docker-compose.yml` (production config)

- Image tag pinned: `castor:custom-2026-09-XX` (placeholder — must
  be replaced with actual cutover date).
- `WEBAUTHN_RP_ID`: `localhost` → `10.8.0.1` (matches apollo origin).
- `WEBAUTHN_ORIGIN`: `http://localhost:8080` → `http://10.8.0.1:8080`.
- `BACKEND_URL`: added → `http://127.0.0.1:8081` (the gunicorn
  supervisor socket).
- Optional `env_file: ./.user/.secrets.env` for the placeholder
  `JWT_SECRET` / `RESET_PASSWORD_TOKEN_SECRET` /
  `NICEGUI_STORAGE_SECRET` env entries.
- Container name kept as `beaverhabits` initially (to preserve
  monitoring / log filters / backup scripts); **renamed to `castor`
  in slice 21**.

## Verification

- `pnpm exec astro check` → 0 errors / 0 warnings / 62 hints.
- pytest `tests/test_slice{1,11,12}*.py` → 36 passed + 6 skipped.
- **Local production server smoke test** (port 9090 — ssh had
  8080 bound):
  - Astro root `GET /` → 307 redirect to `/login`
  - Astro `GET /login` → 200
  - Astro `GET /help` → 200
  - Astro `GET /habits` (no auth) → 302 → `/login`
  - **Astro `GET /health` → 200 OK** (via middleware proxy)
  - **Astro `POST /auth/webauthn/check` → 200 JSON** (via middleware
    proxy)
- **Dev mode regression check** (port 4321):
  - All same paths still work via Vite proxy (middleware correctly
    skips proxying in `import.meta.env.DEV`).
- Docker build NOT verified locally — no docker daemon on this
  dev box. The standalone `entry.mjs` smoke test confirms the
  Astro side; the gunicorn CMD pattern is standard and should
  work but **should be validated with an actual `docker build` on
  apollo or a CI runner before cutover**.

## Out-of-band discoveries worth flagging

1. **`dist/` was not gitignored before this slice**. It is now.
2. **Sharp forces bookworm-slim** — Alpine would break. Documented
   in Dockerfile comment.
3. **`pnpm install --prod` only saves 1MB** — the 345MB
   `node_modules` is mostly Astro+React+Radix+Tailwind, all
   runtime-required. No production-slimming opportunity found.

## What this slice does NOT touch

- No backend code changes (gunicorn runs the same `castor.main:app`
  as before).
- No BFF route changes (middleware is the production proxy now;
  Vite proxy handles dev).
- No parity matrix changes — this is infrastructure, not UX.
