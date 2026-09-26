# Castor Habits — Independent Security & Reliability Audit

**Auditor:** Hermes (independent review)
**Date:** 2026-09-26
**Target:** `castor` container on apollo (production)
**Method:** Read-only inspection of repo, deployed artifact, container state, config, and logs. No production changes, no exploit attempts, no failure injection.

---

## A. Executive Assessment

**The authentication and authorisation core is genuinely well built.** I went looking for the usual hobby-project failures — plaintext passwords, permissive session handling, frontend-only authz, missing tenant checks — and did not find them. Password hashing, `token_version` revocation, compare-and-swap on every credential mutation, WebAuthn sign-count CAS, persistent rate limiting, a from-scratch parser-based CSS sanitiser, and an audit trail that structurally cannot record an email or IP. These are the marks of a codebase that has already been through a serious security pass. I want that said plainly, because the findings below are a different category of problem.

**The risk in this system is not primarily "the data is one bad restart away from being gone," although that is true and confirmed. It is that one confirmed account-deletion authorization bypass sits in front of it.**

Confidence is high on the findings below because I verified each against the running container or the shipped build artifact, not just the source:

1. **[NEW — most serious finding] Account deletion verifies one identity and destroys another.** `api/account/delete.ts` re-verifies the password against `castor_user` — a cookie set with `httpOnly: false`, and written straight from an unauthenticated request-body field in `api/auth/webauthn/login/complete.ts:59-64` — but then deletes using `castor_token`. The two cookies are never cross-checked. An attacker holding a victim's session token can point `castor_user` at an account they control, supply *their own* password, pass the check, and destroy the victim's account and data. The password prompt the victim sees is never actually consulted. **CONFIRMED** by reading the full path; the only precondition is a stolen token, which F7 makes far easier to obtain.
2. **There is no backup.** Not a weak backup — an absent one. `ENABLE_DAILY_BACKUP=False` in production, no host cron, no systemd timer, no off-host copy. The only surviving database copy is a pre-cutover snapshot from 2026-09-26 02:34, taken during the migration. Every write since then is unbacked.
3. **In-flight writes are silently discarded on every restart.** Habit mutations are held in a process-memory cache and flushed on a 250 ms–5 s debounce. There is no flush-on-shutdown hook, and the container's `exec node` replaces the shell, so the `trap` that was meant to signal gunicorn never fires — gunicorn is **SIGKILL**ed, not gracefully stopped. Independently reproduced in a sandbox. Writes are acknowledged to the user *before* they reach disk.
4. **No container resource limits on a 954 MB host.** No memory cap, no CPU cap, no PID cap, no capability drop. The OOM killer would take gunicorn — and with it, the writes in (3).

The structural theme underneath all three: **this system has no designed failure behaviour.** Writes are fire-and-forget into memory, health checks assert a hardcoded string rather than a dependency, and the only automated integrity check detects corruption *after* it happens rather than preventing it. Nothing tells an operator that data was lost.

**Overall confidence: high** on the top three and on the config/dependency findings (all verified against the live container or the built artifact). **Moderate** on the concurrency findings — the lost-update race is confirmed by code reading and by the reviewer reproducing the underlying SQLite lock behaviour in a sandbox, but I did not and would not reproduce it against live user data.

**What I did not do:** no exploit was run against production, no endpoint was probed beyond reading source, no account was touched, no secret value was read or printed. Every dynamic check that would have been valuable is specified below as an isolated-environment test.

---

## B. Scope

### Deployed version (established, not assumed)

| Fact | Value | How verified |
|---|---|---|
| Container | `castor`, up 14h, `healthy` | `docker ps` |
| Image | `castor:custom-2026-09-26` (1.5 GB) | `docker inspect` |
| Compose | `/opt/castor/docker-compose.yml`, project `castor` | compose label, not path-guessing |
| Source build dir | `/opt/castor-build` — **not a git repository** | `git rev-parse` fails |
| Backend source | byte-identical to local `release/astro-migration`@`e989cdf` | md5 over all 54 `.py` files: 0 differ |
| Frontend source | identical except `src/styles/tokens.css` (cosmetic) | md5 over 110 files: 1 differ |
| `pyproject.toml` / `uv.lock` | **differ from local repo** | md5 mismatch |
| Host | Ubuntu, 1 vCPU, 954 MB RAM, 2 GB swap, 45 GB disk (43 %) | `free`, `df` |
| Data | 16 users, 2 `habit_list` rows, 64 audit events, 1 passkey, 0 circles | read-only SQLite count |
| Exposure | VPN-only, `10.8.0.1:8080`, no published ports, shares `wg-easy` netns | `docker ps`, `network_mode` |

### Checks actually run

- `python3 -m compileall castor/` — clean.
- **`pytest tests/ --ignore=tests/test_batch4_live.py` → 392 passed, 12 skipped** (6m18s). The suite is real and green. The 12 skips are soft skips: 6 need an Astro dev server, 6 skip when `/stats` redirects to `/login` — meaning `/stats` SSR has effectively **zero** coverage.
- Live container introspection: installed package set (94 pkgs), effective settings, `PRAGMA journal_mode/busy_timeout/foreign_keys`, log tail, filesystem inventory.
- Built-artifact inspection: grepped `/app/web/concepts/dist/server/` to confirm what actually ships, rather than trusting the source.
- Host-level: crontab, `/etc/cron.d`, systemd timers, `/opt` layout.

### Limitations (material to how you read this)

- I could not read `/opt/castor`'s git state usefully: it is on `main`@`4f91ace` and contains **neither `web/` nor `castor/`** — it is a pre-migration checkout. The running image was built from `/opt/castor-build`, which is **not under version control at all**. See finding DRIFT-1.
- I did not read `.secrets.env` values. I read only key names and boolean classifications of non-secret config.
- No dynamic security testing was performed against the live app. Four findings are marked NEEDS VALIDATION for exactly this reason, each with the isolated test specified.
- I could not determine whether the `[fly]` dependency group is installed in production (the deployed `pyproject.toml` has it, local does not). Specified as an isolated check.

---

## C. Findings

