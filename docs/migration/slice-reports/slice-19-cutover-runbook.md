# Slice 19 — Phase-4 cutover runbook

**Branch**: `release/astro-migration`
**Date**: 2026-09-24
**Deploy target**: apollo, container `castor-beaverhabits` (renamed from
`beaverhabits` if you choose to do that as part of cutover — optional)

This runbook documents every change that must land between the last
slice commit and the moment users start hitting the migrated app at
`http://10.8.0.1:8080`. It is intentionally prescriptive: each step
lists the exact files, commands, and verifications. The order matters.

---

## 0. Pre-cutover verification checklist

Run these from the dev box BEFORE cutting the apollo deploy. They
catch the regression classes we hit during slice 15 (BFF routes) and
slice 17 (missing Back links) so we don't repeat them in production.

```bash
cd /home/joel/castor-repo

# Astro type-check + lint
cd web/concepts && pnpm exec astro check
# Expect: 0 errors, 0 warnings. Hints are fine.
# (slice 27 baseline: 0 errors / 0 warnings / 58 hints, 108 files)

# Python tests (no live integration)
cd ../..
.venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
# Expect (slice 27/28 baseline): 392 passed, 12 skipped.

# Parity matrix audit (catches stale evidence)
python3 Desktop/migration/scripts/audit_parity_matrix.py | head -10
# Expect: open-status rows limited to the 7 intentional/deferred gaps
# (3.4, 6.2, 6.4, 7.3, 11.2, 12.2, 12.5) — anything else is a
# regression. The script prints file-existence evidence for each.

# BFF coverage audit (catches missing Astro BFF proxies)
python3 Desktop/migration/scripts/audit_bff_coverage.py | head -5
# Expect: "(none)" under "Client-side callers of /api/v1/* without an
# Astro BFF proxy". (slice 28: the last gap — the circles
# share-your-habit wizard POST — got its BFF route; before that this
# audit caught a real production-404 bug the dev Vite proxy masked.)

# Browser smoke walk-through on the demo seed (see scripts/dev-up.sh)
bash scripts/dev-up.sh
# ... manually click through /login, /habits, /settings, /tokens,
# /stats, /circles, /account/delete, /help. Verify:
#   - toggles persist (Display card, theme, tokens)
#   - tick cells toggle and persist across reloads
#   - Circles: create, share habit, mint invite (link mode), revoke
#   - Delete account confirmation flow
bash scripts/stop-dev.sh
```

If any of the above fails, **stop and fix the regression before
cutover**. Cutting with broken tests means rolling back under load.

### 0b. Docker image gate (slice 27) — verify BEFORE cutover day

The image must be built and smoke-tested on apollo ahead of time;
slice 27 proved the image can be built and pass all runtime gates.
Re-run the build + smoke on cutover day only if source changed since:

```bash
# 1. Ship source to the apollo build dir (NOT /opt/castor — that is
#    the live legacy tree). From the dev box:
tar czf /tmp/src.tgz --exclude=.git --exclude=node_modules \
  --exclude=.venv --exclude='*.db' --exclude=.user \
  --exclude=Desktop --exclude=.hermes .
scp /tmp/src.tgz apollo:/opt/castor-build/
ssh apollo 'cd /opt/castor-build && tar xzf src.tgz'

# 2. Build (subsequent builds reuse the pnpm/uv layer cache):
ssh apollo 'cd /opt/castor-build && docker build -f docker/Dockerfile \
  -t castor:custom-<DATE> .'

# 3. Import gate (runbook step 3 equivalent):
ssh apollo 'docker run --rm --entrypoint /opt/pysetup/.venv/bin/python \
  castor:custom-<DATE> -c "import castor.main; print(\"ok\")"'

# 4. Throwaway smoke (full sequence, all gates, ~10 min):
#    see slice-reports/slice-27-docker-gate.md "Commands" —
#    volume + chown 65534, run on 127.0.0.1:9095 with DEBUG=true,
#    verify /health, pages, register+login form, habit create + tick
#    in SQLite, restart persistence, logout token_version bump.
# 5. Clean up: docker rm -f castor-smoke && docker volume rm castor_smoke
```

Gates that must pass before cutover day proceeds (all verified in
slice 27 on castor:custom-2026-09-25): import, dual-process boot,
/health via proxy, page smoke, register + auto-login via the real
form path, BFF habit create + form tick verified in SQLite,
docker-restart persistence, logout + token_version bump, and
backup/restore + integrity + re-login on the throwaway volume.

