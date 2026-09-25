# Castor Migration — Risks & Decisions

> Phase 1 deliverable. Decisions I am **flagging** for your review, not
> making unilaterally. Each section: the question, what we know, the
> trade-offs, my recommendation, and what would change if you disagree.
> See `feature-inventory.md` + `parity-matrix.md` for the evidence.

---

## 1. Identity of "production" and the cutover target

**The risk**: `main` branch (NiceGUI) is live on apollo. `migration-to-shadcn-astro` is **not deployed anywhere** — `git status` shows 46 local commits ahead of `origin/migration-to-shadcn-astro`, no running container, no image tag in `/opt/wg-net/docker-compose.yml`. The two branches share no live deployment. "Migration complete" can only mean "the migration branch is ready to deploy".

**What that means for cutover**:
1. The migration branch becomes the new `main` (or a new branch is cut from it), built into a new `castor:custom-…` image, and the apollo compose file swaps image tag.
2. apollo's `castor:custom-2026-09-14` container stays up during build verification — **rollback = "point compose back at the old tag and `docker compose up -d`"** (verified the apollo skill path works).
3. The 12 failing `test_batch4_live.py` tests are apollo-side live tests; they must pass on the new image before swap.

**Decision flagged**: Do you want me to cut a new `release/astro-migration` branch off the migration branch and tag it `v1.0.0-astro`, or merge `migration-to-shadcn-astro` into `main` first?

---

## 2. Data migration: jrodux's 7 habits + 1 passkey

**What we know** (live DB read 2026-09-23):
- 4 users, 2 habit_list rows, 1 webauthn_credential row.
- `jrodux@gmail.com` has a 2779-byte `habit_list.data` JSON blob with 7 habits and notes spanning 2026-06-18 → 2026-09-28.
- 1 webauthn_credential (USB passkey) attached to jrodux.

**What that means**:
- `DictHabitList` shape (`{habits: [{id, name, records, star, status, period, tags}, ...]}`) is identical on both branches — the same Python class serialises/deserialises.
- New tables (`audit_event`, `recovery_email_challenge`, `circle*`, `user.passkey_offer_dismissed`, `user.recovery_email`) are **additive** — `create_db_and_tables` will create them on first start of the castor image.
- No destructive schema change.

**Risk**: `create_db_and_tables` does `create_all` + `ALTER TABLE` idempotently. If the castor source ever introduced a column with a non-null default that conflicts with existing rows (e.g. `user.passkey_offer_dismissed BOOLEAN NOT NULL DEFAULT 0`), `ALTER TABLE ADD COLUMN … NOT NULL DEFAULT 0` works in modern SQLite. But if a future migration introduces a non-null column without a default, it will fail on first start.

**Decision flagged**: Do you want me to write a one-off `migrate_apollo_db.py` script that:
- Snapshots `/var/lib/docker/volumes/beaver_data/_data/habits.db` to a timestamped backup before the cutover?
- Verifies the JSON shape against a known-good schema before swapping the image?
- Bumps `user.token_version = user.token_version + 1` for every user (forces re-login on the cutover — clean slate, no stale JWTs)?

The trade-off is **forced re-login** for the real user. If you'd prefer a transparent cutover (existing JWTs still work), I won't bump token_version. My recommendation: **bump token_version** — the cutover is a clean break, and a forced re-login avoids confusion about which user the JWT belongs to.

---

## 3. Apollo `WEBAUTHN_RP_ID` mismatch (existing operational bug)

**What we know** (verified via `docker exec beaverhabits env` on apollo):
- `docker-compose.yml` declares `WEBAUTHN_RP_ID=10.8.0.1`.
- The running container reports `WEBAUTHN_RP_ID=localhost` — the env file at `/var/lib/docker/volumes/beaver_data/_data/.secrets.env` (loaded by `configs.py:dotenv.load_dotenv(...)`) overrides it.

**What that means**: Passkey registration has been silently broken for VPN origin since at least 2026-09-14 (castor-beaverhabits skill flag). Existing passkey on jrodux may still log in (browser-dependent: iOS Safari accepts rpId suffix matches, desktop browsers usually reject).

**Decision flagged**: Should the cutover bundle a fix? Three options:
1. **Fix as part of cutover** — set `WEBAUTHN_RP_ID=10.8.0.1` in `.secrets.env` (or remove the override entirely). **Risk**: existing jrodux passkey (registered against `localhost` rpId) will not work after rpId change. jrodux will have to re-register.
2. **Fix separately, after cutover** — keep `.secrets.env` as-is for cutover; ask jrodux to re-register a passkey in a follow-up maintenance window.
3. **Don't fix** — accept that passkey login is broken on apollo; force password + recovery email flow.