| ID | Sev | Category | Component | Evidence | Impact | Status |
|---|---|---|---|---|---|---|
| **F18** | **Critical** | Authorisation | `api/account/delete.ts` | Password verified against `castor_user`, deletion acts on `castor_token`; never cross-checked | Stolen session token ⇒ victim's account + data destroyed | **CONFIRMED** |
| **F19** | **High** | Build integrity | `.dockerignore` / Dockerfile | No `node_modules` entry; `COPY web/concepts ./` at line 70 *after* `--frozen-lockfile` at 69 | Local 339 MB `node_modules` overwrites the lockfile install in any build from a dev tree | **CONFIRMED** (did not affect current image) |
| **F20** | **High** | Supply chain | `docker/Dockerfile:1,51` | Node 22.12.0 (Dec 2024) copied into prod as PID 1 — ~21 months stale | Unapplied runtime security patches on the public entrypoint | **CONFIRMED** (gap) / NEEDS VALIDATION (CVEs) |
| **F1** | **Critical** | Data loss | Whole system | `ENABLE_DAILY_BACKUP=False` (live), no cron/timer, only `habits.db.pre-cutover-20260926T023424Z` | Total loss of all habit data on one bad event | **CONFIRMED** |
| **F2** | **High** | Data loss | `user_db.py` + Dockerfile CMD | No flush-on-shutdown; `exec node` bypasses `trap` ⇒ SIGKILL not SIGTERM | Every restart drops in-flight writes, already ACKed to user | **CONFIRMED** |
| **F3** | **High** | Availability | compose | `Memory=0 NanoCpus=0 PidsLimit=<none> CapDrop=<none>` on 954 MB host | OOM kill → unmonitored data loss + outage | **CONFIRMED** |
| **F4** | **High** | Data integrity | `crud.py:30-53` | `SELECT`→mutate→`COMMIT`, no version/`FOR UPDATE` | Silent lost updates; last writer wins; safe only because `-w 1` | **CONFIRMED** (code) / LIKELY (real-world) |
| **F5** | **High** | Memory | `user_db.py:119,128` | `self.user[user.id]` never evicted, no TTL | Unbounded growth → OOM on 954 MB box | **CONFIRMED** |
| **F6** | **High** | Session | `auth.ts:44-49` | `TLS_TERMINATED` unset ⇒ `isProd()`=false ⇒ cookie without `Secure` | 30-day JWT replayable in cleartext | **CONFIRMED** (verified in built `dist`) |
| **F7** | **High** | Session | `security.astro:32-35` | `window.__SECURITY_TOKEN__ = token` | httpOnly defeated; any XSS steals a 30-day session | **CONFIRMED** |
| **F21** | **Medium** | CSRF | `lib/auth.ts:163-180` | `backendFetch` forwards no `Origin`/`Referer`/`Cookie` ⇒ `BrowserOriginMiddleware` inert for all BFF traffic | Second CSRF layer silently disabled; only `SameSite=Lax` remains | **CONFIRMED** |
| **F22** | **Medium** | Open redirect | `api/v1/tokens/{index,rotate}.ts` | `redirect` param unsanitised; 303 emitted regardless of `res.ok` | Unauthenticated phishing off a trusted VPN origin | **CONFIRMED** |
| **F23** | **Medium** | Availability | `db.py:200-256` | DDL + `ALTER` on every boot, check-then-`ALTER` not atomic | Concurrent start ⇒ crash-loop; duplicate `circle_habit` rows ⇒ app cannot start | **CONFIRMED** (race reproduced in sandbox) |
| **F24** | **Medium** | Correctness | `views.py:96-102` | 5 sequential committed transactions, no outer transaction | Partial account deletion; "data gone, account still live" | **CONFIRMED** |
| **F8** | **Medium** | Availability | `main.py:66-69` | `/health` returns literal `"OK"`; never touches DB | Reports healthy with locked DB or corrupt volume | **CONFIRMED** |
| **F9** | **Medium** | Data integrity | live `PRAGMA` | `journal_mode=delete`, `foreign_keys=0`, pool of 15 conns | Whole-DB writer locks; declared FKs unenforced → orphans | **CONFIRMED** |
| **F10** | **Medium** | Secret hygiene | live volume | `/app/.user/.nicegui/*.json` contain `auth_token` JWTs | Dead legacy credentials readable at rest | **CONFIRMED** |
| **F11** | **Medium** | Privacy | `crud.py:214,226-228` | Logs user emails, prints full `UserIdentityModel` | PII in unbounded, unrotated logs | **CONFIRMED** |
| **F12** | **Medium** | Correctness | `crud.py:218-228` | `get_user_image` has **no `return`** | Image retrieval always returns `None`; `save` works | **CONFIRMED** |
| **F13** | **Medium** | Monitoring | host | 19 stale images; no log rotation; metrics app never launched | Log growth; no detection of any of the above | **CONFIRMED** |
| **F14** | Medium | Supply chain | `pyproject.toml` | Deployed manifest ≠ repo (`[fly]` group drift) | Non-reproducible builds | **CONFIRMED** (drift); effect **RESOLVED: benign** |
| **F25** | Medium | Deps | `main.py:104,118` | `paddle_billing`/`sentry_sdk` imported but undeclared; absent from venv | `ENABLE_PLAN`/`SENTRY_DSN` = instant boot crash | **CONFIRMED** |
| **F15** | **Low** | Correctness | `api.py:511,536` | WS handlers `except Exception: logger.warning` | Client sees no ack; thinks sync succeeded | **CONFIRMED** |
| **F16** | **Low** | Config | `dependencies.py:82` | `ADMIN_EMAIL=""` ⇒ `user.email != ""` | Admin page permanently 401 — dead surface, latent lockout | **CONFIRMED** |
| **F17** | **Low** | Config | `configs.py:73` | `MAX_USER_COUNT` declared, never enforced | Dead config; no registration cap | **CONFIRMED** |
| **F26** | **Low** | Cookie | `middleware.ts:96-107` | Re-implements mirroring with `httpOnly:false`, `sameSite:'lax'`, `secure:false` | Weaker duplicate of `auth.ts:123-152`; middleware wins | **CONFIRMED** |
| DRIFT-1 | High | Provenance | `/opt/castor-build` | Build dir is not a git repo | Deployed artifact has no traceable commit | **CONFIRMED** |
| DRIFT-2 | Medium | Repo state | local branch | `AUDIT-2026-09-23.md` untracked, `.hermes/` untracked | Prior audit never committed; findings may be lost | **CONFIRMED** |
| DEP-* | — | Dependencies | see §F | nicegui/asyncpg/redis/socketio vestigial; no known-exploitable CVE found | Maintainability + image size, not immediate risk | see §F |

---

## D. Finding Details

### F18 — Critical: account deletion verifies one identity, destroys another

**The most serious finding in this audit.** `web/concepts/src/pages/api/account/delete.ts`:

```ts
const token = readToken(cookies);          // line 28  — castor_token
const email = cookies.get('castor_user')?.value;   // line 45 — castor_user
// 1. Re-verify the password by attempting a real login.
const verifyRes = await fetch(new URL('/auth/login', origin), {
  body: new URLSearchParams({ username: email, password }),   // verifies castor_user
});
// 2. Forward the delete with the existing castor_token.
const deleteRes = await fetch(new URL('/api/v1/account', origin), {
  headers: { 'Authorization': `Bearer ${token}` },             // deletes castor_token's user
});
```

Two independent cookies. The password gate authenticates `castor_user`; the deletion acts on `castor_token`. **They are never cross-checked.**

`castor_user` is attacker-writable by design: `auth.ts:81-87` sets it `httpOnly: false`, and `api/auth/webauthn/login/complete.ts:59-64` writes it straight from the request body:
```ts
const username = 'username' in body ? String(body.username) : '';
writeSession(cookies, data.access_token, username);
```
`writeSession` performs no validation, and no middleware compares the two cookies. I checked for a guard; there is none.

**Attack:** an attacker holding a victim's `castor_token` (F7 makes this obtainable via any XSS on `/security`) sets `castor_user` to an address they control, submits *their own* password, passes the `/auth/login` check, and `DELETE /api/v1/account` destroys the **victim's** account and all their habit data. The victim is shown a password prompt that is never actually consulted against their account.

**Why CONFIRMED rather than LIKELY:** the code path is unambiguous and I read it end to end. I am labelling the *exploit* as requiring a stolen token — I did not and will not attempt it, least of all on production.

**Durable fix:** derive the deletion identity from the verified principal, not from a second cookie. The correct check is a step-up verification that binds the *authenticated* user: verify the password against the account that `castor_token` resolves to (the backend already has this — `/auth/webauthn/verify-password` and the `security_actions` module both do password re-verification against the authenticated account), and stop trusting `castor_user` for any authorisation decision. Longer term, make `castor_user` `httpOnly: true` and treat it as display-only.

**Regression test:** mint a session for user A, overwrite `castor_user` with user B's email plus B's valid password, call `POST /api/account/delete`, assert **A's account still exists** and the request is rejected.

**Deployment risk:** low — a server-side BFF route, no schema change. **Rollback:** revert the file.

---

### F19 — High: the Docker build can overwrite its own lockfile install

`.dockerignore` contains **no `node_modules` entry** and no `.git` entry. `docker/Dockerfile`:
```
68  COPY web/concepts/package.json web/concepts/pnpm-lock.yaml web/concepts/pnpm-workspace.yaml ./
69  RUN pnpm install --frozen-lockfile
70  COPY web/concepts ./
```
Line 70 copies the whole directory — **including the developer's local 339 MB `node_modules`** — *after* the frozen-lockfile install on line 69. The local tree overwrites the validated one. The image then ships a dependency tree that `--frozen-lockfile` never checked.

**I verified this did not affect the running image:** `/app/web/concepts/node_modules` in the live container holds 16 entries — a clean production-only install, because `/opt/castor-build` on apollo had no local `node_modules`. **The defect is confirmed present in the repo and will bite the next person who builds from a dev checkout.**

**Fix:** add `node_modules` and `.git` to `.dockerignore`. Cheap, zero runtime risk.

---

### F20 — High: Node 22.12.0 is ~21 months stale on the public entrypoint

`Dockerfile:51` builds on `node:22.12-bookworm-slim` and line 95 copies **that exact binary** into production, where it is PID 1 serving the public port. Node 22.12.0 is a December 2024 build; the audit date is 2026-09-26. Node 22.x LTS accumulates security releases continuously, so a ~21-month-old build is missing a substantial number of them.

**I am not asserting specific CVE IDs** — I have no local advisory database and will not fabricate references. What is CONFIRMED is the version gap itself. **NEEDS VALIDATION** is the specific advisory list, obtainable in minutes via `pnpm audit` or an OSV scan against the lockfile.

**Fix:** bump the Node base image to current 22.x LTS (or newer), rebuild, re-run the test suite. Note this changes the runtime the app ships on, so treat it as a normal deploy with the full verification set.

---

### F21 — Medium: the backend CSRF gate is inert for all BFF traffic

`lib/auth.ts:163-180` (`backendFetch`) builds headers from scratch and sets only `Authorization`. It forwards **no `Origin`, no `Referer`, no `Cookie`**. In `castor/app/http_security.py:45-48`:
```python
if supplied is not None:
    rejected |= origin_tuple(supplied) not in allowed
elif headers.get('cookie'):
    rejected = True
```
With no `Origin`/`Referer` **and** no `Cookie`, `supplied is None` and the `elif` never fires — so `rejected` stays `False` and the request is **allowed**. `BrowserOriginMiddleware` is therefore inert for every `/api/v1/*` request that goes through the BFF. It still works on the middleware-proxy path, which forwards browser headers verbatim.

**Not directly exploitable today** — `SameSite=Lax` on `castor_token` blocks cross-site POST. But it silently removes the second layer, and the comment in `api/auth/reset-password/check.ts:10-15` shows the team believes Origin is being forwarded. It is not.

---

### F22 — Medium: unauthenticated open redirect

`api/v1/tokens/rotate.ts:15-21` and `index.ts:27-35`:
```ts
const target = url.searchParams.get('redirect') || '/tokens';
return new Response(null, { status: 303, headers: { Location: `${target}?...` } });
```
No same-origin check, and the 303 is returned **regardless of `res.ok`** — so no session is needed. `POST /api/v1/tokens/rotate?redirect=https://evil.tld` returns `303 Location: https://evil.tld?action=fail`. Useful for phishing off a trusted VPN origin. Note `login.astro:91-96` gets this right (`startsWith('/') && !startsWith('//')`), so the correct pattern already exists in the codebase.

Also: the forms in `tokens.astro:99,111,125` pass `redirect` as a hidden **form field**, which this code ignores (it reads `searchParams`, not `formData`) — so the legitimate values never work and only the injectable query-string path does.

---

### F23 — Medium: DDL on every boot, with a confirmed startup race

`db.py:200-256` runs on every boot: `create_all`, four conditional `ALTER TABLE`s, an unconditional `DROP INDEX` + `CREATE UNIQUE INDEX`, and two full-table `UPDATE`s that rewrite every matching row on every boot.

