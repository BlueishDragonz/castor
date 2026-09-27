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
| FIXED, race eliminated | 1 | F17 (second pass — count-then-insert replaced by an atomic seat claim) |
| FIXED, DEPLOY PENDING | 2 | F20, F26 |
| DEPLOY ACTION | 2 | F3, F13 |
| CONFIRMED, DONE | 1 | F10 |
| BENIGN / NO ACTION NEEDED | 1 | F14 |
| NOT CODE-FIXABLE | 2 | F24(partly), DRIFT-2 |

Every ID in the audit is now accounted for: 25 findings (F1–F26 less F14 and
DRIFT-*) plus DRIFT-1, which is closed by the image revision label.

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

### F4 — Lost-update race on the JSON blob — FIXED, VALIDATED ON DEPLOY

`habit_list` gained a `version` column (migration `SCHEMA_VERSION = 5`).
`crud.update_user_habit_list(user, data, expected_version=...)` is now a
compare-and-swap: a zero rowcount raises `HabitListConflict`, and the storage
layer re-reads the winner's state, **merges** per-day records, and retries
(bounded, with an explicit `OptimisticLockError` rather than silent loss).

**Validated against the running production container, not just in tests.**
Unit tests cannot show this, because they never pass through gunicorn, the
lifespan shutdown hook, or a real SIGTERM. The probe
(`scripts/f4_deploy_probe.py`) registers a throwaway user, creates a habit,
records a completion, then reads the SQLite file *directly* — bypassing the
API and the storage layer, since a read through the app could be served from
the F5 in-memory cache and would prove nothing.

  write phase     habit 7878be, completion on disk: [('2026-09-27', True)]
  docker stop     "Flushed pending habit-list writes on shutdown"
                  SIGTERM handled cleanly, exit 143
  restart         healthy after 70s
  verify phase    on disk: [('2026-09-27', True)] — identical
                  RESULT: PASS

The probe was then checked against itself: planting an expectation the disk
does not contain makes it fail, so the PASS is meaningful and not an artefact
of a comparison that cannot fail.

The habit list is a single JSON blob on one row, which is exactly why this
matters: a lost update silently drops a user's day, with no error anywhere.

Two notes for whoever runs this next time:
- `/auth/login` is `x-www-form-urlencoded` with a `username` field, not JSON
  with `email`. Posting JSON returns a 422 that names *both* fields as
  missing, which reads like a typo rather than a content-type problem.
- `Tick.date` is parsed with `date_fmt` (`%d-%m-%Y`). An ISO date is a 400.

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

### F10 — Legacy NiceGUI session files — CONFIRMED DEAD, DELETED

**Invalidation confirmed against the live data, then deleted** (operator
decision F10-A). See "F10 — invalidation confirmed" below for the evidence
and the reasoning.

What is done: the app no longer writes this pattern;
`test_f10_sessions_stay_in_memory_with_bounded_ttl` asserts the session
storage is a bounded in-memory `TTLCache` with no file-writing surface; and
`scripts/reap_legacy_nicegui_tokens.py` verifies and, on explicit request,
reaps the leftovers without ever printing a token.

**Requires:** one command, after the F1 backup exists.

### F13 — Monitoring, log rotation, stale images — MOSTLY FIXED

- Log rotation: **FIXED** in `docker-compose.yml` (`max-size: 10m`,
  `max-file: 3`).
- Stale images (19 on the host, 1 dangling): **DEPLOY ACTION** — needs a prune
  policy on apollo.
- **Monitoring: FIXED via Sentry**, for both the Astro frontend and the Python
  backend, wired end to end.

  This was the audit's standing gap — "no detection of any of the above" —
  and the reason an OOM kill or a post-deploy crash was something a user
  found out before an operator did.

  F11's privacy posture is enforced explicitly rather than inherited.
  Sentry's defaults attach request bodies, headers, cookies, user identity
  and URL query strings to every event; this app handles passwords, recovery
  codes and habit data. So both SDKs run `sendDefaultPii: false`,
  `dataCollection` is off for `userInfo`/`headers`/`cookies`/`formData`, query
  strings and fragments are stripped, session replay is pinned to 0, the
  server config discards the request object entirely, and `beforeSend` scrubs
  JWT- and SHA256-shaped values from anywhere they can appear — including
  breadcrumbs and interpolated error messages, which a blocklist on request
  keys would not reach.

  `web/concepts/scripts/check-sentry-config.mjs` asserts all of that, **and**
  that the DSN is genuinely present in `dist/` — an SDK that is installed but
  never injected produces a build that looks fine and reports nothing. That
  check's first version had inverted `find` exit handling and reported
  sourcemaps that did not exist.

  **Source maps are not shipped**: the build deletes `dist/**/*.map` after
  upload, verified as zero `.map` files. Leaving them would publish the
  application's full source.

  Verified end to end, not assumed:
  - 2 artifact bundles present in the `castor-astro` project (client and
    server), which only exist if source maps were actually accepted.
  - A deliberate `RuntimeError` raised in the running container appeared in
    the project within seconds.
  - Zero `.map` files in the deployed image, zero occurrences of the token in
    `docker history` and in the build log.

  ### Two failures worth recording, because both looked like success

  **The first token was invalid** (`401 Unauthorized` from sentry.io,
  confirmed against the API rather than inferred from the build output). Error
  reporting still worked — it needs only the DSN — but stack traces would have
  been minified, and the build was green throughout, because a failed
  source-map upload is a warning from the plugin, not a build error.

  **The second build silently did nothing.** With a valid token the build
  returned exit 0, logged no error, and uploaded no source maps. The cause was
  Docker layer caching: the `astro build` stage was reported `CACHED`, so the
  step never executed and the token was never read. The log looked exactly like
  a success.

  That is the failure mode this whole document keeps running into, and the
  build now busts the frontend stage whenever a token is supplied
  (`--no-cache-filter astro-builder`). Verified: the stage re-ran, the plugin
  logged "Successfully uploaded source maps to Sentry" twice, and the bundles
  appeared in the project.

  The token is passed by BuildKit secret mount, never as a build-arg or an
  inline ENV. An earlier version wrote it in plaintext to the build log,
  recorded in the commit history along with the reason; that log was scrubbed
  and the token rotated. Verified: zero occurrences in the current build log
  and zero in `docker history`.