My recommendation: **option 1**. Document the re-registration requirement in the cutover announcement. The castor `recovery_email` flow provides a fallback (jrodux has Google recovery via jota.rodrigues.89@gmail.com SMTP).

---

## 4. Python package rename: `beaverhabits/` → `castor/`

**Current state on migration branch**:
- Directory renamed: `beaverhabits/` → `castor/`.
- Imports use `from castor.app.…` consistently.
- `pyproject.toml` still references the package — needs verification.
- **One wart**: `castor/app/auth.py` line 1: `from nicegui import app` (kept on purpose; NiceGUI's per-session storage interface is still used for legacy imports of `app.storage.user`). The migration branch's auth code does NOT call into NiceGUI at runtime; the import is leftover.
- The migration branch has `pyproject.toml` that **still depends on nicegui** (same as `main`). Building the migration branch's Docker image installs nicegui even though Astro doesn't use it.

**Risk**: If we remove `nicegui` from `pyproject.toml` prematurely, any import that still references `from nicegui import app` will fail at import time. The audit shows at least one such import in `castor/app/auth.py`. Need to grep + remove all such imports first.

**Decision flagged**: Should I:
- (a) Leave `nicegui` in `pyproject.toml` for v1 cutover (waste ~50 MB image space, but zero risk)?
- (b) Strip `nicegui` after grep confirms no usage (smaller image; cosmetic)?

My recommendation: **(a) for the v1 cutover**, defer (b) to a Phase 4 commit with its own test pass.

---

## 5. Astro build artefact: where does the compiled JS/HTML live?

**Current state**:
- `docker/Dockerfile` (migration branch) builds the Python side (`castor/`).
- The Astro side (`web/concepts/`) has `package.json` + `pnpm-lock.yaml` but the Dockerfile does not run `pnpm install && pnpm build`.
- The migration branch's `astro.config.mjs` uses `output: 'server'` + `@astrojs/node` standalone — the build artefact is `web/concepts/dist/`.

**Risk**: As written, the migration branch's Docker image does **not contain** the Astro build artefact. There is no path to deploy it.

**Three deployment options** (must pick one before Phase 2):

| Option | Architecture | Pros | Cons |
|---|---|---|---|
| **(A) Monorepo Docker image** | One image: Python + Node + Astro dist. The Python FastAPI app is the entrypoint; Astro is started as a subprocess on port 4321. | One image to manage; one rollback target; matches `main`'s single-container deployment | Image grows by ~150 MB; two processes in one container |
| **(B) Two-image split** | `castor:custom-…` runs Python on 8080; `castor-web:custom-…` runs Astro on 4321. Both share `network_mode: container:wg-easy`. | Clean separation; can scale independently | Two compose services; two image tags to keep in sync; health checks need coordination |
| **(C) Astro as static export + Python serves everything** | Build Astro as a fully static SPA (no SSR), serve files from `/app/web/dist` via Python `StaticFiles` | Simplest image (Python only); no SSR complexity | **Loses server-rendered pages (`/login`, `/forgot-password`, server-side POST handlers, BFF endpoints)** — would require rewriting every page as a client-side fetch |

**Decision flagged**: Option **(A)** is the closest to a drop-in for apollo — single container, single compose change. Option (B) is cleaner long-term. Option (C) is not viable without rewriting half the pages.

My recommendation: **(A) for v1 cutover**, plan a migration to (B) in Phase 4 if and when we want to scale Astro independently.

If you pick (A), I need to amend `docker/Dockerfile` to add:
```dockerfile
# After the Python build steps, install Node and build Astro:
RUN apt-get install -y nodejs npm && npm install -g pnpm
COPY web/concepts/package.json web/concepts/pnpm-lock.yaml ./web/concepts/
RUN cd web/concepts && pnpm install --frozen-lockfile && pnpm build
# Resulting dist/ lives in /app/web/concepts/dist
```

And `start.sh` needs to spawn both gunicorn and `node ./web/concepts/dist/server/entry.mjs`.

---

## 6. Authentication model alignment

**Current state**:
- Live: JWT in `beaver_auth` cookie (set by middleware), `app.storage.user["auth_token"]` (NiceGUI's encrypted per-session dict).
- Migration: JWT in `castor_token` httpOnly cookie (set by Astro), `castor_user` (visible) for UI greeting, `castor_webauthn_browser` for ceremony binding.
- Both paths use the same backend (`POST /auth/login` returns JWT); both must respect `VersionedJWTStrategy` + token_version.

**Cookie lifetime mismatch**:
- Live: `JWT_LIFETIME_SECONDS=2592000` (30 days), no explicit cookie maxAge.
- Migration: cookie `maxAge=60*60*24*7` (7 days), backend JWT still 30 days.

**Risk**: After cutover, a user logging in via the migration Astro UI has a 7-day cookie. If they later use a mobile client that submits the JWT directly, the JWT is still valid for 30 days. Inconsistency is cosmetic — both paths work; mobile clients just have a longer usable window than Astro.

**Decision flagged**: Should I align the cookie `maxAge` with the backend JWT lifetime (30 days) in the Astro `writeSession()` call? My recommendation: **yes** — set cookie `maxAge = JWT_LIFETIME_SECONDS` so logout is the only way to lose the session on the Astro side.

---

## 7. CSRF posture after cutover

**Current state**:
- Backend `BrowserOriginMiddleware` rejects cross-origin browser writes (legacy safe).
- Astro pages are server-rendered; all state-changing requests are same-origin POSTs from `<form>` elements. `Origin` header is set by the browser to the Astro origin, which matches the backend's allowlist.
- BFF endpoints under `web/concepts/src/pages/api/` attach `Authorization: Bearer` from the cookie and forward to backend — `Origin` is the Astro origin, backend's `BrowserOriginMiddleware` accepts.

**Risk**: If a future Astro page uses a `<form action="https://api.example.com/…">` (cross-origin), the backend would reject the write. We should **not** add cross-origin forms without updating `CSRF_ALLOWED_ORIGINS` in `.secrets.env`.

**Decision flagged**: None — current architecture is safe. Document the constraint in `docs/security/baseline.md` (D7 hardening was already flagged in plan).

---

## 8. Live WebSocket sync — who uses it?

**Current state**:
- `/api/v1/sync/ws` is implemented in `castor/routes/api.py` and live on apollo.
- The legacy NiceGUI app uses it indirectly via `HabitListChanged` events fanning out to other browser tabs (Engine.IO).
- The migration Astro app does **not** use it (no client connects to the WebSocket).

**Risk**: If the migration Astro app is the only client, no WebSocket usage. If the user still uses the mobile app (native iOS), the mobile app **does** use the WebSocket (`push_tick` and `push_habit_list` are iOS-app messages). The migration cutover does not break the mobile app — the backend endpoint is unchanged.

**Decision flagged**: None for the cutover. The WebSocket stays; mobile apps continue to work. If/when we drop mobile-app support, we can remove it.

---

## 9. Audit log activation on first cutover

**Current state**:
- Running apollo does **not** have an `audit_event` table (legacy image predates `castor/app/audit.py`).
- The castor `main.py` lifespan creates the table on first start.

**Risk**: On first cutover, `audit.append_audit_event("login", "success", user_id, ip)` will succeed but the table will be empty. Historical login events (jrodux's 2779-byte habit list with months of records) will have **no audit trail**. This is a regression in observability, but only for events that already happened.

**Decision flagged**: None — pre-existing data cannot have a retroactive audit trail. New events from cutover forward will be audited. Document this in the cutover announcement.

---

## 10. Demo account wipe mystery (homelab2 dev DB only)

**Current state** (per `AUDIT-2026-09-23.md` §4):
- The `demo@castor.example.com` user on the homelab2 dev DB is intermittently wiped every 1–3 hours.
- Root cause unconfirmed; `castor/demo_seed.py` startup hook + `/dev/reseed-demo` mitigate by restoring the demo user.
- The watcher at `/tmp/db-watcher.py` was installed 2026-09-22 to catch the next wipe.

**Risk**: This affects only the dev DB on homelab2 — **not the apollo production DB**. The apollo DB does not have the demo user (only jrodux, test, e2e, rotate-probe). No production risk.

**Decision flagged**: None for the cutover. Continue investigating separately; the migration plan already accounts for `castor/demo_seed.py` startup behaviour.

---

## 11. Cutover sequence — recommendation

Given the above, the proposed cutover sequence is:

1. **Pre-cutover**:
   - Tag migration branch as `v1.0.0-astro`; cut `release/astro-migration` branch.
   - Add `castor:custom-2026-09-XX` Docker build that includes the Astro build artefact (option A from §5).
   - Update `docker/Dockerfile` to add Node + pnpm build step.
   - Update `docker-compose.yml` (or compose override) to start both gunicorn + astro server.
   - Fix `WEBAUTHN_RP_ID=10.8.0.1` in `.secrets.env` on apollo (option 1 from §3).
   - Back up `/var/lib/docker/volumes/beaver_data/_data/habits.db` to `/opt/castor-backups/2026-XX-XX-pre-astro/habits.db`.
   - Run `test_batch4_live.py` on apollo against the new image; fix any regressions.
2. **Cutover**:
   - `cd /opt/wg-net && sudo /usr/bin/docker compose up -d` (with new image tag).
   - Watch `docker logs beaverhabits` for boot errors (table creation, ALTER TABLE failures).
   - Verify `/health` returns 200.
   - Verify Astro SSR is reachable (port 4321 inside wg-easy netns).
3. **Post-cutover verification**:
   - jrodux logs in with password → confirms habit list intact.
   - jrodux re-registers passkey (old rpId broken).
   - Mobile clients connect, push_tick works.
   - `/admin` reachable by ADMIN_EMAIL.
   - `audit_event` table growing.
4. **Rollback** (if needed):
   - `cd /opt/wg-net && sudo /usr/bin/docker compose down` (new tag).
   - Edit `image:` back to `castor:custom-2026-09-14`.
   - `cd /opt/wg-net && sudo /usr/bin/docker compose up -d`.
   - Verify jrodux can log in (old rpId restored).
   - Restore DB from backup only if data corruption observed.

**Decision flagged**: Confirm the cutover sequence. The risks above all have a mitigation path; the open questions are §3 (rpId fix), §5 (deployment architecture), §2 (forced re-login).

---

## 12. Phase 2/3 work that must happen before any cutover

If you approve the migration branch as v1-ready, these gaps should be closed **before** claiming cutover. Each is a parity-matrix row marked ❌ or 🟡. Estimated effort:

| Item | Effort | Blocks cutover? |
|---|---|---|
| `/help` page (matrix 4.4) | 1 hour | **Yes** (mobile MoreSheet 404) |
| `/tokens` page (matrix 12.1) | 2 hours | No (medium priority) |
| Habit-display preferences persistence backend (matrix 12.6) | 1 day | No (functional now via cookies) |
| Backend `POST /api/v1/habits/import` (matrix 6.3) | 1 day | No (page surfaces honest 404) |
| Stats date-range picker (matrix 3.2) | 4 hours | No |
| CSV export (matrix 6.2) | 4 hours | No (legacy never had it) |
| Last-key passkey removal UX (matrix 5.3) | 2 hours | No |
| Change-password UX (matrix 5.4) | 2 hours | No |
| Sort-by-Name/Category menu (matrix 2.10, 12.7) | 2 hours | No |
| Custom CSS persistence backend (matrix 4.2) | 4 hours | No |
| Long-press notes-textarea on grid cells (matrix 2.13) | 1 hour | No |

**Minimum viable cutover set**: just `/help` (the visible 404). Everything else can ship in subsequent slices.

---

## 13. Things I am NOT proposing without further approval

These are decisions you specifically asked me to flag, not silently make:

- **Removing the legacy `beaverhabits/` NiceGUI code from the castor image entirely** — currently still imported in `castor/app/auth.py`. Touching this is Phase 4 decoupling, not v1 cutover.
- **Removing Paddle, Google One Tap, `chip_sets_page.py`** — intentionally not migrated; can be deleted as part of Phase 4 cleanup with their own ADRs.
- **Switching `castor_token` cookie `maxAge` to 30 days** (cookie/JWT lifetime alignment) — §6.
- **Bumping `user.token_version` for all users during cutover** — §2.
- **Touching the apollo compose file's `WEBAUTHN_RP_ID` setting** — §3.

---

## 14. What's still missing from Phase 1

The instructions asked for behaviour-focused tests against the legacy app for important flows without tests. I did not run those — the castor test suite already covers auth, webauthn, sensitive actions, recovery, and storage. Untested legacy flows I should validate before declaring "complete":

- Drag-drop reorder (`/gui/order`) — no test in repo.
- Calendar heatmap rendering (53-week view) — no test in repo.
- Per-habit completion status chips — no test.
- Telegram backup happy path + failure paths — no test.
- Notes-image upload (`/assets`) — no test.

**Recommendation**: add a `tests/legacy_acceptance.py` that probes each legacy route via the live apollo container (using the same pattern as `test_batch4_live.py`) before claiming the legacy behaviour is the reference for migration parity. Estimated: 1 day. Not blocking Phase 2/3 if you accept the risk of "untested-on-legacy" status.

---

## 15. References

- `architecture-current.md` — file inventory of both systems
- `feature-inventory.md` — page-by-page feature list with handlers
- `parity-matrix.md` — 50-row parity matrix with evidence
- `AUDIT-2026-09-23.md` — pre-existing audit
- `ASTRO_MIGRATION_GAP_REPORT.md` — pre-existing gap report
- `web/concepts/docs/plan/migration-plan.md` — slice ordering (mostly accurate; diverges from current code state)
- `web/concepts/docs/architecture/00-overview.md` — STALE (predates current code state)
- Apollo skills: `apollo-beaverhabits`, `apollo-pihole`, `apollo-wg-easy`