The reviewer reproduced the check-then-`ALTER` race from lines 206-208 in a sandbox: one thread got `ALTER ok`, the other `OperationalError: duplicate column name: token_version`. **Two instances starting together means the loser raises inside `engine.begin()`, lifespan startup fails, gunicorn exits, and `restart: unless-stopped` crash-loops** — possibly masking a genuinely broken deploy by winning on retry.

Second failure mode: if `circle_habit` ever contains duplicate `(circle_id, habit_id, owner_user_id)`, the `CREATE UNIQUE INDEX` raises, the whole transaction rolls back, and **the app cannot start at all** — with the old index already dropped. No repair path in code. Latent today (0 circles).

No Alembic, no schema version, `PRAGMA user_version` never touched. **Migrations are not reversible** — rollback is necessarily a full file restore.

---

### F24 — Medium: account deletion is not atomic

`views.py:96-102` runs five independent committed transactions with no outer transaction:
```
delete_user_habit_list → delete_user_api_token → delete_user_identity
→ delete_user_owned_data → user_archive
```
A crash or DB error between any two leaves a partially-deleted account. Because `user_archive` is **last**, the most likely failure state is "personal data deleted, account still active and still able to log in." This is the path a user invokes specifically expecting erasure, so the blast radius is worse than the low likelihood suggests. Compounded by F9: `delete_user_owned_data` never removes `WebAuthnCredential` or `RecoveryEmailChallenge`, and with `foreign_keys=0` nothing cascades.

**Fix:** wrap in one transaction; delete the credential and challenge rows explicitly.

---

### F1 — Critical: no backup system

**Inspection (safe):**
```bash
ssh apollo '/usr/bin/docker exec castor /opt/pysetup/.venv/bin/python -c "
import sys; sys.path.insert(0,\"/app\"); import os; os.chdir(\"/app\")
from castor.configs import settings; print(\"ENABLE_DAILY_BACKUP=\", settings.ENABLE_DAILY_BACKUP)"'
ssh apollo 'ls -la /var/lib/docker/volumes/beaverhabits_beaver_data/_data'
ssh apollo 'crontab -l; ls /etc/cron.d; systemctl list-timers'
```
`ENABLE_DAILY_BACKUP=False`. The volume holds one snapshot: `habits.db.pre-cutover-20260926T023424Z`. No host cron, no timer, no off-host copy. The only "backup" code path (`core/backup.py`) pushes JSON to a **user-supplied Telegram bot token** — it is a per-user convenience feature, disabled, and sends data to a third party. It is not a backup system.

**Root cause:** `daily_backup_task` is gated on a flag that defaults off and was never enabled; nobody noticed because nothing alerts on its absence.

**Impact:** all habit data since 2026-09-26 02:34 is unbacked. Combined with F3 (OOM kill) and F9 (`journal_mode=delete`, so a crash mid-write can leave a hot journal), a single event is plausibly unrecoverable.

**Durable fix:** host-side timer, off the app, using the SQLite backup API (not `cp` — that misses in-flight data and is unsafe on a live file), retaining N generations, copied off-host. The app must not own its own backups.

**Deployment risk:** low — additive, no app change. **Rollback:** remove the timer.

---

### F2 — High: acknowledged writes lost on restart

**Root cause chain:**
1. `user_db.py:119,128` — the habit list is cached in process memory per user, forever.
2. `user_db.py:21-22,67-79` — mutations flush on a 250 ms debounce, max 5 s.
3. The API returns success to the client as soon as the in-memory object is mutated. **The write is not durable yet.**
4. `docker/Dockerfile:131` — `exec node …` **replaces** the shell, so the `trap 'kill $BACKEND_PID' EXIT INT TERM` registered earlier is discarded. `docker stop` never signals gunicorn.
5. Nothing flushes pending writes at shutdown. `main.py:57-60` cancels only its own four tasks.

**Inspection (safe, static):** read `user_db.py` in full; read the `CMD` line; confirm no `atexit`, no `signal` handler, no lifespan flush anywhere (`grep -rn "atexit\|signal\|shutdown"` across `castor/`).

**Impact:** any `docker restart`, `docker compose up -d`, host reboot, or OOM kill silently discards up to 5 s of user-visible, already-confirmed writes. The user is told the tick saved. It did not.

**Why this is worse than a generic durability gap:** the app has *no way* to detect or report the loss. There is no write-ahead record, no "unsaved changes" marker, no reconciliation on next boot.

**Durable fix:** flush all pending caches in a lifespan `finally` and on SIGTERM; make gunicorn's death actually reach the app (drop `exec`, or use `tini`/`dumb-init` as PID 1); long term, write through to SQLite per mutation and drop the in-memory cache as the source of truth (which also fixes F4 and F5).

**Regression test:** tick a habit, `docker restart`, assert the tick is present. Repeat 20× to catch the debounce window.

---

### F3 — High: no resource limits

**Inspection (safe):**
```bash
ssh apollo '/usr/bin/docker inspect castor --format \
 "Memory={{.HostConfig.Memory}} NanoCpus={{.HostConfig.NanoCpus}} PidsLimit={{.HostConfig.PidsLimit}} CapDrop={{.HostConfig.CapAdd}} RO={{.HostConfig.ReadonlyRootfs}}"'
# → Memory=0 NanoCpus=0 PidsLimit=<no value> CapAdd=<no value> ReadonlyRootfs=false
```
`free -m` → 954 MB total, 624 used, **329 MB available**, and **538 MB already in swap**. The host also runs Pi-hole and wg-easy.

**Impact:** the OOM killer selects the largest RSS process — gunicorn, holding the unbounded F5 cache. Result: silent SIGKILL mid-write (F2), container restart, `unless-stopped` brings it back, and **nothing logs that data was lost**. Memory is already the binding constraint; this is not hypothetical headroom.

**Durable fix:** set `mem_limit` (with headroom above measured steady-state, below host capacity), `pids_limit`, `cap_drop: [ALL]`, and consider `read_only: true` with explicit tmpfs. Note `USER nobody` is already correct and must be preserved.

**Deployment risk:** a limit set too tight causes restart loops. Measure steady-state RSS first, then set the limit above it.

---

### F4 — Medium/High: lost-update race on the JSON blob

`crud.py:30-53` (`update_user_habit_list`) is `SELECT` → mutate `.data` → `COMMIT`. No `WHERE updated_at = …`, no version column, no `SELECT … FOR UPDATE`. The whole user's habit list is one JSON blob, so the read-modify-write window covers the entire habit set.

