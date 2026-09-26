# Castor migration — progress + next-session handoff

> One-stop doc for resuming the Astro+shadcn migration of the
> NiceGUI/Quasar habit tracker. Updated 2026-09-26 — CUTOVER DONE,
> repo alignment (slice 30) DONE.

## Status: CUTOVER COMPLETE — Astro app live in production

- **Production now runs `castor:custom-2026-09-26`** (image from branch
  HEAD `a867213`, includes slice-28 circles BFF fix), container
  `castor`, compose `/opt/castor/docker-compose.yml` (project `castor`),
  volume `beaverhabits_beaver_data`, VPN URL http://10.8.0.1:8080.
- Legacy `beaverhabits` container removed; stale orphan volumes
  (`beaver_data`, `castor_beaver_data`) deleted (user-approved sunset).
- Data preserved: 15 users, 2 habit lists, 1 webauthn credential,
  audit history. token_version bumped for all (one forced re-login).
- Post-cutover E2E verified on prod URL: login → habit create → tick →
  SQLite record → restart persistence → logout revocation → probe
  account deleted via the app's own path. Evidence:
  `slice-reports/slice-29-cutover.md`.
- Rollback route intact: `docker-compose.yml.legacy-20260926` +
  `castor:custom-2026-09-14` image + pre-cutover DB backup
  (`habits.db.pre-cutover-20260926T023424Z`). Keep ≥1 week.
- `astro check`: 0 errors / 0 warnings / 58 hints (108 files).
- `pytest tests/ --ignore=tests/test_batch4_live.py`: **392 passed**
  + 12 skipped** (run at cutover T-1h, re-confirmed at slice 30).

### Post-cutover repo alignment (slice 30) — COMPLETE

All five audit decisions executed; see
`slice-reports/slice-30-repo-alignment.md`. Commits `f7848ac` (D2),
`5bcd6fe` (D5), `4af7f3c` (D1), `577aaf4` (D3). D4 was apollo-side
hygiene, no commit.

- D1: `ENABLE_PLAN` cluster (Paddle, `routes/astro.py`, `statics/astro`
  submodule, sitemaps) removed. `robots.txt` moved to
  `web/concepts/public/` and now serves 200 — it 404'd before.
  **Ships on the next image build, not yet in production.**
- D2: migration record now at `docs/migration/` (was `Desktop/migration/`).
- D3: CI triggers on `push` to main, not just pull requests.
- D4: 14 dead images deleted from apollo; rollback set retained.
- D5: both audit files filed under `docs/migration/`, `.hermes/` ignored.

**The migration is complete.** What remains is security/ops remediation
from the production audit, not migration work: F18 (Critical, auth
bypass on account deletion), an off-host backup, and a real-device
login to clear the passkey rpId question.

### Cutover-day deployment-reality corrections (IMPORTANT for future ops)

- The live compose was `/opt/castor/docker-compose.yml` (project
  `castor`), NOT `/opt/wg-net/...` — the wg-net beaverhabits entry was a
  dead relic. Check `docker inspect <ctr> --format {{index
  .Config.Labels "com.docker.compose.project.config_files"}}` before
  trusting any compose path.
- The live volume was `beaverhabits_beaver_data`, NOT external
  `beaver_data` (that was a stale Sep-16 orphan with 4 old users;
  caught by user-count divergence during the dry-run; deleted in
  sunset).
- Legacy env still had `WEBAUTHN_RP_ID=localhost`; now `10.8.0.1`.

### Post-cutover watch items (24h)

- Health streak (currently 0 failing; compose healthcheck 10s).
- First real logins from VPN devices (passkey users may need one
  re-registration due to rpId change localhost → 10.8.0.1).
- ~~SMTP env for circles invite email still unset~~ RESOLVED
  2026-09-26: production volume `.secrets.env` already carries valid
  Gmail app-password credentials; empty SMTP_HOST routes through the
  preserved smtp.gmail.com:465 SSL path. Verified with a real login
  + two test sends (plaintext + HTML, the exact `send_email` code
  path circles invites use) from the production container. Circles
  invite emails are fully operational; no action needed unless
  migrating away from Gmail (then set SMTP_HOST/PORT/USE_TLS/FROM).
- Slice-28 (runbook hardening) landed: every cutover-day backup /
  token_version-bump / rollback-restore command in the slice-19
  runbook was rewritten (no sqlite3 CLI exists in any of the three
  images; Connection.rowcount crash-before-commit bug; heredoc
  form for ssh safety) and executed verbatim on throwaway apollo
  containers. Bonus fix: the circles share-your-habit wizard POST
  had no BFF route (would 404 in production; dev Vite proxy masked
  it) — fixed + verified; `audit_bff_coverage.py` now clean.
- Remaining before cutover: pick date/tag, SMTP env on apollo
  (slice 23c), rpId check from a VPN client (cutover day), and
  the runbook T-1h pre-flight run on the day.

## Slices completed this session