---

## 1. Decisions already taken (do not revisit)

These were decided 2026-09-24 and approved before slice 15. They
constrain this runbook; do not change them without a new explicit
user decision.

| # | Decision | Why it's locked |
|---|---|---|
| 1 | Cut `release/astro-migration` (this branch) for cutover, not `main`. | The fork's `main` is the legacy NiceGUI baseline; `release/astro-migration` is the migration branch with all 18 prior slices. |
| 2 | Bump `token_version` for **every user** on cutover. | Forces re-login; clears any stale JWTs issued by the legacy image. (See §4.) |
| 3 | Fix Apollo `WEBAUTHN_RP_ID` mismatch on cutover. | Currently `WEBAUTHN_RP_ID=localhost` in `/opt/wg-net/docker-compose.yml`; production origin is `http://10.8.0.1:8080`, so rpId should be `10.8.0.1`. (See §5.) |
| 4 | Deployment = **monorepo Docker image** (option A). | One image runs both FastAPI backend and Astro frontend behind a single port. Not two services. |
| 5 | Cookie `maxAge` 7d → 30d to match backend JWT. | Done in slice 1 (`writeSession()` in `web/concepts/src/lib/auth.ts`). Already in this branch — no extra work. |

---

## 2. Repo changes required BEFORE building the new image

### 2.1 docker/Dockerfile — add Astro build stage

Current Dockerfile copies `castor/`, `statics/`, `start.sh`, and
`healthcheck.py`. It does **not** build or run the Astro frontend.
The migration branch needs this multi-stage build:

```dockerfile
# Existing python-base, builder-base, production stages unchanged.
# ADD an Astro build stage that runs `pnpm install && pnpm build`
# and produces web/concepts/dist/

FROM node:22-bookworm-slim AS astro-builder
WORKDIR /build
COPY web/concepts/package.json web/concepts/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY web/concepts ./
RUN pnpm exec astro build

# In the production stage, copy the Astro build output:
COPY --from=astro-builder /build/dist /app/web/concepts/dist
COPY --from=astro-builder /build/node_modules /app/web/concepts/node_modules

# Update HEALTHCHECK to hit /api/health (Astro proxies to backend).
# Update CMD to run both backend (gunicorn) + Astro (node entry.mjs)
# behind a process supervisor.
```

**Process supervisor pattern** (simplest, single supervisor):

```dockerfile
# Replace CMD with a tiny shell that runs both processes, the
# backend in background, the Astro frontend on PID 1.
# Astro binds 0.0.0.0:8080, the backend binds 127.0.0.1:8081.
CMD ["sh", "-c", "gunicorn castor.main:app --bind 127.0.0.1:8081 -w 1 -k uvicorn_worker.UvicornWorker --max-requests 10000 --log-level info & BACKEND_PID=$!; trap 'kill $BACKEND_PID' EXIT; HOST=0.0.0.0 PORT=8080 BACKEND_URL=http://127.0.0.1:8081 NICEGUI_STORAGE_PATH=/app/.user/.nicegui exec node web/concepts/dist/server/entry.mjs"]
```

**Astro Vite proxy in production** (`astro.config.mjs`):
The existing `vite.server.proxy` only runs in dev mode. For
production, Astro needs a Node middleware that proxies `/auth/*`,
`/users/*`, `/webauthn/*`, and any non-overridden `/api/v1/*` to
`BACKEND_URL`. Add a small `src/middleware.ts` (already exists — see
slice 15 audit) that proxies these paths using Node's `fetch`.

### 2.2 web/concepts/src/middleware.ts — add production proxy

Currently middleware only runs auth gating. Extend it to also proxy
backend routes that haven't been moved to Astro BFF:

```ts
// In src/middleware.ts, add an early-return proxy for /auth, /users,
// /webauthn, and any /api/v1/* path not matched by a BFF route.
```

(Reference: `web/concepts/src/middleware.ts` — verify the existing
Astro BFF routes in `pages/api/v1/` cover everything before adding
this. The slice 15 audit script reports which routes still need it.)

### 2.3 docker-compose.yml — pin image, fix WEBAUTHN_RP_ID