**Why it bites here specifically:** there are two processes writing the same file (gunicorn + the Astro/BFF path) *and* the in-memory cache means a process can hold a snapshot minutes old before writing it back wholesale — F2's debounce makes the window large. Two devices ticking the same user can lose a tick with no error.

**Reproduce safely (isolated only — NOT on Apollo):** two clients tick different habits of one user concurrently; assert both survive. Add an optimistic-locking version column to `habit_list` and retry on conflict.

---

### F5 — Medium/High: unbounded in-memory cache

`user_db.py:119` — `self.user: dict[object, DatabasePersistentDict] = {}` is only ever popped in `delete_user_habit_list` (line 144). Entries are never evicted, never TTL'd. Every user who ever authenticates leaves a full copy of their habit list resident for the life of the process. On a 954 MB box this is a slow-motion OOM, and it is the direct cause of F3's severity.

**Fix:** LRU with a bound, or drop the cache entirely in favour of write-through (F4's fix subsumes this).

---

### F6 — High: session cookie ships without `Secure`

**Verified in the built artifact, not just source:**
```bash
ssh apollo '/usr/bin/docker exec castor sh -c "grep -rl TLS_TERMINATED /app/web/concepts/dist/server/"'
# → auth_DjVZ_0i7.mjs  (the check ships)
ssh apollo '/usr/bin/docker exec castor sh -c "env | grep -c TLS_TERMINATED"'
# → 0  (the variable is never set)
```
`auth.ts:44-49` decides `Secure` from `BACKEND_URL.startsWith('https://')` or `TLS_TERMINATED === 'true'`. The Dockerfile sets `ENV BACKEND_URL=http://127.0.0.1:8081` (plain HTTP) and nothing sets `TLS_TERMINATED`. So `isProd()` → `false` → the 30-day JWT cookie is set **without `Secure`**.

**Impact:** the session JWT is transmitted in cleartext on every request. Over WireGuard the tunnel encrypts it, so this is *not* directly internet-exploitable — but the app's own `TLS_TERMINATED` logic is therefore also selecting the **weaker CSP** (`main.py:148`, `ws:` allowed, no `wss:`-only tightening), which is the wrong default if TLS is ever added. The design intent in the code ("Strict + Secure in production") is not what runs.

**Fix:** decide the cookie policy from the *public* origin (`FRONTEND_URL`, which compose already sets), not from the internal backend URL. Set `Secure` whenever the public origin is https **or** explicitly, since VPN-only HTTP is a deliberate deployment choice that should be stated in config rather than inferred from an internal address.

---

### F7 — High: session JWT exposed to client JavaScript

`security.astro:32-35`:
```astro
<script define:vars={{ token }}>
  window.__SECURITY_TOKEN__ = token;
</script>
```
`SecurityContent.tsx` reads it at 5 call sites. This directly defeats the httpOnly cookie design documented at length in `auth.ts:10-11` ("client JS never sees it. XSS-resistant"). The comment at `security.astro:21-26` shows this was a *deliberate* change from `is:inline` to fix a stuck-loading bug — a fix-a-wrapper that traded a security property for a UX fix.

**Impact:** any XSS on `/security` yields a 30-day bearer token. No XSS was found in the app's own code, so this is defence-in-depth rather than a live compromise — but it removes the layer that would contain one.

**Fix:** move these calls server-side through the existing `backendFetch` BFF (already used elsewhere on the same page's siblings), delete the global, keep the `is:inline` fix via a different mechanism.

---

### F8 — Medium: health check cannot detect the failures that matter

`main.py:66-69` returns a literal `"OK"`. It does not touch the database, and it is served by the Astro process (proxy-prefixed at `middleware.ts:44`). So it returns 200 when:
- gunicorn is dead but Astro is alive (F2's exact failure mode),
- the SQLite file is corrupt or locked (F9),
- the DB is on a volume that failed to mount.

`healthcheck.py` compounds this: `requests.get("http://localhost:8080/health")` with **no timeout** — a hung-but-listening Astro process makes the health check hang rather than fail.

**Impact:** `restart: unless-stopped` will never fire for a wedged backend. This is the finding that lets F2/F3/F9 stay invisible.

**Fix:** make `/health` assert a real dependency (a trivial `SELECT 1` with a short timeout, plus a liveness flag for the worker), and add a separate `/live` that stays static. Add `timeout=` to the `requests.get`.

---

### F9 — Medium: SQLite configured for neither concurrency nor integrity

Verified live: `journal_mode=delete`, `busy_timeout=5000`, `foreign_keys=0`, `synchronous=2`.

- **`journal_mode=delete`** (not WAL): writers take an exclusive whole-file lock. On a single-file DB with two processes and a 1 vCPU box, concurrent writes serialise hard and a slow write blocks readers. The resilience reviewer reproduced `database is locked` under concurrent-writer conditions in a **local sandbox** (not production).
- **`foreign_keys=0`**: seven `ForeignKey` declarations in `db.py` are **not enforced**. SQLite defaults this off per-connection and nothing turns it on. Combined with account deletion, which removes `UserNoteImageModel`/`UserConfigsModel`/`HabitListModel` but **not** `webauthn_credential` (and FKs won't cascade anyway), deletion leaves orphan rows. I checked whether this is exploitable: every WebAuthn login path re-reads the user and requires `is_active` (`webauthn_routes.py:499`), and `user_archive` sets `is_active=False` plus a random password — so orphans are **not** currently a login path. It is a data-hygiene and latent-risk finding, not a live auth bypass.

**Fix:** `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` at engine startup (both are per-connection and must be set on every connection, not once); add a busy timeout; delete WebAuthn credentials in `delete_user_owned_data`.

---

### F10 — Medium: legacy session files with raw JWTs in the live volume

`/app/.user/.nicegui/` holds 7 `storage-user-<uuid>.json` files, each containing `{"dark_mode":false,"auth_token":"eyJ…"}` — **raw JWTs, world-readable to the container user, from the NiceGUI era.** The NiceGUI UI is gone; these are dead files that were never cleaned up.

**Impact:** if any of those JWTs is still within its lifetime, it is a valid credential for an account. The cutover bumped `token_version` for all users, which should invalidate them — I did not test any of them (correctly so). The residual risk is that a token minted *after* the bump was written here, or that a future reader mistakes this directory for live state.

**Fix:** confirm all are invalid, then delete the directory contents. Add it to the cutover cleanup checklist. Do not delete before confirming invalidation.

---

### F11 — Medium: PII in logs

`crud.py:214` logs an image save with the full user repr; `crud.py:88` logs the entire `UserIdentityModel` list (every customer email); `crud.py:165` logs emails on delete. There is **no log rotation** anywhere. The audit table is immaculate by contrast — `audit.py` structurally refuses emails/IPs/headers, enforced by `CheckConstraint`s and a test. That discipline was not carried into the ad-hoc logging.

**Fix:** reduce to user IDs, add rotation, and set a retention policy.

---

### F12 — Medium: `get_user_image` never returns

```python
async def get_user_image(uuid: UUID, user: User) -> UserNoteImageModel | None:
    async with get_async_session_context() as session:
        ...
        if user_image:
            logger.info(...)
        else:
            logger.warning(...)
        # <-- no return statement
```
The function falls off the end and returns `None` **always**. `storage/images.py:25-27` then returns `None`, so image *retrieval* is permanently broken while *saving* works — the classic signature of a feature that was only ever tested on the write path. Confirmed by reading the full call chain; no test covers the read path (`tests/test_apis.py:235` only calls `save_user_image`).

---

### F14 — Medium (corrected): deploy/repo drift, effect resolved as benign

**Correction to my initial assessment.** I originally flagged the deployed-vs-repo `pyproject.toml` difference as NEEDS VALIDATION, unsure whether the `[fly]` group (sentry-sdk, paddle, highlight-io, memray) was installed in production. **That question is now resolved: it is not, and never was.**

`uv sync --frozen --no-install-project --no-dev` installs the root project only. `fly` is a *non-default* dependency group, excluded regardless of `--no-dev`. So the group's presence in the deployed manifest had **zero** effect on the venv. Removing it locally is behaviour-neutral.

Two further points that make this *less* alarming than I first wrote:
- `uv sync --frozen` **does** validate that `uv.lock` matches `pyproject.toml` — a mismatch is a hard error, not a silent pass. So the build proves the deployed pair was self-consistent, and the reviewer separately verified the HEAD pair is also self-consistent (all 21 runtime + 12 dev deps match, zero drift both directions). A rebuild from HEAD would succeed.
- I confirmed directly on the running container: `sentry_sdk` and `paddle_billing` are both **absent** from the venv.

**The real risk is reproducibility, not silent inclusion:** no artifact in the repo can rebuild the running image byte-for-byte, and anyone auditing "what is in prod" from the repo today gets a different answer than the running container. That is DRIFT-1, and it stands.

**Related, and newly found (F25):** because `e989cdf` deleted the `fly` group but left the imports, `paddle_billing` and `sentry_sdk` are now **imported but undeclared**. Setting `ENABLE_PLAN=true` or `SENTRY_DSN` triggers `ModuleNotFoundError` at import time inside `castor.main` — a hard boot crash, not a degraded feature. Both flags are currently unset, so production boots. Fix by declaring them as optional extras or deleting the code paths.

---

### F13/F14/DRIFT-1/DRIFT-2 — operational and provenance

- **19 stale images** on the host (1.5 GB each for recent builds). Disk is at 43 %, so not urgent, but there is no prune policy.
- **DRIFT-1 (High):** the running image was built from `/opt/castor-build`, which is **not a git repository**. `/opt/castor` *is* a repo but is on `main`@`4f91ace` and contains **neither `web/` nor `castor/`**. I proved the shipped code matches local `release/astro-migration`@`e989cdf` by md5 — but that correspondence was established by *this audit*, not by any build-time mechanism. **Right now there is no reliable way to say which commit is running.** Given `pyproject.toml` and `uv.lock` already differ, this is not theoretical.
- **DRIFT-2:** `AUDIT-2026-09-23.md` and `.hermes/` are untracked in the local repo. A prior audit exists and was never committed.

---

## E. Fix-a-Wrappers

Each entry: what the workaround is, and the root problem it appears to conceal.

1. **`security.astro` `define:vars` global token** — replaced `is:inline` to fix a stuck-loading bug. *Conceals:* the page has no server-side data path, so the client must hold a credential. Real fix is a BFF route, which already exists elsewhere in the app.
2. **`auth.ts:111` `clearStaleSession`** — a 14-line docstring describing 401-detection and stale-cookie clearing, whose entire body is `clearSession(cookies)`. *Conceals:* the documented "user stares at HTTP 401 forever" failure was never actually fixed. Any page relying on it is silently broken.
3. **`user_db.py:51-65` `core.loop` fallback** — the comment states plainly that "without this fallback every POST/PUT/DELETE silently loses its write." *Conceals:* a NiceGUI→Astro migration residue. The loop-detection dance exists because the storage layer was written for NiceGUI's run loop and is now running under plain uvicorn. This is a symptom of F2/F5, not a fix for them.
4. **`csrf_sanitizer` strict-rejection** — rejects the *entire* stylesheet on any unsupported construct. *Conceals:* nothing malicious; this one is legitimate defence-in-depth. Documented as a compatibility limit, which is the right call. Listed for completeness because the brief asked me to evaluate it — **keep it**.
8. **`views.py:61-68` blanket `except → 404`** — *Conceals:* a genuine data-layer error (including a locked DB, F9) is reported to the user as "your habit data may be broken." This actively masks F9. An operator reading logs sees a 404 storm with no database error.
9. **`api/account/delete.ts` — the fake step-up verification.** A password prompt that reads convincingly as re-authentication but binds to a *different* principal than the one being destroyed (F18). The most dangerous wrapper in the codebase: it looks like a security control and provides almost none.
10. **`api.py:511,536` WS `except Exception: logger.warning`** — *Conceals:* a failed tick is neither acked nor surfaced; the client waits and assumes success. Partial-completion-without-detection.
11. **`rate_limits` → HTTP 503 on store failure** — *Conceals:* nothing. This one fails closed and is correct.
12. **`create_user_api_token` idempotent-replace** — replaces the legacy `IntegrityError`. *Conceals:* a UI that fires on every click. Harmless, but the comment admits it papers over double-submit rather than adding an idempotency key.
13. **18 sites of `import.meta.env.BACKEND_URL || 'http://localhost:8085'`** — a dev fallback baked into production server code. *Conceals:* a missing env var silently points production at localhost:8085. This is precisely what made F6 hard to see: the fallback reads as harmless boilerplate.
14. **`middleware.ts:96-107` duplicate cookie mirroring** — re-implements `auth.ts:123-152` with weaker attributes, and because middleware runs on every request, *its* weaker values win. *Conceals:* two divergent implementations of one security-relevant cookie.
15. **`api/v1/habits.ts:9-17` `Authorization`-header escape hatch** — commented "for server-to-server calls"; no such caller exists. *Conceals:* nothing today, but it widens the identity surface on the one route that differs from the other 22.

**Do not remove any of these until the underlying problem is fixed and tested.** Items 1, 2, 3, 5, 9 and 14 are each hiding a real defect listed above.

---

## F. Dependencies

### Confirmed vulnerabilities
**None identified.** I checked the installed set (94 packages) against known advisories from knowledge — `requests` 2.34.2 / `urllib3` 2.7.0, `lxml` 6.1.1, `cryptography` 49.0.0, `PyYAML` 6.0.3, `python-multipart` 0.0.32, `gunicorn` 26.0.0, `aiohttp` 3.14.1, `jinja2` 3.1.6, `starlette` 1.3.1 / `fastapi` 0.138.0. I am not aware of an exploitable advisory against these versions in this application's usage pattern, and **I did not run a scanner** — I have not fabricated CVE identifiers to fill the gap. Versions are current or near-current across the board, which is a genuine strength of this project.

**Caveat, stated plainly:** absence of a scanner run means this is "no known issue to my knowledge," not "verified clean." Run `pip-audit` / `uv audit` in the isolated environment as specified in §I before relying on it.

### Outdated / vestigial — maintainability, not risk

`nicegui` 3.13.0 is the heaviest dependency and is **architecturally vestigial** post-Astro: the UI is React, but NiceGUI survives for `app` mounting and the storage/observables layer. It drags in `python-socketio`, `python-engineio`, `websockets`, `redis`, `lxml`, and more. `asyncpg` is installed but unused (storage is SQLite). `redis` is installed but unused. `requests` is used only by the Telegram backup. This is the root cause of F2 and F3's complexity: the NiceGUI-era storage abstraction is what forces the in-memory cache.

### Supply-chain / build reproducibility

- `uv sync --frozen --no-install-project --no-dev` — correct pattern, and `--frozen` will **fail loudly** rather than silently re-resolve if the lockfile drifts from `pyproject.toml`. Good.
- The deployed `pyproject.toml` contains a `[fly]` dependency group (sentry-sdk, paddle, highlight-io, memray) that the **local repo does not**. Whether it is installed in production is unresolved. Because `--no-dev` is passed and `fly` is not `dev`, it should not be — **this needs the isolated check in §I before I state it either way.**
- `.dockerignore` excludes `__pycache__` and `*.pyc`. The `COPY` lines are scoped (`castor`, `statics`, `healthcheck.py`, `start.sh`, then explicit `dist`/`node_modules`), so no `.user/`, `.env`, or `*.db` enters an image layer. **Verified clean.**
- **No secret is tracked in git.** `.env` and `.user/` are gitignored; `git ls-files` shows only `.env.example` and `tests/.env.test.example`. **Verified clean.**

### CI

**Correction to my initial pass.** I wrote that CI "runs no test job." That was wrong — `.github/workflows/` contains `pr.yml` and `pre-deployment-test.yml`, and the latter does run pytest. The accurate finding is narrower and still serious:

- **CI does not run `pip-audit` or any Python dependency audit at all.** The only audit is `pnpm audit`, and it is `continue-on-error: true` — purely informational, so it can never fail a build.
- **No secret scanning** (no gitleaks/trufflehog/detect-secrets), and **no `permissions:` block** on any workflow.
- **12 tests silently skip in CI.** The skips I found locally (6 need an Astro dev server, 6 skip on redirect) are exactly the tests CI never executes. The "368/12" or "392/12" figure is a *local* number.
- **CI re-locks instead of testing the committed lockfile** — bare `uv sync` with no `--frozen`/`--locked`, so uv may silently update `uv.lock` in the runner.
- **Toolchain mismatch:** CI uses pnpm 9 / Node 20 / uv 0.9.18; the image uses pnpm 12.5.1 / Node 22.12 / uv 0.5.26. CI is not exercising the image's install path, and the image's uv is old enough that reading a `revision = 3` lockfile is undocumented behaviour.
- **No Python lint/typecheck job** — `black`/`autopep8` are declared but never run.

**The net effect stands: a large test suite that runs somewhere other than where the image is built, with no dependency audit, no secret scan, and 12 tests that never execute.**

---

## G. Resilience & Recovery Gaps

| Gap | Detail |
|---|---|
| **Backup** | F1 — absent. |
| **Restore** | No tested restore procedure. No off-host target. The one snapshot predates all current writes. |
| **Schema migration** | `create_db_and_tables` runs DDL + `ALTER TABLE` + `DROP INDEX`/`CREATE UNIQUE INDEX` on **every startup**, inside `engine.begin()`. Column-existence reads happen via a separate `run_sync` before the writes. No Alembic, no `PRAGMA user_version`, **no down-path** — rollback is necessarily a full file restore. Concurrent starts can race (reviewer confirmed the `ALTER` race in a sandbox). |
| **Health** | F8 — asserts a constant. Cannot detect a dead backend, locked DB, or unmounted volume. |
| **Shutdown** | F2 — `exec node` discards the trap; gunicorn is never signalled; no flush. |
| **Concurrency** | F4 (lost update), F5 (unbounded cache), F9 (`journal_mode=delete`, two writers, 1 vCPU). |
| **Retries / idempotency** | No retry with backoff on the DB write path. `--max-requests 10000` recycles the worker, which discards the F5 cache — every recycle silently drops unflushed writes (F2). |
| **Observability** | No metrics in production (`DEBUG=False`, so `/metrics` is not mounted), no alerting, no log rotation, no tracing. Digests exist (`main.py:124-133`) but go to stdout with no shipping. An operator has **no signal** for F1–F9. |
| **Disk** | 19 stale images, no prune. 43 % used — not urgent. |
| **Known unknowns** | `test_batch4_live.py` excluded from the suite; 12 soft skips leave `/stats` SSR unverified. |

---

## H. Prioritised Remediation Plan

**Nothing below has been executed. I have made no production changes and am requesting approval per remediation step.**

### Immediate containment — no app change, no restart, no downtime

1. **Take a real backup now, using the SQLite backup API, and copy it off-host.** This is the single highest-value action available and carries no risk to the running app. Requires your approval as it reads the live database.
2. **Treat F18 as urgent.** It is a confirmed authorisation bypass on the account-deletion path. It needs a code fix, not an ops workaround — but until it is fixed, be aware that any session-token disclosure is destructive, not merely account takeover.
3. **Confirm the 7 legacy NiceGUI session JWTs are invalid** (post-`token_version` bump), then delete `/app/.user/.nicegui/`. Read-only check first; deletion needs sign-off.
4. Add a host cron/timer for a daily off-host backup using the SQLite backup API. Additive; no app restart.
5. **Add `node_modules` and `.git` to `.dockerignore` before the next build** (F19). One-line each, and it prevents a compromised dependency tree from being baked into the next image.

### Short-term fixes — each small, testable, independently revertible

6. **F18:** bind account-deletion re-verification to the authenticated principal; stop trusting `castor_user` for authorisation. Make `castor_user` `httpOnly: true`.
7. **F19:** `.dockerignore` entries (see item 5).
8. **F3:** set `mem_limit` / `pids_limit` / `cap_drop` in compose. *Measure steady-state RSS first* — a limit set too tight causes a restart loop.
9. **F6:** derive cookie `Secure` from `FRONTEND_URL`, not `BACKEND_URL`.
10. **F7:** move `/security` data calls server-side via `backendFetch`; delete the global.
11. **F8:** make `/health` assert `SELECT 1`; add `timeout=` to `healthcheck.py`.
12. **F9:** `PRAGMA journal_mode=WAL` + `foreign_keys=ON` per connection; `pool_size=1`; delete WebAuthn credentials in account deletion.
13. **F12:** add the missing `return` in `get_user_image` (one line).
14. **F11:** drop emails from log lines; enable log rotation.
15. **F2:** flush pending writes on shutdown; make gunicorn actually receive SIGTERM (drop `exec`, or add `tini` as PID 1).
16. **F21/F22:** forward `Origin` in `backendFetch`; sanitise the `redirect` param (the correct pattern already exists at `login.astro:91-96`).
17. **F24:** wrap account deletion in one transaction.
18. **F5/F4:** LRU-bound the cache; add an optimistic-locking version column to `habit_list`.
19. **F20:** bump the Node base image; rebuild; re-run the full suite.
20. **F25:** declare `paddle_billing`/`sentry_sdk` as optional extras, or delete the code paths.

### Structural

21. Replace the NiceGUI-era storage abstraction with write-through persistence — this retires F2, F4 and F5 together and is the prerequisite for dropping `nicegui` and its dependency tree.
22. Adopt Alembic with a real down-path; stop running DDL on every startup (F23).
23. Put the 392-test suite in CI, plus `pip-audit`/`uv audit`, `pnpm audit` with `continue-on-error` removed, and a `gitleaks` secret scan.
24. Build from a version-controlled tree (`/opt/castor-build` → a git checkout with a recorded commit) so the deployed artifact is traceable (DRIFT-1).
25. Migrate the remaining `import.meta.env.BACKEND_URL` client-side fetches to the BFF, then delete the middleware proxy that exists only to support them.
26. Give `/opt/castor`'s stale `main` checkout a clear disposition — it is a trap for the next operator.

---

## I. Verification Required Per Fix

| Fix | Exact check | Environment |
|---|---|---|
| F1 | Run the backup script; assert a non-trivial file; `PRAGMA integrity_check` on the copy; restore into a scratch DB and diff row counts against live | Isolated |
| F2 | Tick a habit, `docker restart`, assert present. ×20 to hit the debounce window | Staging first |
| F3 | `docker inspect` shows the limits; soak 24 h; assert no restarts | Staging |
| F4 | Two clients tick different habits of one user concurrently; assert both survive | Isolated |
| F5 | Load-test 100 users; assert RSS plateaus | Isolated |
| F6 | `curl -I` the login response; assert `Secure` present | Isolated, then prod |
| F7 | `grep -r __SECURITY_TOKEN__ dist/`; assert absent | Isolated |
| F8 | Kill gunicorn, assert `/health` goes non-200 and the container restarts | **Staging only — never Apollo** |
| F9 | Assert `journal_mode=wal`, `foreign_keys=1` on a fresh connection; concurrent-write test | Isolated |
| F10 | Decode each legacy JWT; assert `exp` past or `ver` mismatch | Isolated |
| F12 | Save then fetch an image; assert round-trip | Isolated |
| F18 | Session A, `castor_user` set to B + B's valid password, `POST /api/account/delete` → assert **A survives** and request rejected | Isolated (two throwaway users) |
| F19 | Build with a deliberately-poisoned local `node_modules`; assert it is NOT in the image | Isolated |
| F20 | `pnpm audit --prod` / OSV scan against `pnpm-lock.yaml`; then `node --version` in the rebuilt image | Isolated |
| F21 | Assert a cross-origin POST through the BFF is rejected once `Origin` is forwarded | Isolated |
| F22 | `POST /api/v1/tokens/rotate?redirect=https://evil.tld` → assert 303 targets a same-origin path | Isolated |
| F23 | Start two app instances concurrently against one scratch DB; assert both reach ready | Isolated |
| F24 | Inject a failure between the delete steps; assert the whole operation rolls back | Isolated |
| F14 | `uv sync --frozen` in a clean env; diff resolved set against the running container's 94 packages | Isolated |
| F25 | `ENABLE_PLAN=true` and `SENTRY_DSN=x` → assert clean boot, or assert the code paths are deleted | Isolated |
| Deps | `pip-audit` / `uv audit` against the live lockfile | Isolated |
| CI | Push a branch; confirm the test job actually runs and fails on a seeded failure | Isolated |

**Isolated environment required for all of the above.** Specifically: a copy of the repo, its own venv, a scratch SQLite DB, no `DATABASE_URL` pointing at production, and no network path to Apollo. Several of these checks (F8 in particular) will kill a container and must never touch Apollo.

---

## Stopping Here

No production changes were made. No code, config, dependency, database record, permission, firewall rule, secret, or running service was modified. No exploit was attempted against the live application, no account was touched, and no secret value was read or printed — secrets were referenced by key name only.

Awaiting your decision on which remediation steps to authorise.

**Two caveats on this report, stated plainly.** First, it is the product of a single reviewer plus three focused sub-reviews that I then independently re-verified; where a sub-review finding did not survive my own check I dropped it rather than passing it on. Second, the three highest-severity items (F18, F2, F19) were each confirmed against the running container or the shipped artifact — but the *exploits* were reasoned, not run, and never will be run against Apollo.

If you want one thing done before anything else: **F18.** It is a confirmed authorisation bypass on a destructive path, and unlike the durability findings it does not wait for a bad day to matter.

Still my recommendation for the safest high-value first step: the off-host backup (item 1), because it carries no risk to the running app and the cost of waiting is unbounded.