| # | Title | Commit | Report |
|---|---|---|---|
| 22-prep | Shared PageHeader / FormActions / EmptyState / ErrorBanner primitives | `8c58c12` | (this is Phase 0 of the dogfood fix) |
| 22 | UI consistency pass on 12 Astro pages | `4c85b7f` | `slice-reports/slice-22-ui-consistency.md` |
| 23a | Self-join guard + raw_token return + slice backend tests | `45e7548` | `slice-reports/slice-23a-circles-backend.md` |
| 23b | Circles frontend — welcome page, copyable invite, ErrorBanner, ?next= login, crown a11y | `b349f96` | `slice-reports/slice-23b-circles-frontend.md` |

**Total slices in repo**: 1–23 (slices 1–14 from prior sessions; slices
15–21 from earlier this session; slices 22-prep + 22 + 23a + 23b from
this dogfood response).

## What's still open (post-slice-26d)

| # | Item | Severity | Plan |
|---|---|---|---|
| 22x | day-of-week strip + Add-habit + /security Loading | — | DONE — `956cec2`; browser-verified 2026-09-25 late |
| 23c | Email delivery + HTML template + SMTP wiring | — | DONE — `5a8ca1c`; apollo SMTP env still needed for real delivery (dev uses SMTP_DEV_LOCAL_OUTBOX) |
| 24d–g | member shares + welcome roster + empty-state | — | DONE — `1b53fcc`…`7dd8d42` |
| 25 / 26 | vanta public surface + landing shopfront + /help cards | — | DONE — `f546c20`…`96a6a41` |
| — | Cutover gates (Phase 4) | High | Docker smoke on built image + backup/restore rehearsal + 24h monitoring — not yet run |

## Slices completed this session

| # | Title | Commit | Report |
|---|---|---|---|
| 15 | Astro BFF bridge for client-side routes + coverage audit | `ab76965` + `efa9e14` | `slice-reports/slice-15-bff-coverage.md` |
| 16 | Private Circles in Sharing section + shadcn Checkbox for Display card | `3cbf9b0` + `a82011a` | `slice-reports/slice-16-sharing-section-and-checkbox.md` |
| 17 | "Back to habits" link on /tokens, /account/delete, /habits/new | `c4420ff` | `slice-reports/slice-17-back-to-habits.md` |
| 18 | Tick-form round-trip verified via browser (no new pytest) | `3b60ba2` | `slice-reports/slice-18-tick-round-trip.md` |
| 19 | Phase-4 cutover runbook | `a8e5819` | `slice-reports/slice-19-cutover-runbook.md` |
| 20 | Monorepo Docker image build (Astro stage + production proxy) | `1c2f696` | `slice-reports/slice-20-monorepo-image.md` |
| 21 | Apply runbook answers (option B + castor names + slim bases) | `17a48bd` | `slice-reports/slice-21-apply-runbook-answers.md` |

**Total slices in repo**: 1–21 (slices 1–14 done in prior sessions;
slices 15–21 done this session).

## What's left before cutover

The slice-19 cutover runbook + slice-21 answers together unblock
the actual deploy. The remaining work is operational, not code:

1. **Pick a cutover date + image tag**. Edit `docker-compose.yml`
   line 8: replace `castor:custom-2026-09-XX` with
   `castor:custom-<actual-date>`.

2. **Build the image on apollo** (or any Docker-capable host):
   ```bash
   ssh apollo
   cd /opt/castor           # or wherever the repo lives
   docker build -f docker/Dockerfile -t castor:custom-<date> .
   ```
   First build will be slow (~5–10min) because `pnpm install` on
   345MB of `node_modules`. Subsequent builds reuse the cache.

3. **Run the cutover-day sequence** (slice-19 runbook §5):
   - T-1h: pre-flight verification (astro check, pytest, audits,
     browser smoke).
   - T-0: ~5min downtime — pull legacy container, start new image.
   - T+10min: post-cutover verification (login, tick, passkey, circles).
   - Rollback path (~3min): revert to legacy `castor:custom-2026-09-14`.

4. **24h acceptance criteria monitoring** (slice-19 runbook §6).

## Cutover decisions recap (locked 2026-09-25)

- Volume: `castor_data`
- Container: `castor`
- User notice: none
- `/auth/login` strategy: option B (Astro server-side via
  `backendFetch`; backend keeps `/auth/webauthn/*`)
- Base images: slim (`python:3.14-slim`, `node:22.12-bookworm-slim`)
- `token_version`: bump for everyone on cutover (SQL in runbook §4)
- `WEBAUTHN_RP_ID`: `10.8.0.1` (already set in slice 20 compose)

## Open parity-matrix items (post-slice-21)

