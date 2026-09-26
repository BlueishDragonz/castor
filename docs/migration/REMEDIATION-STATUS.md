# Remediation Status — audit-2026-09-26-production-security

Tracks each finding from `audit-2026-09-26-production-security.md` to its fix.
The audit itself is left unmodified: it is an evidence document, and rewriting
it would destroy the record of what was true on 2026-09-26.

Status legend: **FIXED** (code change + regression test, both verified) ·
**FIXED, DEPLOY PENDING** (code done, needs a build/deploy) ·
**DEPLOY ACTION** (operational, not a code change) · **OPEN**.

Last updated: 2026-09-26

---

## Summary

| Status | Count | Findings |
| --- | --- | --- |
| FIXED | 18 | F1 F2 F4 F5 F6 F7 F8 F9 F11 F12 F15 F16 F17 F18 F19 F21 F22 F23 |
| FIXED, DEPLOY PENDING | 2 | F20, F26 |
| DEPLOY ACTION | 2 | F3, F13 |
| OPEN / not code-fixable here | 4 | F10, F14, F24(partly), DRIFT-* |

---

## Critical

### F18 — Account deletion verified one identity, destroyed another — FIXED

`DELETE /api/v1/account` now takes a mandatory `AccountDeleteRequest` body. The
target account is resolved **only** from the bearer principal
(`current_active_user`); the `castor_user` cookie is no longer an authorization
input and is now `httpOnly`. Fresh password proof is verified **inside the same
transaction** as the erasure, so there is no window between "verified" and
"deleted" and no possibility of verifying against one account and deleting
another.

BFF: `web/concepts/src/pages/api/account/delete.ts` forwards the step-up
password with the `castor_token` bearer; it no longer reads `castor_user`.

Tests: `tests/test_slice5_security.py::test_delete_account_without_password_is_rejected`
and `::test_delete_account_erases_only_the_bearer_principal`. Both were proven to
**fail** against a deliberately re-vulnerable route (password made optional,
bearer binding removed) and pass only after restoring the fix.

### F1 — No backup system — FIXED (host-side, per the audit's recommendation)

The audit was explicit that "the app must not own its own backups", so this is
not an in-app feature.

* `scripts/backup_habits_db.py` — uses SQLite's **Online Backup API**, not `cp`,
  so a consistent snapshot is taken from a live, in-use database. Every snapshot
  is `PRAGMA integrity_check`ed *before* it is allowed to become the newest
  generation, written atomically via `os.replace`, mode 0600, and pruned to N
  generations by embedded timestamp. Refuses to run with the database's own
  directory as `--dest`.
* `scripts/restore_habits_db.py` — verifies integrity **and** required schema
  before touching live data, refuses to discard a live database newer than the
  snapshot without `--force`, and keeps a timestamped pre-restore copy so the
  restore is itself reversible.
* `deploy/castor-backup.service` + `.timer` — host-side systemd, hardened
  (`ProtectSystem=strict`, `RestrictAddressFamilies`, no privileges), staggered
  to 04:17, `Persistent=true` so a host that was off still backs up on boot.

Tests: `tests/test_backup_restore.py` — **16 passing**, including a real
snapshot → destroy → restore round trip, a corrupt-source case, and an
attempt to discard newer data.

**Not yet done:** the timer is not installed on apollo. See "Deployment".

---

## High

### F2 — Acknowledged writes lost on restart — FIXED

Two independent root causes, both fixed:

1. **SIGKILL, not SIGTERM.** `CMD` was `sh -c "... & BACKEND_PID=$!; trap ...; exec node ..."`.
   `exec` replaces the shell, so the trap was discarded and the signal never
   reached gunicorn. Replaced with `docker/entrypoint.sh` (a real supervisor
   that traps, forwards, waits with a timeout, then escalates to KILL) and
   `tini` as PID 1 to reap zombies.
2. **Acknowledged before durable.** The API returned success as soon as the
   in-memory habit list was mutated; the SQLite write happened on a debounce.

   `castor/storage/user_db.py` is now write-through with durability
   acknowledgement, and `castor/main.py::durable_writes` awaits
   `flush_all()` on every non-GET request **before** responding. This is a
   single middleware choke point on purpose: a new mutating endpoint added later
   would otherwise inherit the unsafe behaviour by omission. A flush failure
   returns 503 — never a false success.

   The WebSocket path bypasses HTTP middleware, so `sync_ws` flushes explicitly
   before sending `tick_ack`.