```yaml
services:
  castor:
    build:
      context: .
      dockerfile: docker/Dockerfile
    container_name: castor          # rename from beaverhabits if you choose
    network_mode: "container:wg-easy"
    image: castor:custom-2026-09-XX  # pin to a custom tag — NEVER :latest
    environment:
      - HABITS_STORAGE=DATABASE
      - WEBAUTHN_RP_ID=10.8.0.1                  # FIX (was localhost)
      - WEBAUTHN_ORIGIN=http://10.8.0.1:8080
      - BACKEND_URL=http://127.0.0.1:8081         # internal-only
      - JWT_SECRET=...                            # from .secrets.env
      - RESET_PASSWORD_TOKEN_SECRET=...
      - NICEGUI_STORAGE_SECRET=...
      - REQUIRE_ADMIN_FOR_REGISTRATION=false
    volumes:
      - castor_data:/app/.user/
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "python healthcheck.py"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  castor_data:
    external: true
```

`castor_data` is the existing `beaver_data` volume (or new — see §3).

---

## 3. Volume migration

The legacy image uses `beaver_data`. The migration branch should reuse
the same volume to preserve all user data (habits, ticks, configs,
tokens, circles, audit events):

```bash
# On apollo:
sudo /usr/bin/docker volume inspect beaver_data --format '{{.Name}}'
# If you want a clean name, alias it:
sudo /usr/bin/docker volume create castor_data
# Then point compose at castor_data and migrate data with the sqlite3
# backup API (per apollo-beaverhabits skill):
#
# NOTE (slice 28): the legacy image has NO sqlite3 CLI (verified
# 2026-09-26: `docker exec beaverhabits which sqlite3` → not found),
# so use the app's own Python. The heredoc form is deliberate: it
# avoids the nested-quote traps that break `python -c "…VACUUM INTO
# '…'…"` when run through ssh. Validated verbatim on a throwaway
# volume (slice 28): writes a real 270KB backup as the app user.
sudo /usr/bin/docker exec -i beaverhabits /opt/pysetup/.venv/bin/python - <<'PY'
import sqlite3
c = sqlite3.connect('/app/.user/habits.db')
c.execute("VACUUM INTO '/app/.user/habits.db.castor-backup'")
print('backup written')
c.close()
PY
sudo /usr/bin/docker run --rm \
  -v beaver_data:/from -v castor_data:/to \
  alpine sh -c "cp /from/* /to/ && chown -R 65534:65534 /to"
```

**Ownership is load-bearing** (slice 27 rehearsal finding): both the
legacy and migration images run as `nobody` (uid 65534). Any volume
copy/restore done by a root sidecar (or any file written by root into
the volume) leaves the DB root-owned and the next container boot dies
with `sqlite3.OperationalError: attempt to write a readonly database`
(gunicorn worker exit 3). The `chown -R 65534:65534` above is NOT
optional cleanup — without it the app will not start. If a restore is
ever done with a different sidecar, re-run the chown before `docker
compose up`.

**Decision required**: keep `beaver_data` (zero-effort) or rename to
`castor_data` (cleaner but requires a brief cutover outage for the
volume move). The user has not yet chosen; **default is to keep
`beaver_data`**.

---

## 4. token_version bump (forced re-login)

Per decision #2, every user gets their `token_version` bumped on
cutover so any JWTs issued by the legacy image become invalid. Run
this **once** on apollo BEFORE pointing compose at the new image:

```bash
# On apollo, inside the running container:
# (slice 28: cursor.rowcount — connection.rowcount does not exist and
#  would crash before commit, bumping nobody. Validated 2026-09-26.)
sudo /usr/bin/docker exec beaverhabits /opt/pysetup/.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/app/.user/habits.db')
cur = c.execute('UPDATE user SET token_version = token_version + 1')
print('Bumped', cur.rowcount, 'users')
c.commit()
c.close()
"

# Verify:
sudo python3 - <<'PY'
import sqlite3
c = sqlite3.connect('/var/lib/docker/volumes/beaver_data/_data/habits.db')
print('token_version distribution:',
      c.execute('SELECT token_version, COUNT(*) FROM user GROUP BY token_version').fetchall())
PY
```

The image restart (which `docker compose up -d` will trigger) loads
the bumped values into the in-memory user cache. All subsequent login
attempts issue JWTs with the new `ver` claim; legacy JWTs are rejected
by `VersionedJWTStrategy.read_token`.

**Side effect**: every user has to log in again on first hit. There is
no way to avoid this without a per-user migration of token_version,
which is more error-prone than a single SQL bump.

---

## 5. WebAuthn rpId fix

`WEBAUTHN_RP_ID` must match the host the browser sees. The compose
file on apollo currently has `localhost`, which only works for browser
sessions on the apollo host itself. VPN clients hit
`http://10.8.0.1:8080`, so the rpId should be `10.8.0.1`:

```yaml
WEBAUTHN_RP_ID: 10.8.0.1
WEBAUTHN_ORIGIN: http://10.8.0.1:8080
```

After the change, **existing passkeys still work in most browsers**
because they were registered against the previous rpId; the question
is whether the browser accepts the new origin's rpId when validating.
Safari (iOS) is more permissive than Chrome/Firefox on rpId suffix
matches, but for a clean cutover we recommend also bumping
`WEBAUTHN_RP_ID` AND informing users that they may need to
re-register their passkey on first login. The legacy NiceGUI does
NOT have a "delete passkey and re-register" flow as a single step —
users go to /security, delete the old passkey, then add a new one.

**Verification before cutover** (on apollo, inside wg-easy's netns):

```bash
/usr/bin/docker exec wg-easy wget -q -O - --timeout=10 \
  http://10.8.0.1:8080/auth/webauthn/login/begin
# Expect: 200 OK, JSON body
```

This must return 200 from outside the container (via VPN). The legacy
image with `WEBAUTHN_RP_ID=localhost` will fail this check for any
non-localhost origin.

---

## 6. Cutover sequence

This is the exact playbook. Each step is verified before moving to
the next.

### Pre-flight (T-1 hour)

```bash
# On apollo:
cd /opt/castor

# 1. Source must be on release/astro-migration, clean working tree
git status --short
git log --oneline -1
# Expect: clean tree, HEAD = 3b60ba2 or later (latest slice commit).

# 2. Build the new image
sudo /usr/bin/docker build \
  -f /opt/castor/docker/Dockerfile \
  -t castor:custom-2026-09-XX \
  /opt/castor

# 3. Verify image builds and main imports cleanly
sudo /usr/bin/docker run --rm castor:custom-2026-09-XX \
  /opt/pysetup/.venv/bin/python -c "import castor.main; print('ok')"

# 4. Backup the DB BEFORE touching the running container
# (slice 28: legacy image has no sqlite3 CLI; heredoc form avoids
#  nested-quote breakage over ssh — validated on a throwaway volume)
sudo /usr/bin/docker exec -i beaverhabits /opt/pysetup/.venv/bin/python - <<'PY'
import sqlite3, subprocess
c = sqlite3.connect('/app/.user/habits.db')
target = '/app/.user/habits.db.pre-cutover-' + subprocess.run(
    ['date', '-u', '+%Y%m%dT%H%M%SZ'], capture_output=True, text=True
).stdout.strip()
c.execute("VACUUM INTO '" + target + "'")
print('backup written:', target)
c.close()
PY
```

### Cutover (T+0, ~5 min downtime)

```bash
# 5. Stop the running container
cd /opt/wg-net
sudo /usr/bin/docker compose stop castor
# (or 'beaverhabits' if you kept the old service name)

# 6. Bump token_version (see §4)
# (slice 28: stock alpine has no sqlite3 CLI — verified 2026-09-26.
#  Use the venv Python from the already-built new image instead.
#  Validated verbatim on a throwaway volume: cursor.rowcount, not
#  connection.rowcount — the §4 phrasing would have crashed before
#  commit and silently bumped nobody.)
sudo /usr/bin/docker run --rm -i \
  -v beaver_data:/app/.user \
  --user 65534:65534 \
  castor:custom-2026-09-25 /opt/pysetup/.venv/bin/python - <<'PY'
import sqlite3
c = sqlite3.connect('/app/.user/habits.db')
cur = c.execute('UPDATE user SET token_version = token_version + 1')
print('Bumped', cur.rowcount, 'users')
c.commit()
c.close()
PY

# 7. Verify the bump
sudo python3 - <<'PY'
import sqlite3
c = sqlite3.connect('/var/lib/docker/volumes/beaver_data/_data/habits.db')
print('MIN,MAX token_version:', c.execute('SELECT MIN(token_version), MAX(token_version) FROM user').fetchone())
PY
# Expect: (N, N) where N > 0 (everyone bumped by 1).

# 8. Update compose env (WEBAUTHN_RP_ID) — DO THIS BEFORE RESTART
sudo sed -i 's/WEBAUTHN_RP_ID=localhost/WEBAUTHN_RP_ID=10.8.0.1/' \
  /opt/wg-net/docker-compose.yml
sudo sed -i 's|WEBAUTHN_ORIGIN=http://localhost:8080|WEBAUTHN_ORIGIN=http://10.8.0.1:8080|' \
  /opt/wg-net/docker-compose.yml

# 9. Update compose image tag
sudo sed -i 's/castor:custom-[0-9-]*/castor:custom-2026-09-XX/' \
  /opt/wg-net/docker-compose.yml
grep image /opt/wg-net/docker-compose.yml  # verify

# 10. Start the new container
sudo /usr/bin/docker compose up -d

# 11. Wait for health
for i in 1 2 3 4 5 6 7 8 9 10; do
  if sudo /usr/bin/docker exec wg-easy wget -q -O /dev/null --timeout=5 \
       http://10.8.0.1:8080/health 2>/dev/null; then
    echo "UI UP after ${i} attempts"
    break
  fi
  sleep 5
done
```

### Post-cutover verification (T+10 min)

```bash
# 12. Page-level smoke
sudo /usr/bin/docker exec wg-easy wget -q -O /dev/null --timeout=10 http://10.8.0.1:8080/        && echo "ROOT OK"
sudo /usr/bin/docker exec wg-easy wget -q -O /dev/null --timeout=10 http://10.8.0.1:8080/login   && echo "LOGIN OK"
sudo /usr/bin/docker exec wg-easy wget -q -O /dev/null --timeout=10 http://10.8.0.1:8080/help    && echo "HELP OK"

# 13. Backend reachable via Astro BFF (this is the new path the migration added)
sudo /usr/bin/docker exec wg-easy wget -q -O - --timeout=10 http://10.8.0.1:8080/auth/webauthn/check \
  --post-data='{"username":"demo@castor.example.com"}' --header='Content-Type: application/json' | head -c 200
# Expect: 200, JSON body with has_passkey

# 14. Try logging in via the demo seed (from a VPN client, NOT the
# apollo host, because the legacy rpId was localhost-only and iOS
# Safari may reject the new rpId). If login fails, immediately check
# the WebAuthn rpId fix.

# 15. Check the audit_event table is being written to
sudo python3 -c "
import sqlite3
c = sqlite3.connect('/var/lib/docker/volumes/beaver_data/_data/habits.db')
print('audit_event rows:', c.execute('SELECT COUNT(*) FROM audit_event').fetchone()[0])
print('tables:', [r[0] for r in c.execute('SELECT name FROM sqlite_master WHERE type=\"table\"')])
"
# Expect: audit_event table exists, has at least the rows written since
# cutover.
```

### Rollback

If anything fails during post-cutover verification:

```bash
# Revert compose to the previous image tag
sudo sed -i 's/castor:custom-2026-09-XX/castor:custom-2026-09-14/' \
  /opt/wg-net/docker-compose.yml

# Recreate with the old image
cd /opt/wg-net
sudo /usr/bin/docker compose up -d --force-recreate

# Verify old image is serving
sudo /usr/bin/docker inspect castor --format '{{index .Config.Image}}'
# Expect: castor:custom-2026-09-14

# Restore DB if you took a snapshot in step 4
# (slice 28: sqlite3 CLI doesn't exist in the app image either —
#  use Python; heredoc form. The restore runs INSIDE the app
#  container, so files are written as the app user — no chown needed
#  on this path. Replace RESTORE_THIS with the actual filename.)
sudo /usr/bin/docker exec -i beaverhabits /opt/pysetup/.venv/bin/python - <<'PY'
import glob, shutil, sqlite3
candidates = sorted(glob.glob('/app/.user/habits.db.pre-cutover-*'))
if not candidates:
    raise SystemExit('No pre-cutover backup found in /app/.user/')
latest = candidates[-1]
print('Restoring from:', latest)
shutil.copy(latest, '/app/.user/habits.db')
c = sqlite3.connect('/app/.user/habits.db')
print('integrity:', c.execute('PRAGMA integrity_check').fetchone()[0])
c.close()
PY
# Expect: integrity: ok

# Confirm rollback by re-running post-cutover verification (steps 12-13)
```

**Note on rollback time**: ~3 minutes for `docker compose up -d
--force-recreate` + DB restore. The user-facing downtime is bounded by
that. The legacy image's `castor:custom-2026-09-14` stays available as
the rollback target until you've verified the new image for at least
one week in production.

---

## 7. Open architectural questions (must answer before cutover)

These are the things this runbook cannot decide on the user's behalf.
**Stop and ask the user** if any are still open:

1. **Volume name**: keep `beaver_data` (zero-effort) or rename to
   `castor_data` (cleaner but adds a step)?
2. **Container name**: keep `beaverhabits` (legacy) or rename to
   `castor` (matches the brand)? The skill says `beaverhabits`
   currently; renaming is fine but must propagate to monitoring,
   log filters, backup scripts, and any systemd unit.
3. **First-day user notice**: do we email users explaining the
   re-login requirement + possible passkey re-registration? (Castor
   has no built-in mailer; would need an external send or a
   server-side `audit_event` log query to identify affected users.)
4. **`/auth/login` mount point**: in the new monorepo image, where
   does the FastAPI backend's `/auth/login` route live? Two options:
   - **Astro reverse-proxies `/auth/*` to backend** (current plan,
     via `src/middleware.ts`). Single port, transparent to clients.
   - **Astro owns `/auth/login` server-side via `backendFetch`** and
     the backend still owns `/auth/webauthn/*`. Splits the surface.
5. **Astro Node version**: current image has Python 3.14. The Astro
   build stage needs Node ≥22.12.0 (per `package.json` engines).
   Slim base images for both stages to keep image size reasonable.

---

## 8. What this runbook does NOT cover

- **DNS / TLS**: castor is behind wg-easy, served over plain HTTP on
  port 8080. There is no public TLS termination.
- **Backups**: the existing `/opt/castor-backups/` snapshots are the
  source of truth for rollback targets. The runbook preserves the
  pre-cutover DB as a named snapshot; the regular backup cadence is
  unchanged.
- **Monitoring**: container health is via the existing
  `healthcheck.py` (returns 200 on `/health`). The Apollo skill's
  "Reachability from inside the VPN netns" probe (run from
  `wg-easy`) is the right post-cutover check.
- **Cookie domain / TLS migration**: not relevant — cookies are
  domain-scoped to `10.8.0.1` and HTTPS is not in play.

---

## 9. Reference: file inventory for cutover

Files this runbook expects to change in `release/astro-migration`
**before** cutover (not yet committed in any slice):

| File | Change |
|---|---|
| `docker/Dockerfile` | Add `astro-builder` stage; copy Astro build to production; update CMD to run both processes |
| `docker-compose.yml` | Pin image tag, fix `WEBAUTHN_RP_ID` and `WEBAUTHN_ORIGIN`, add `BACKEND_URL` |
| `web/concepts/src/middleware.ts` | Add production proxy for `/auth`, `/users`, `/webauthn`, and unmigrated `/api/v1/*` |

Files this runbook references but does not change (already correct in
the migration branch):

- `web/concepts/astro.config.mjs` — Node adapter already configured
- `web/concepts/src/lib/auth.ts` — cookie maxAge already 30d
- All Astro BFF routes from slices 7, 11, 12, 15
- All Python backend routes (no Python changes needed for cutover)

---

## 10. Acceptance criteria for cutover

All of these must hold for at least 24h after cutover before the
legacy image tag is considered decommissioned:

1. Health check returns 200 from `wg-easy` netns.
2. `/login`, `/help`, `/privacy`, `/terms` return 200 (public pages).
3. Login with `demo@castor.example.com / DemoPass1234!` succeeds.
4. `/habits` renders 5 demo habits.
5. Toggling a tick cell persists across page reload (DB write).
6. Toggling a Display Card checkbox persists (DB write + cookie mirror).
7. `/tokens` create/rotate/revoke round-trips (3 DB state changes).
8. `/circles` create + share habit + mint invite works.
9. `/account/delete` confirmation flow renders without server error.
10. Audit events are written for: login, logout, password change, tick
    toggle, token create/rotate/revoke, circle create, account delete.
11. No 5xx responses in `docker logs` for 1 hour.

If any criterion fails, **rollback to `castor:custom-2026-09-14`** and
treat the migration branch as broken until root-caused.

---

## Status

📋 **Document only**. No code changes in this slice — the runbook
records the exact changes needed for cutover day so they don't get
re-derived under time pressure. The Dockerfile + middleware changes
are a separate slice ("slice 20: monorepo image build") that should
happen before this runbook is executed.