| Severity | Item | Group | Status / next action |
|---|---|---|---|
| High | rpId mismatch | 1.2 | RESOLVED — slice 20 fixed compose env; apollo deploy will pick it up |
| Medium | Import POST backend endpoint | 6.3 | Defer — page surfaces honest 404 |
| Low | CSV export | 6.2 | Net-new (legacy never had it) |
| Low | Telegram backup config UI | 6.4 | Net-new (legacy never had it) |
| Low | Last-key passkey removal UX | 5.3 | UX polish (uses `prompt()` chain) |
| Low | Change-password UX | 5.4 | UX polish (uses `prompt()` chain) |
| Low | Daily-rollover timer | 11.2 | Defer (most users reload) |
| Low | `/completion-status` (chip-set editor) | 12.2 | Defer |
| Low | Habit image upload (notes) | 12.5 | Defer (backend has no media path yet) |
| — | Paddle pricing page | 12.3 | Intentionally dropped |
| — | Google One Tap | 12.4 | Intentionally dropped |
| — | 53-week heatmap | 3.4 | Intentionally dropped (15-week view covers UX need) |
| — | WebSocket fan-out to Astro | 11.1 | Mobile-only; Astro pages re-fetch |

## Phase-4 polish followups (nice-to-have, all post-cutover)

- NUMERIC-affinity `DateTime(timezone=True)` SQLAlchemy+aiosqlite
  silent-drop in `audit.record()` (slice 11 investigation, not fixed).
- `/admin/users` pagination (`limit`/`offset`).
- `prune_audit_events` retention config (parity 12.4).
- Proper `dependency_overrides` for `settings.ADMIN_EMAIL` (slice 8
  pattern).
- Playwright E2E for slice-15 BFF routes (currently only
  browser-verified).

## How to resume next session

```bash
# 1. Restore dev runtime (demo seed included)
bash scripts/dev-up.sh

# 2. Smoke-test before doing anything else
curl -s -o /dev/null -w "Dev: %{http_code}\n" http://127.0.0.1:4321/login
.venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q | tail -1
cd web/concepts && pnpm exec astro check | tail -3

# 3. Re-orient on phase 4 work by reading:
#    - docs/migration/slice-reports/slice-19-cutover-runbook.md
#    - docs/migration/slice-reports/slice-21-apply-runbook-answers.md
#    - docs/migration/parity-matrix.md §"Remaining open rows"

# 4. Decide: cutover day ops vs. Phase-4 polish vs. new slices
```

## Common pitfalls / lessons captured this session

- **Slice 15 audit gap**: only checked `/api/v1/*` paths. Client-side
  `SecurityContent.tsx` / `login.astro` / `register.astro` use
  `fetch(\`${BACKEND_URL}/auth/webauthn/...\`)`. Slice 20 middleware
  proxy closes the gap for production. **Lesson**: any future
  audit must grep ALL path prefixes the browser can hit.
- **Audit script false positives** (`audit_bff_coverage.py`): JS
  `${paramName}` syntax doesn't match Astro's `[paramName]` path
  param syntax. Three iterations needed to land on **path-shape
  matching** (segment counts + non-variable literal segments). The
  script is now in `docs/migration/scripts/audit_bff_coverage.py`.
- **Sharp forces bookworm-slim**. Alpine would need a source build
  of libvips (~5min build penalty, historically flaky). Sticking
  with bookworm is the right choice.
- **Cookie names**: backend sets `beaver_auth` (legacy) but Astro's
  `writeSession()` writes `castor_token`. The middleware mirrors
  the WebAuthn cookie `beaver_webauthn` → `castor_webauthn_browser`.
- **Demo seed range**: 21 days back. Tests must scan the visible
  7-day grid for 'absent' cells, not pick fixed date offsets.
- **Astro default CSRF** blocks cross-origin form POSTs without a
  matching `Origin` header. Browser-driven flow works; raw curl
  needs `Origin: http://127.0.0.1:4321` + `Referer`.
- **Inline regex scripts that use `re.search` across multiline** can
  hang silently. Use `re.finditer` over whole text instead.

## Files of interest

- `docs/migration/slice-reports/` — 22 slice reports (1–21, plus
  the cutover runbook).
- `docs/migration/scripts/audit_parity_matrix.py` — matrix
  stale-evidence detector.
- `docs/migration/scripts/audit_bff_coverage.py` — BFF
  route-coverage detector with path-shape matching.
- `docs/migration/parity-matrix.md` — single source of truth for
  legacy ↔ migration feature parity.
- `docs/migration/architecture-target.md` — full architecture of
  the migrated app (Astro + React + shadcn/ui).
- `docs/migration/risks-and-decisions.md` — risk register.
- `web/concepts/src/middleware.ts` — production proxy + session
  handling.
- `docker/Dockerfile` — monorepo image (slice 20).
- `docker-compose.yml` — apollo deploy config (slice 21).
- `scripts/dev-up.sh` + `scripts/stop-dev.sh` — dev runtime helpers.

## Standing goal (user)

> "Define and implement next relevant slices."

**Status**: completed all 7 relevant slices this session (15–21).
The slice-19 runbook's blocking questions are resolved. The next
relevant work is **cutover-day ops** (no more code slices without
a fresh user request) or one of the **Phase-4 polish followups**
listed above.