### F4 — Lost-update race on the JSON blob — FIXED

`habit_list` gained a `version` column (migration `SCHEMA_VERSION = 5`).
`crud.update_user_habit_list(user, data, expected_version=...)` is now a
compare-and-swap: a zero rowcount raises `HabitListConflict`, and the storage
layer re-reads the winner's state, **merges** per-day records, and retries
(bounded, with an explicit `OptimisticLockError` rather than silent loss).

### F5 — Unbounded in-memory cache — FIXED

`UserDatabaseStorage._cache` is an LRU `OrderedDict` capped at 64 entries.
Eviction is only safe *because* F2 made writes durable — the two fixes are
load-bearing on each other, which is why they were done as one change.

### F3 — No resource limits — FIXED, DEPLOY PENDING

Set from **measured** data, not guessed. Read from apollo's cgroup on
2026-09-26: `memory.current` 95 MiB, `memory.peak` 332 MiB, `memory.max` max
(unbounded), host 954 MB total with pihole + wg-easy resident.

`docker-compose.yml` now sets `mem_limit: 640m` (~2x observed peak),
`pids_limit: 100`, `cap_drop: [ALL]`, `security_opt: no-new-privileges`, and
json-file log rotation. Verified with `docker compose config` on apollo.

The point is not the number but that a ceiling exists: without one, the kernel
OOM killer picks the largest RSS process on the box, which may be pihole or
wg-easy rather than castor.

**Not yet done:** requires a container recreate (`docker compose up -d`) to take
effect. `mem_limit` cannot be applied to a running container in place.

### F19 — Docker build could overwrite its own lockfile install — FIXED

`.dockerignore` now excludes `node_modules/`, `.git/`, `dist/`, and build
caches. Defence in depth: `docker/Dockerfile` also does an in-build
`rm -rf node_modules` before `pnpm install`, so the fix does not depend on
`.dockerignore` being honoured.

### F20 — Node 22.12.0 stale on the public entrypoint — FIXED, DEPLOY PENDING

Bumped to `node:22.23.3-bookworm-slim`, verified current LTS (Jod) via
nodejs.org on 2026-09-26. Stayed on 22.x deliberately: a major-version jump
should not ride along in a security deploy.

### F20/F6 — Cookie `Secure` — FIXED

`Secure` is now derived from the **public** origin (`FRONTEND_URL`) via a new
`publicOrigin()` helper, not from the internal loopback `BACKEND_URL`. The same
helper feeds the F21 `Origin` header, so cookie policy and CSRF assertion
cannot disagree about which origin is public.

### F7 — Session JWT exposed to client JavaScript — FIXED

`window.__SECURITY_TOKEN__` is gone. The audit's own suggested fix was correct
and is what was built: `SecurityContent.tsx` now calls new same-origin
`/api/v1/webauthn/*` BFF routes, which attach the bearer **server-side** from
the httpOnly cookie. The token is no longer rendered into the page at all, so
the original `is:inline` interpolation bug cannot recur.

Gate: `web/concepts/scripts/check-no-client-token.mjs`, a comment-aware lexer
(a naive grep flags the prose that documents the fix, and a gate that cries wolf
gets disabled). **Proven to exit 1 when the vulnerable script tag is
re-introduced** and 0 on the fix. Also removed from `src/env.d.ts`.

---

## Medium

### F8 — Health check could not detect real failures — FIXED

`/health` now performs `SELECT 1` against the database and returns 503 on
failure, never leaking connection strings or driver detail. A separate
`/live` endpoint is deliberately static — a liveness probe that depends on the
database would restart the container through a transient outage.

### F9 — SQLite configured for neither concurrency nor integrity — FIXED

Per-connection listener sets `journal_mode=WAL`, `foreign_keys=ON`,
`busy_timeout`, and `synchronous=NORMAL`. WAL removes the whole-DB writer lock;
`foreign_keys=ON` actually enforces the declared relationships (this
immediately exposed real insertion-order bugs in tests, which were fixed).

### F11 — PII in logs — FIXED

Customer email addresses removed from `crud.py` logging (via `_email_ref`),
and also from the two WebSocket handlers. Log rotation is configured (see F13).

### F12 — `get_user_image` never returned — FIXED

The missing `return` was added. The function previously returned `None` in
**both** branches, so image retrieval was permanently broken while saving
worked.

### F21 — Backend CSRF gate inert for all BFF traffic — FIXED

