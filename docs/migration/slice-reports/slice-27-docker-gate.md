# Slice 27 — Phase-4 Docker gate: build image, smoke, persistence, backup/restore rehearsal

**Branch**: `release/astro-migration`
**Date**: 2026-09-26 (built 2026-09-25 late)
**Host**: apollo (Docker 29.8.0; homelab2 has no docker daemon)
**Result**: ALL GATES PASSED on `castor:custom-2026-09-25` (1.5GB, built at `/opt/castor-build/`, NOT `/opt/castor/` — legacy untouched throughout).

## Scope

Slice 20 shipped the Dockerfile without ever building it ("Docker build NOT
verified locally — no docker daemon on this dev box", slice-20 report).
This slice closed that: build the monorepo image on apollo from a clean
source tarball, then run the Phase-4 release-gate rehearsal (runbook §5/§6
steps 3-4, 12-13) against a throwaway container + volume.

## Build failures found + fixed (4 real bugs, each reproduced before fixing)

1. **corepack signing keyring stale** — `node:22.12-bookworm-slim`'s
   corepack hard-fails fetching pnpm: `Cannot find matching keyid`
   (npm key rotation after the base image shipped).
   Fix: `RUN npm install -g pnpm@12.5.1` (pins to the local dev
   toolchain version; npm treats signatures as advisory). Reproduced the
   original failure in the build log at 2026-09-25 22:xx.

2. **`pnpm-workspace.yaml` missing from the install layer** — pnpm ≥10
   no longer reads `pnpm.onlyBuiltDependencies` from package.json, and
   the build approvals (`allowBuilds: esbuild, @tailwindcss/oxide,
   sharp`) live in the workspace file. Dockerfile only copied
   package.json + lockfile → `ERR_PNPM_IGNORED_BUILDS` (esbuild).
   Reproduced in a clean scratch dir with pnpm 12.5.1 before patching.
   Fix: `COPY web/concepts/pnpm-workspace.yaml` into the layer.

3. **`uv.lock` still declared root package `beaverhabits`** — the
   branch renamed the package to `castor` in pyproject.toml (slice
   lineage) but never re-locked; `uv sync --frozen` in Docker fails
   with `Could not find root package 'castor'` (line 24 error, exit 2).
   Local dev masked it (venv built with uv 0.12.1 without --frozen).
   Fix: `uv lock` regen — verified pure rename: same 22 deps, zero
   version changes (git diff shows only beaverhabits→castor + entry
   reordering). **Full suite re-run against the regenerated lock:
   392 passed / 12 skipped** (same as baseline). Also verified the
   exact Docker-layer command with uv 0.5.26 (the version the image
   pins) in a scratch dir: sync exit 0.

4. **No Node.js runtime in the production stage** — CMD execs
   `node web/concepts/dist/server/entry.mjs` but the stage is
   `python:3.14-slim`; container died instantly with
   `exec: node: not found` (exit 127). The single most damning
   evidence that slice 20's image had never been booted.
   Fix: `COPY --from=astro-builder /usr/local/bin/node` + `apt-get
   install libstdc++6` (node's only non-glibc dep).

5. **`import.meta.env.BACKEND_URL` inlined as undefined at build time**
   — ~15 server files (pages/api/v1/habits*.ts, habits/[id].astro,
   tokens.astro, …) read `import.meta.env.BACKEND_URL` which Vite
   inlines AT BUILD; no build env was set → silent fallback to dev's
   `localhost:8085` → habits BFF 500 (`TypeError: fetch failed`) in
   the container. The `process.env.BACKEND_URL` sites (auth.ts,
   login.astro, middleware.ts) were fine — that split is what made
   login work while habits failed.
   Fix: `ENV BACKEND_URL=http://127.0.0.1:8081` in the astro-builder
   stage before `astro build` (placed after the cached pnpm layers).
   Trade-off (documented in-file): changing the backend port now
   requires an image rebuild.

## Gates exercised on the built image (throwaway volume `castor_smoke`, 127.0.0.1:9095, legacy untouched)

| Gate | Evidence |
|---|---|
| Image builds | `docker build -f docker/Dockerfile -t castor:custom-2026-09-25 .` at `/opt/castor-build/` → success, 1.5GB |
| Backend imports | `docker run --rm … python -c "import castor.main"` → `import ok` (runbook step 3) |
| Container boots, both processes | Astro listening :8080 + gunicorn :8081 in one container |
| Health | backend `/health` 200 via in-container check; Astro `/health` 200 through middleware proxy |
| Page smoke | `/` 200, `/login` 200, `/help` 200, `/terms` 200, `/privacy` 200; `/habits` unauth 302 |
| Middleware proxy (runbook step 13) | `POST /auth/webauthn/check` → 200 `{"has_passkey":false,"passkey_offer_dismissed":false}` (first hit 8.8s cold DB, then 85ms) |
| Register + auto-login (real form path) | `POST /login action=login_or_register` (email+password+confirm, Origin/Referer set) → 302 `stage=offer_passkey`, `castor_token` cookie set, `/habits` 200 |
| Read/write E2E | BFF `POST /api/v1/habits` → 200 `{id: e744fd}`; form `POST /habits/e744fd/complete` → 302; record verified in volume SQLite: `{day: 2026-09-26, done: true}` |
| Persistence across restart | `docker restart` → session cookie still valid, BFF GET habits 200 with the record present |
| Logout / session revocation | `POST /logout` → 302 `/login`; `token_version` 0→1 in DB; old cookie → 302 |
| Backup | `VACUUM INTO habits.db.smoke-backup` from inside the app container |
| Restore + integrity | stop → restore via sidecar → `PRAGMA integrity_check` = ok; Smoke habit + today's tick present; re-login 302, BFF 200, record count 1 |
| Production untouched | `beaverhabits Up (healthy)` throughout; smoke container + volume deleted after rehearsal |

## Rehearsal finding (runbook-relevant)

Restoring via a **root sidecar** (`docker run --rm -v … alpine cp …`)
left the DB root-owned and the app (USER nobody) failed to boot:
`sqlite3.OperationalError: attempt to write a readonly database`,
gunicorn worker exit 3. Fixed with `chown -R 65534:65534` and re-verified.
The runbook's rollback path (`docker exec <app-container> cp …`) runs as
the app user and would not hit this — but any volume-copy / sidecar
restore MUST include the chown step. Recommend adding this note to the
slice-19 runbook §Rollback before cutover day.

Fresh-user note (pre-existing, not a Docker regression): UI-registered
users have no `habit_list` row until their first POST (fastapi-users
`/auth/register` doesn't seed it; only trusted-local-email does via
`views.register_user`). `/habits` renders its empty state on 404 and
POST lazily creates the list, so the UX is fine — recorded here so
nobody re-diagnoses the BFF 404 as a deployment bug.

## Commands (reproducible)

```bash
# source to apollo build dir (NOT /opt/castor):
tar czf src.tgz --exclude=.git --exclude=node_modules --exclude=.venv \
  --exclude='*.db' --exclude=.user --exclude=Desktop … .   # 3.7MB
scp src.tgz apollo:/opt/castor-build/ && ssh apollo 'cd /opt/castor-build && tar xzf src.tgz'

# build:
ssh apollo 'cd /opt/castor-build && docker build -f docker/Dockerfile -t castor:custom-2026-09-25 .'

# smoke (throwaway):
docker volume create castor_smoke
docker run -d --name castor-smoke -p 127.0.0.1:9095:8080 -v castor_smoke:/app/.user \
  -e DEBUG=true -e HABITS_STORAGE=DATABASE -e JWT_SECRET=… -e WEBAUTHN_RP_ID=127.0.0.1 …
docker run --rm -v castor_smoke:/data alpine chown -R 65534:65534 /data   # fresh volume must be nobody-owned
```

## What's still open before cutover

1. **Cutover date + tag** — image exists as `castor:custom-2026-09-25`;
   runbook §6 still needs the T-1h pre-flight on the day.
2. **Backup/restore chown note** into runbook §Rollback (small doc fix).
3. **SMTP env for circles email** on apollo (slice 23c wiring is in,
   real delivery needs SMTP_HOST etc. in `.secrets.env`).
4. **rpId verification from a VPN client** (runbook §5 step) — can only
   be done from a real VPN session, on cutover day.
5. Deferred parity rows unchanged (7, all intentional — see
   `audit_parity_matrix.py`).

## Files changed this slice

- `docker/Dockerfile` — 4 fixes (pnpm install path, workspace file,
  node runtime, BACKEND_URL build env), each commented in-file.
- `uv.lock` — regenerated root package (beaverhabits → castor), pure
  rename, no version changes; 392/12 test result unchanged.
- `Desktop/migration/slice-reports/slice-27-docker-gate.md` — this file.
- `Desktop/migration/PROGRESS.md` — status refresh.
