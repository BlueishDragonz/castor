# Slice 21 — Apply cutover runbook answers

**Branch**: `release/astro-migration` (parent `1c2f696` from slice 20)
**Date**: 2026-09-24

## Trigger

The slice-19 cutover runbook (§7) flagged 5 architectural questions
blocking the deploy step. User answered all 5 on 2026-09-25 in a
single message:

1. Volume name: **`castor_data`** — already set in compose; volume
   data migration is cutover-day ops, not a code change.
2. Container name: **`castor`** — renamed from `beaverhabits`.
3. User notice / email: **none required**.
4. `/auth/login` strategy: **option B** — Astro owns `/auth/login`
   server-side via `backendFetch`; backend still owns
   `/auth/webauthn/*`. Surface is split.
5. Base images: **slim for both stages**.

## Changes

### 1. Middleware proxy surface (option B)

The slice-20 middleware proxied the entire `/auth/` prefix. With
option B, `/auth/login` is Astro-handled (the actual login flow is
`/login` page → server-side `backendFetch('/auth/login')`). If the
middleware also proxied `/auth/login`, two handlers would race for
the same path.

- Narrowed `PROXY_PREFIXES` from `['/auth/', ...]` to
  `['/auth/webauthn/', ...]`.
- Added `ASTRO_HANDLED_AUTH_PATHS` set listing `/auth/login`,
  `/auth/logout`, `/auth/forgot-password`, `/auth/reset-password`,
  `/auth/reset-password/check` (all server-side-only Astro paths
  that must NOT be intercepted by the middleware).

### 2. Container rename

`docker-compose.yml`: `container_name: beaverhabits` →
`container_name: castor`. **Note**: any monitoring / log filters /
backup scripts that reference the old container name need updating
on cutover day. The migration backup script lives at
`~/Desktop/beaverhabits-backup-2026-09-12-COPY2`.

### 3. Slim base images — verified already slim

- `FROM python:3.14-slim AS python-base` ✅
- `FROM node:22.12-bookworm-slim AS astro-builder` ✅
- Investigated `node:22.12-alpine`: REJECTED — sharp (libvips)
  needs glibc, and sharp IS used at runtime by Astro's image
  optimization. Switching to Alpine would require a source build
  of sharp (~5min build penalty) and has been historically
  unreliable.
- Investigated `pnpm install --prod`: only saves 1MB (most
  `node_modules` are runtime deps: astro, react, radix-ui,
  tailwind, lucide-react). Not worth the risk of breaking a
  runtime import.

### 4. Backend routing comment in compose

Updated the `BACKEND_URL` comment block to reflect the actual
proxy surface (`/auth/webauthn/*`, `/users/*`, `/webauthn/*`,
`/health`) and the rationale for excluding `/auth/login`.

## Verification

- `pnpm exec astro check` → 0 errors / 0 warnings / 62 hints.
- **Production smoke test** (port 9091):
  - `GET /auth/webauthn/check` → 200 JSON via middleware proxy ✅
  - `GET /health` → 200 OK via middleware proxy ✅
  - `POST /auth/login` → 404 (no proxy, no handler — by design;
    the actual login flow is `/login` page) ✅
  - `GET /login` → 200 Astro page render ✅
- **Dev mode regression check** (port 4321):
  - Vite proxy still handles all `/auth/*` paths ✅
  - Middleware correctly skips because `import.meta.env.DEV === true`
    ✅

## What this slice does NOT touch

- No backend changes (gunicorn config unchanged).
- No slice-19 runbook content changes — answers came in via the
  user's message; the runbook text in §7 stays as a record of
  the questions.
- No parity matrix changes (this is pure infrastructure).

## Cutover readiness

After this slice, the slice-19 runbook's §7 blocking questions are
all resolved. The next concrete steps toward the cutover are
operational, not code:

1. **Pick a cutover date** and replace the `castor:custom-2026-09-XX`
   image-tag placeholder in `docker-compose.yml`.
2. **Build the image** on apollo (`docker build -f docker/Dockerfile -t castor:custom-<date> .`)
   and verify both processes start cleanly.
3. **Cutover-day ops** (runbook §4-5):
   - `token_version` SQL bump for all users.
   - Pre-cutover image-tag swap on the running container.
   - ~5min downtime during `docker compose up -d`.
   - 10min post-cutover verification.
   - Rollback path (~3min target) by reverting to the legacy
     `castor:custom-2026-09-14` image.
4. **24h acceptance criteria monitoring** (runbook §6).