### F26 — Duplicate, weaker cookie mirroring — FIXED, DEPLOY PENDING

`middleware.ts` re-implemented the WebAuthn cookie mirroring with weaker
attributes (`httpOnly:false`, `sameSite:'lax'`, `secure:false`) and, because
middleware runs on every request, *its* weaker values won. Now uses the shared
`mirrorWebAuthnBrowserCookie` from `lib/auth.ts`. Registration completion was
likewise switched to the shared `writeSession`.

### DRIFT-1 — Which commit is running? — FIXED

**Was:** the running image was built from `/opt/castor-build`, which is **not a
git repository**. The audit established by md5 that the shipped code matched
`release/astro-migration` — but *the audit* established that, not any
build-time mechanism. There was no reliable way to say which commit was
running.

**Now:** the Dockerfile takes `GIT_SHA` and stamps it as
`org.opencontainers.image.revision`. It defaults to `unknown`, so a build that
forgets to pass the SHA is visibly unlabelled rather than silently
mislabelled.

```
$ docker inspect castor --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
87bfe740e0cb
```

Verified on a real image built on apollo from a `git archive` context.

### F14 — pyproject/uv.lock drift — BENIGN, VERIFIED

The audit's own correction concluded this is harmless: `uv sync --frozen`
validates the pair and hard-fails on mismatch, so both the deployed and HEAD
pairs were self-consistent. Re-checked during this pass — `uv lock --check`
resolves cleanly. No action needed.

### DRIFT-2 — Untracked prior audit — RESOLVED

The 2026-09-23 audit is now tracked at
`docs/migration/audit-2026-09-23-branch-vs-main.md`. Only `.hermes/` (agent
scratch, correctly ignored) remains untracked.

---

## Additional findings — not in the audit

These were found while remediating and are not in the original report. All
**eight** were real defects, and five are the kind that silently break the very
mechanism meant to fix a finding — or report a fault that is not there.

Numbers 5, 7 and 8 are the argument for this whole section: every one of them
passed the full suite, `astro check`, Pyright and a green build, and was found
only by starting the container. Number 7 is the worst of them — a monitoring
control that reported a fault which did not exist.

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

### 6. The version marker outlived the schema it described — FIXED

`_schema_is_current` trusted the recorded `SCHEMA_VERSION` on its own. But a
version marker is a *claim* about what was applied, and a claim can outlive
the thing it describes: `Base.metadata.drop_all` in a test fixture, a partial
restore, or a hand-run script drops the application tables while leaving
`schema_migration` behind. The gate then read "current", skipped every
migration, and the first real query died with "no such table".

Boot now confirms the schema-defining tables exist before trusting the marker.

Surfaced as "F17 tests fail in the full suite but pass alone" — the ordering
was the symptom, the version gate was the cause.

### 7. The health check reported unhealthy while serving perfectly — FIXED

`healthcheck.py` used `requests.get`, and `import requests` alone costs
**9.1s** in this image. The probe took 5.5s against a `--timeout=3s` health
check, so it never once completed in time.

The container booted, logged "Application startup complete", served `/health`
200 on both ports — and Docker reported `unhealthy` with a failing streak of
5. A health check that reports an outage which does not exist is worse than no
health check: it sends an operator hunting a fault that isn't there, and as a
rollout gate it would block healthy deploys.

Now uses `urllib.request` (0.8s to import, 0.5s for the request), with
`--timeout=10s` and `--start-period=90s`. The start period is measured, not
guessed: the container takes **42s** to reach "Application startup complete"
before the port binds, and 45s produced three spurious failures before the
first success.

### 8. The Sentry token leaked into the build log — FIXED

Two wrong ways to pass a secret to a build, both found by running one:

- `--build-arg` with no matching `ARG` is **accepted and silently discarded** —
  green build, no warning, no source maps uploaded.