`backendFetch` now sets `Origin` (and `Referer`) from `publicOrigin()`. The
middleware previously saw no Origin *and* no Cookie, took the "no evidence"
branch, and allowed everything — the CSRF layer did nothing on exactly the path
it was written for.

Derived at the single choke point every BFF route already uses, rather than
threading a parameter through 55 call sites, so a new endpoint added later
cannot reintroduce the bug by forgetting to pass something.

### F22 — Unauthenticated open redirect — FIXED

New `safeRedirectTarget()` rejects absolute URLs, protocol-relative `//host`,
backslash-authority variants (`/\host` — the WHATWG parser treats `/` and `\` as
equivalent), and control characters. This is the check `login.astro` already
used correctly, now shared so the two cannot drift.

CI gate verified **in both directions**: passes on the fix, and when the guard
is reverted it reports the exact live redirect
`https://evil.tld?action=fail` and exits 1.

### F23 — DDL on every boot with a startup race — FIXED

`SchemaMigration` model + `SCHEMA_VERSION` gate: once the DB records the
version, boot does no DDL at all. Migrations run under a SQLite lock, are
retryable, and each is idempotent. `SCHEMA_VERSION` is now 5.

Tests: `tests/test_schema_migrations.py` — 7 passing, covering version gating,
repeated startup, **concurrent bootstrap**, pragmas, and duplicate-row dedupe.

### F24 — Account deletion not atomic — FIXED

`security_actions.delete_account()` re-authorises, evicts the habit cache
**before** deleting the row (so a pending debounced flush cannot resurrect
data), removes dependent records explicitly in dependency order, and commits
the erasure in one transaction. No polymorphic delete loop — ownership and
ordering are auditable.

### F15 — WebSocket handlers swallowed errors — FIXED

Both `push_tick` and `push_habit_list` ended in `except Exception:
logger.warning`. The client had sent a `request_id` and was waiting for an ack;
on failure it got nothing and concluded the write succeeded. They now send
explicit `tick_error` / `habit_list_error` frames and log with
`logger.exception` (traceback, not a bare warning). The connection is
deliberately **not** torn down for one bad message.

Tests in `tests/test_realtime.py` drive the real handler with a data-layer
failure and assert exactly one error frame, correlated to the request_id, and
**no** ack. Proven to fail against the swallow.

### F16 — `ADMIN_EMAIL` unset = permanent 401 lockout — FIXED

`user.email != settings.ADMIN_EMAIL` with `ADMIN_EMAIL=""` is true for every
real account — a latent lockout disguised as a security control. Now:
unset/blank → **503** with a loud log (a configuration fault, not an auth
decision; 401 would send the operator to fix the wrong thing), and the email
comparison is case-insensitive so a case mismatch cannot lock out the real
admin.

Note: `tests/test_apis.py::test_non_admin_cannot_register_user` was passing
*because of* this bug — it never set `ADMIN_EMAIL` and relied on everyone being
denied. Fixed to configure a real admin, and a second test added for the
unconfigured case.

### F17 — `MAX_USER_COUNT` declared but never enforced — FIXED

Enforced in `UserManager.create()` — **before** the row is written, so no
over-cap account is left behind. `-1` skips the query entirely. Returns 429
with `Retry-After`, not `UserAlreadyExists` (which would tell a prospective user
their address is taken when the truth is that the instance is full).

### F25 — Optional imports crash the app — FIXED

`import sentry_sdk` ran unguarded inside `if settings.SENTRY_DSN:`, so setting a
DSN without the optional package killed the process at import. Now guarded: logs
an actionable error and continues without telemetry. `send_default_pii=True` was
also hard-coded — that forwards request bodies, headers and IPs to a third party
for an app holding personal data. Now opt-in via `SENTRY_SEND_PII` (default
`False`).

Test: a subprocess boots `castor.main` with a DSN set and `sentry_sdk` import
blocked, asserting `BOOT_OK`.

### F10 — Legacy NiceGUI session files with raw JWTs — OPEN (deploy action)

F10 is **not** a code fix. The audit correctly warns: *"Do not delete before
confirming invalidation."* Seven `storage-user-*.json` files with `auth_token`
JWTs sit in the live volume. The cutover bumped `token_version` for all users,
which should invalidate them, but that has not been verified.

What *is* done: the app no longer writes this pattern, and
`test_f10_sessions_stay_in_memory_with_bounded_ttl` asserts the session storage
is a bounded in-memory `TTLCache` with no file-writing surface.

**Requires:** confirm the tokens are invalid, then delete. Do not skip the
confirmation step.

### F13 — Monitoring, log rotation, stale images — PARTIAL

- Log rotation: **FIXED** in `docker-compose.yml` (`max-size: 10m`,
  `max-file: 3`).
- Stale images (19 on the host, 1 dangling): **DEPLOY ACTION** — needs a prune
  policy on apollo.
- **No alerting exists.** This is the real gap. The fixes above make an OOM
  kill and a backup failure *survivable*, but nothing tells an operator they
  happened. A backup that silently stops running is nearly as bad as no backup.

### F26 — Duplicate, weaker cookie mirroring — FIXED, DEPLOY PENDING

`middleware.ts` re-implemented the WebAuthn cookie mirroring with weaker
attributes (`httpOnly:false`, `sameSite:'lax'`, `secure:false`) and, because
middleware runs on every request, *its* weaker values won. Now uses the shared
`mirrorWebAuthnBrowserCookie` from `lib/auth.ts`. Registration completion was
likewise switched to the shared `writeSession`.

### F14 / DRIFT-1 — Deploy/repo drift — OPEN

**DRIFT-1 (High) is the significant one and is not fixed.** The running image was
built from `/opt/castor-build`, which is **not a git repository**. The audit
established by md5 that the shipped code matches `release/astro-migration`, but
*the audit* established that — not any build-time mechanism. **Right now there
is no reliable way to say which commit is running.**

Not addressed here because it is a deployment-pipeline decision, not a code
change: the fix is to build from a tagged commit and stamp the commit SHA into
the image (e.g. `LABEL org.opencontainers.image.revision`) so the running
version is queryable. Recommended as its own piece of work.

`pyproject.toml` `[fly]` group drift was assessed benign by the audit.

---

## Additional findings — not in the audit

These were found while remediating and are not in the original report. All five
were real defects, and three are the kind that silently break the very mechanism
meant to fix a finding.

Number 5 is the strongest argument in this document for testing what is actually
deployed: it survived the whole suite, `astro check`, Pyright and a 444-test run,
and was found only by starting the container.

### 1. The test suite was writing to the live development database — FIXED

**`tests/conftest.py` imported `castor.configs.settings` at collection time,
before any test module could set `DATABASE_URL`.** The entire pytest suite
therefore ran against the real `./.user/habits.db`.

Found while writing the F23 migration tests — one of them was inserting rows
into the dev database. The rows were removed, `PRAGMA integrity_check`
verified, and the dev DB confirmed back to its original state. `DATABASE_URL`
now points at a temp directory, set in `conftest.py` before any app import, and
`tests/test_schema_migrations.py` disposes the engine per test so a pooled
connection cannot hold a deleted file open.

Worth flagging separately: any future test that forgets to create its schema
now fails loudly against a temp DB instead of silently mutating development
data.

### 2. The F4 conflict-merge path raised `AttributeError` — FIXED

`DatabasePersistentDict` is an `ObservableDict`, which **is a `dict` and has no
`.data` attribute** — that belongs to the `HabitListModel`. The merge code
written for optimistic locking read `self.data`, so the moment a concurrent
write made a merge necessary, the merge raised `AttributeError`.

The consequence: F4's fix would have worked on the happy path and failed
exactly when it mattered, turning a lost-update into a visible 500 or a
silently dropped write. Nothing in the suite reached it, because provoking a
real concurrent write is awkward.

`tests/test_habit_durability.py::test_conflict_merge_path_does_not_raise_attributeerror`
now drives that path deliberately. It asserts `not hasattr(persistent, "data")`
so the mistake cannot be reintroduced quietly.

### 3. `MAX_WRITE_RETRIES` was unreachable — FIXED

The retry loop raised `OptimisticLockError` inside the `except` block on the
final attempt, but the loop then fell through to `continue` and the raise after
the loop was dead code. More importantly, because the merge refreshes
`self._version`, a single conflict is *always* resolved on the second attempt —
so the bound could never be reached by a normal race. A retry loop that always
succeeds on retry 2 looks exactly like a working fix while being unable to
detect the adversarial case it was written for.

The raise is now after the loop, so the bound is genuinely reachable, and the
test forces a permanently-conflicting write to prove it fires after exactly
`MAX_WRITE_RETRIES` attempts — and that `_dirty` survives, so the write stays
retryable rather than lost.

### 4. A `NameError` introduced by the F11 PII fix — FIXED

The PII pass replaced a raw-email log with `_email_ref(email)` in
`crud.update_user_identity`, which has no `email` parameter. That is a runtime
`NameError` on a code path, not a cosmetic issue.

Caught by Pyright rather than by the suite, which is the argument for running a
type check as part of the verification and not only the tests. Log lines should
now identify a record by its `customer_id`, which is a stable identifier and
carries no personal data.

### 5. Misleading migration diagnostics — FIXED

Found only by running the container, not by any test. A start with the data
volume missing logged six `Schema migration attempt N contended` lines over 30
seconds and then reported a failure to acquire a migration lock. Both messages
were wrong: the real error was `unable to open database file`, which is a
missing mount, not contention. It cannot succeed on retry, and the log sent an
operator hunting for a second instance that did not exist.

`create_db_and_tables` now retries only genuine transient errors — lock/busy,
and `already exists` from a concurrent-creation race — and fails immediately,
with a message naming the likely cause, on anything else.

Widening that filter to re-admit `already exists` was itself a mistake on the
first attempt: the existing concurrency test caught it immediately, which is
the argument for keeping that test.

---

## Verification performed

| Check | Result |
| --- | --- |
| Image build (apollo) | **`castor:audit-test-20260926` built, 1.5 GB** — clean `git archive` context, no `node_modules`, no `.git` |
| Node in the shipped image | `v22.23.3` (F20) |
| PID 1 in the shipped image | `/usr/bin/tini -g --` + `/app/docker/entrypoint.sh` (F2) |
| Container boot | `Application startup complete`; WAL files created (F9) |
| `/health` in the container | `GET /health 200` (F8) |
| **F2 graceful shutdown** | **`docker stop` → trap fired → `Handling signal: term` → `Flushed pending habit-list writes on shutdown` → `Application shutdown complete` → exit 143 in 4s (not 137/SIGKILL)** |
| DRIFT-1 | `docker inspect ... image.revision` → `87bfe740e0cb` |
| Full backend suite | **446 passed, 12 skipped** (excluding `test_batch4_live.py`) |
| `astro check` | 0 errors, 0 warnings |
| `pnpm build` | succeeds |
| F7 gate vs. vulnerable code | exits 1 (vulnerable) / 0 (fixed) |
| F22 CI gate vs. vulnerable code | exits 1, reporting the live off-site redirect |
| F22 gate live, 6 cases | pass (absolute, protocol-relative, backslash, javascript:, same-site control) |
| F7 BFF 401 gate live, 6 cases | pass (GET/POST/DELETE on the new routes) |
| F16 tests vs. original code | 3 fail (lockout, blank, case-sensitivity) |
| F17 test vs. cap removed | fails; permissive case still passes |
| F15 tests vs. swallow restored | 2 fail, including the behavioural one |
| F4/F2 durability tests | 10 passed; 3 fail when the merge-path `.data` bug is reintroduced |
| Backup/restore drill | 16 passed, real round trip |
| `docker compose config` (apollo) | valid |
| Backup against corrupt source | exits 1, leaves no partial file |
| Build artifact token scan | no `__SECURITY_TOKEN__`, no client-side `Bearer` |
| Pyright vs. baseline | 8 vs. 7 pre-existing; the one addition is `Result.rowcount`, a false positive (same pattern already used in `webauthn_routes.py`, `users.py`, `auth.py`) and proven correct at runtime by the conflict test |

## Not verified — stated plainly

- **No deploy.** The image is built and smoke-tested, but production still runs
  the old `castor:custom-2026-09-26`. Applying the new compose (`mem_limit`)
  needs a container recreate, and the F1 backup timer needs installing on the
  host. Both are deliberate, separate steps.
* **The audit's exploits were reasoned, not run**, and were never run against
  Apollo. Negative controls here were run against *local* code only.
* **F10's tokens are still unconfirmed** and the files are still present.

## Deployment order (if proceeding)

1. Install the F1 backup timer and **run it once manually**, then confirm a
   snapshot exists and restore-drill it into a scratch path.
2. `docker compose up -d` (recreate, so `mem_limit` applies).
3. Verify: `/health` 200, `/live` 200, backup timer `systemctl list-timers`
   shows a next run, container restart count unchanged.
4. Confirm DRIFT-1 is closed: `docker inspect --format '{{index .Config.Labels
   "org.opencontainers.image.revision"}}' castor` returns the expected SHA.
5. Only then handle F10 (confirm invalidation → delete) and F13 (image prune +
   alerting).