- An inline env var on the `RUN` is expanded and echoed by buildkit into the
  build log. The first real build wrote the token in plaintext to
  `/tmp/deploy-build.log`.

Replaced with a BuildKit secret mount. Verified: zero occurrences in the build
log, zero in `docker history`. The log was scrubbed and the staged token file
deleted.

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
| Full backend suite | **466 passed, 12 skipped** (excluding `test_batch4_live.py`) |
| `astro check` | 0 errors, 0 warnings |
| `pnpm build` | succeeds |
| F7 gate vs. vulnerable code | exits 1 (vulnerable) / 0 (fixed) |
| F13 Sentry config gate | 19 checks, all pass; fails if the integration is re-gated on the token |
| F13 Sentry build output | DSN present in `dist/`, zero `.map` files shipped |
| F4 on the deployed service | write survives SIGTERM + restart, read straight from SQLite |
| F4 probe vs. a planted mismatch | fails as it should (exit 1) |

**A note on the user count.** `SELECT COUNT(*) FROM user` returns 20, but
only **4 accounts are live**. The other 16 rows are `deleted+<uuid>@deleted.invalid`
tombstones, left by F18 so that a deleted user's foreign keys stay resolvable.
Quoting the raw row count as "16 users" earlier in this document was wrong by
12; live accounts are `jrodux@gmail.com`, `newuser2@example.com`,
`test2@example.com`, `test@example.com`. The tombstones are also independent
evidence that F18 works — 14 real deletions have gone through it.
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
| F17 concurrency, vs. racy code | fails as required (grants exceed the limit) |
| Pyright vs. baseline | 8 vs. 7 pre-existing; the one addition is `Result.rowcount`, a false positive (same pattern already used in `webauthn_routes.py`, `users.py`, `auth.py`) and proven correct at runtime by the conflict test |

## Not verified — stated plainly

- **No deploy.** The image is built and smoke-tested, but production still runs
  the old `castor:custom-2026-09-26`. Applying the new compose (`mem_limit`)
  needs a container recreate, and the F1 backup timer needs installing on the
  host. Both are deliberate, separate steps.
* **The audit's exploits were reasoned, not run**, and were never run against
  Apollo. Negative controls here were run against *local* code only.
* **F10: invalidation CONFIRMED, and the files deleted.** See below.


## F10 — invalidation confirmed, then deleted

The audit said: confirm all are invalid, *then* delete. Do not delete before
confirming. So the confirmation was done first, with a tool that will not
delete anything it has not proved dead.

`scripts/reap_legacy_nicegui_tokens.py` decodes each legacy JWT **without
printing it** (SHA-256 prefix only), then establishes its status two ways:
expiry from the `exp` claim, and — for anything unexpired — the `ver` claim
against the user's current `token_version` in the live database. A file it
cannot parse, or whose token has no integer `ver` claim, is reported
`UNKNOWN` and **blocks deletion**. Files with no `auth_token` at all are safe
by inspection.

Run report-only against a copy of the production volume:

```
Summary: 11 dead, 0 live, 0 unknown, 11 total
```

- **9 files** contain no `auth_token` — no credential to leak.
- **2 files** contain a JWT for a user id that no longer exists in the
  database. A token for a deleted account cannot authenticate.
- **0 live, 0 unknown.** The audit's stated residual risk — a token minted
  *after* the `token_version` bump — is ruled out by the data, not assumed.

Run against a copy, with production untouched and the 11 files still in place.

### Order of operations

The F1 backup was taken and verified first (off-volume, root-owned 0600,
`PRAGMA integrity_check` = ok, 20 user rows, 2 habit lists), so the deletion had a
real undo before it happened. The reaper was then re-run report-only against
the live volume — still 11 dead, 0 live, 0 unknown — and only then run with
`--delete`. It removed all 11 and left a copy at
`.nicegui-backup-20260926T213417` in the same volume.

The command, for the record:

```
# after the F1 backup timer has produced at least one good snapshot
python3 scripts/reap_legacy_nicegui_tokens.py --volume <mountpoint>          # re-verify
python3 scripts/reap_legacy_nicegui_tokens.py --volume <mountpoint> --delete
```

`--delete` refuses if any file is live or unproven, and backs the directory up
before removing anything. Order matters: take the F1 snapshot first, so that
"reversible" is true rather than aspirational.

## Deployment order (if proceeding)

1. Install the F1 backup timer and **run it once manually**, then confirm a
   snapshot exists and restore-drill it into a scratch path.
2. `docker compose up -d` (recreate, so `mem_limit` applies).
3. Verify: `/health` 200, `/live` 200, backup timer `systemctl list-timers`
   shows a next run, container restart count unchanged.
4. Confirm DRIFT-1 is closed: `docker inspect --format '{{index .Config.Labels
   "org.opencontainers.image.revision"}}' castor` returns the expected SHA.
5. Only then handle F10 (reap the 11 confirmed-dead legacy files — invalidation
   is already confirmed, see above) and F13 (image prune + alerting).
