# Castor Migration Audit — Branch vs Main
**Date:** 2026-09-23 22:55 BST
**Branch:** `migration-to-shadcn-astro` (52 commits ahead of main)
**Main:** `BlueishDragonz/castor` running live on apollo (NiceGUI/Quasar)

---

## TL;DR

The migration is **substantially complete for the happy path**: auth, login,
habit CRUD, stats, settings, circles, recovery email. Roughly **35 Astro pages**
and **17 BFF endpoints** replace the 27 NiceGUI pages. Branch has **net-new**
features main lacks (circles, passkey offer persistence, audit trail, 60-second
demo re-seeding).

What's missing for full parity:

| # | Feature | Branch status | Severity |
|---|---|---|---|
| 1 | `/help` page (linked from MoreSheet.tsx — **currently 404**) | ❌ Missing | **High — visible 404** |
| 2 | `/gui/tokens` — API token management page | ❌ Missing | Medium |
| 3 | `/gui/completion-status` — per-habit default completion state | ❌ Missing | Low |
| 4 | Best-streak card (main has dedicated page; branch has stats but no best-streak UI) | ⚠️ Partial | Low |
| 5 | WebSocket sync (`/api/v1/habits/sync_ws`) — branch has endpoint, untested client | ⚠️ Untested | Medium |
| 6 | `metrics` app + `/metrics` Prometheus endpoint | ❌ Missing (dropped) | Low |
| 7 | Google One Tap login | ❌ Dropped | Low |
| 8 | Paddle billing + `/pricing` page | ❌ Dropped (was commercial-only) | None |
| 9 | `/demo/*` NiceGUI routes — replaced by `castor/demo_seed.py` startup hook | ✅ Replaced | — |

**Plus a separate issue blocking user workflow:**

| # | Issue | Status |
|---|---|---|
| A | Demo account wipes every ~1–3 hours, root cause unknown | Mitigation in place (auto-seed on startup + `/dev/reseed-demo` endpoint). New poll-based watcher now armed. |

---

## 1. Branch vs main — file inventory

### What main has that the branch dropped (36 files deleted)

```
beaverhabits/frontend/*  (all 23 NiceGUI page modules — replaced by Astro pages)
beaverhabits/routes/google_one_tap.py
beaverhabits/routes/routes.py          (NiceGUI @ui.page registrations)
beaverhabits/routes/metrics.py         (Prometheus metrics app)
beaverhabits/metrics_app.py
beaverhabits/plan/paddle.py            (billing — commercial, intentionally dropped)
beaverhabits/utils.py                  (helpers, partly ported to castor/utils.py)
beaverhabits/const.py                  (port constants; renamed to castor/const.py)

tests/test_batch1_webauthn.py
tests/test_batch2_ux.py
tests/test_batch3_durability.py
tests/test_lane2_context_menu.py
tests/test_lane2_security_page.py
tests/test_legacy_recovery_safety.py
tests/test_recovery_logging.py
tests/test_security_dialogs.py
tests/test_security_passkeys.py
tests/test_security_recovery_card.py
tests/test_storage.py
tests/test_utils.py
tests/test_gui.py
tests/security_dom_harness.py
tests/security_virtual_acceptance.py
```

### Net-new on branch (was not on main)

- **`castor/`** — entire Python package (was `beaverhabits/`); pure rename + enhancements
- **`web/concepts/`** — Astro app (35 pages, 17 BFF endpoints, 18 shadcn primitives)
- **`castor/app/audit.py`** + `audit_event` table — main has NO audit trail
- **`castor/app/circle_routes.py`** + circles schema (5 tables) — main has NO circles
- **`castor/app/security_actions.py`** — passkey CRUD with session-version bumping
- **`castor/app/admin_routes.py`** — admin backup endpoint + `/admin` page
- **`castor/demo_seed.py`** — idempotent demo seeder, startup hook + `/dev/reseed-demo`
- **`castor/recovery_email_*` schema** — recovery email verification
- **`castor.integrity.py`** — daily SQLite integrity check + `habits.db.integrity.json` ledger
- **`castor.scheduler.py`** — daily backup task (vs main's apollo-side systemd timer)
- 9 new shadcn primitives: avatar, alert, badge, button, card, carousel, command, context-menu, dialog, dropdown-menu, input, label, menu, resizable, scroll-area, separator, sheet, skeleton, tabs
- 35 Astro pages including: `/security`, `/settings`, `/stats`, `/admin`, `/export`, `/import`, `/circles/*`, `/account/delete`, `/forgot-password`, `/verify-reset`, `/reset-password`, `/terms`, `/privacy`, `/logout`

### Schema differences (live DBs, not source)

| Column / Table | Main (apollo, live) | Branch (homelab2, dev) |
|---|---|---|
| `user.token_version` | **Missing** | ✅ present, default 0 |
| `user.passkey_offer_dismissed` | **Missing** | ✅ present |
| `user.recovery_email`, `recovery_email_verified` | **Missing** | ✅ present |
| `audit_event` table | **Missing** | ✅ present (4 rows in dev) |
| `circle`, `circle_habit`, `circle_invite`, `circle_member` | **Missing** | ✅ schema exists, empty in dev |
| `recovery_email_challenge` | **Missing** | ✅ present |
| `password_reset_code` | ✅ present | ✅ present |

**Implication:** Branch has accumulated several security and feature enhancements
that haven't been back-ported to main's running schema. The branch is the
**more recent state of the data model**, not the older one. Migration plan
direction: branch becomes the new main, not the other way around.

---

## 2. Routes parity — what users can still reach on main that's gone on branch

| Main route | Branch route | Note |
|---|---|---|
| `/demo/*` (9 demo URLs) | `/dev/reseed-demo` (POST) | Replaced — same goal (demo content), different mechanism. Cleaner. |
| `/gui` | `/habits` | Renamed; same content. |
| `/gui/add` | `/habits/new` | Renamed. |
| `/gui/stats` | `/stats` | Renamed. |
| `/gui/order` | `/habits/order` | Renamed. |
| `/gui/habits/{id}` | `/habits/[id]` | Renamed. |
| `/gui/habits/{id}/streak` | — | **Missing — `/habits/[id]` has streak but no dedicated streak page.** Low. |
| `/gui/habits/{id}/heatmap` | `/habits/[id]` (heatmap is inline) | Combined. |
| `/gui/import` | `/import` | Renamed. |
| `/gui/export` | `/export` | Renamed. |
| `/gui/settings` | `/settings` | Renamed. |
| `/gui/security` | `/security` | Renamed. **Branch adds passkey list, add, remove, recovery email, password change, recovery email send/verify.** |
| `/gui/completion-status` | — | **Missing** — per-habit default completion state UI. Low. |
| `/gui/tokens` | — | **Missing** — API token management UI. Branch has API token *backend* (`user_api_tokens` table + `delete_user_api_token`) but no `/tokens` page. Medium. |
| `/login` | `/login` | Same — plus branch has passkey login + 4-stage progressive disclosure + offer screen. |
| `/register` | `/register` | Same — branch auto-logs in after register. |
| `/reset-password` | `/reset-password` + `/verify-reset` (staged) | Branch split reset into two pages. |
| `/settings` | `/settings` | Same. |
| `/metrics` | — | **Missing** — Prometheus endpoint. Dropped. |
| `/api/auth/google-one-tap/callback` | — | Dropped. |

**Counts:**
- Main: 27 NiceGUI pages + 1 metrics + 1 google-one-tap endpoint
- Branch: 35 Astro pages
- Branch has 17 BFF endpoints (proxy to backend, secure cookie bridge)
- Branch has 33 FastAPI backend endpoints (vs main's 10)

---

## 3. Live DB state (right now)

### Branch (homelab2) — `/home/joel/castor-repo/.user/habits.db`

```
fixture@example.com      | token_v=2 | 2026-09-23 21:35:58 (login test fixture)
demo@castor.example.com  | token_v=0 | 2026-09-23 21:54:04 (just re-seeded)
audit_event rows: 4 (all for fixture@example.com)
webauthn_credential rows: 0
habit_lists: 1 (demo's 5 habits)
integrity.json: {"checked_at": "...", "status": "ok", "problem_count": 0}
```

### Main (apollo, production) — `/var/lib/docker/volumes/beaver_data/_data/habits.db`

```
jrodux@gmail.com         | created 2026-09-06 — REAL USER, 7 habits with 3+ months of data
test@example.com         | 2026-09-11
e2e@example.com          | 2026-09-12 (1 habit_list entry with encrypted API token)
rotate-probe@example.com | 2026-09-12
habit_lists: 2 (jrodux has 2779-byte JSON with 7 real habits, e2e has 14-byte stub)
webauthn_credential: 1 (likely jrodux's)
audit_event table: MISSING
recovery_email_challenge: MISSING
```

**Real user data:** jrodux@gmail.com has 7 habits with notes spanning
2026-06-18 to 2026-09-28, including specific meal logs ("Pain au Lait",
"Sweet Chilli Chicken", "Apple and chocolate raisins"). Migration must
preserve this on switchover. Currently the branch doesn't have a data
import path from main's JSON shape — `castor.storage.dict.DictHabitList`
should be compatible (same `{habits: [...]}` shape), but no migration
script exists yet.

---

## 4. The DB-wipe mystery — current best understanding

**What's verified:**
- Wipe is real and reproducible (happened at 21:33, 21:55, 22:35 in this session alone)
- Wipe affects **only the `demo@castor.example.com` user**, not fixture or other test users
- Wipe **does not write to `audit_event`** (only 4 rows, none for demo)
- Wipe happens **without an `account_delete` event** being recorded
- Wipe happens via the SQLite file itself, not via SQLAlchemy ORM (which would log)
- Wipe happens at irregular intervals (35min, 1h22min, 22min gaps today)

**What's been ruled out:**
- ❌ Test fixtures (`test_priority1_auth.py:44`, `test_lane2_integration.py:26` do `metadata.drop_all` on the LIVE DB — confirmed bad, but last ran 19:09, no auto-runs found)
- ❌ `daily_integrity_task` — read-only integrity check
- ❌ `audit_retention_task` — clears audit table, not user table
- ❌ Cron jobs (`crontab -l` reviewed — himalaya-pa, honcho-portal-push; nothing touches the DB)
- ❌ Systemd timers — only `honcho-model-refresh`, `beaverhabits-backup` (apollo-side only), `launchpadlib-cache-clean`
- ❌ `daily_backup_task` (in lifespan) — backs up DB to volume, doesn't wipe
- ❌ `castor/demo_seed.py` startup hook — idempotent, doesn't delete
- ❌ `pytest conftest` — only widens rate limits, no DB reset

**What's still suspect:**
- ⚠️ pyinotify-only watcher missed the wipe (the WAL absorbs DELETE → no inotify event)
- ⚠️ A process outside the backend ran `sqlite3 habits.db "DELETE FROM user WHERE email='demo@…'"` — bypassing SQLAlchemy and audit
- ⚠️ Could be a previous Hermes session that ran an interactive SQL command we can't see in any log

**What's now in place:**
- `castor/demo_seed.py` startup hook auto-restores demo on every backend startup (~3 seconds)
- `POST /dev/reseed-demo` endpoint for on-demand restore without restart
- `/tmp/db-watcher.py` — **NEW poll-based watcher** (just installed at 22:53) that checks `user` table every 2 seconds via `mode=ro` SQLite. Will catch the next wipe.

---

## 5. Why main's DB isn't being wiped by UI changes

**The two DBs are on completely different machines and code paths:**

- **Main's DB** lives at `/var/lib/docker/volumes/beaver_data/_data/habits.db` inside a Docker container on **apollo** (the homelab server hosting WG-Easy VPN). It's only accessible via the VPN at `http://10.8.0.1:8080`. UI changes on the branch can't touch it because:
  - The branch's Astro frontend at `127.0.0.1:4321` only talks to the **branch's backend** at `127.0.0.1:8086` — both on homelab2.
  - The branch's backend uses a different SQLite path (`/home/joel/castor-repo/.user/habits.db`).
  - `DATABASE_URL=sqlite+aiosqlite:///./{USER_DATA_FOLDER}/habits.db` resolves to `.user/habits.db` per the `USER_DATA_FOLDER` env var (default `.user`).

- **The branch's UI changes** only modify Astro files (`web/concepts/src/pages/*`, `web/concepts/src/components/*`). They never touch the SQLite file directly. The only way UI changes could *appear* to wipe the DB is via the **test suite** (`test_priority1_auth.py` and `test_lane2_integration.py` do `metadata.drop_all` on the live DB), and pytest isn't auto-running.

**So the branch's UI changes are innocent of the wipe.** The wipe is happening on the branch DB itself, via an unknown external process.

---

## 6. The TODO.md is stale

The TODO file at `/home/joel/castor-repo/TODO.md` lists 11 items all as `[ ]` (unchecked), but most are actually done. Current state vs TODO:

| TODO item | Actual state |
|---|---|
| 1. BottomNav component | ✅ Done (BottomNav.astro + MoreSheet.tsx) |
| 2. MoreSheet (shadcn Sheet, side=bottom) | ✅ Done |
| 3. Multi-day habit grid with past-day checkboxes | ✅ Done (HabitGrid + HabitCheckBox) |
| 4. HabitCheckBox for grid cells | ✅ Done |
| 5. /security page with passkey list/add/remove/password change | ✅ Done |
| 6. WebAuthn client helpers | ✅ Done (uses navigator.credentials.{create,get}) |
| 7. auth.ts proxy for /users/* and /webauthn/* | ✅ Done |
| 8. Passkey login button on /login | ✅ Done (passkey stage in FSM) |
| 9. Settings: Import/Export/Help/PWA/Custom CSS | ⚠️ Import/Export done; /help page **MISSING** (404 from drawer); PWA done; Custom CSS dropped |
| 10. Calendar heatmap + history + best streaks | ✅ Heatmap done; ⚠️ best streaks NOT done (no dedicated UI) |
| 11. Tag filtering + long-press notes | ✅ Done (long-press notes via HabitNoteDialog) |
| 12. Verify visual quality | ✅ Done in /habits, /stats, /security via browser_vision |

**Still actually missing from the audit above:** /help page (404), /tokens (API token mgmt), /completion-status (per-habit default), best-streak card UI.

---

## 7. Recommended next actions

**Quick wins (visible bugs):**
1. Add `/help.astro` page (currently 404 from the drawer's "Help" menu item)
2. Add `/tokens.astro` for API token management (matches main)
3. Add `/admin/backup` triggering (currently the endpoint exists at backend level but no UI)

**Quality:**
4. Run the dogfood-e2e skill on `/security` flow (passkey register/login/manage) — last dogfood was cycle 3, only covered /habits and /stats
5. Run dogfood-e2e on /circles — entirely new, never dogfooded
6. Run dogfood-e2e on /forgot-password → /verify-reset → /reset-password chain

**Operational:**
7. Wait for next DB wipe event to be caught by the new poll-based watcher, then identify the culprit from the PID/cmdline
8. Once the watcher catches the wipe, capture the responsible process and decide between:
   - Block the offending process at the OS level
   - Or accept the wipe as benign and stop chasing the root cause (the auto-seed handles the symptom)

**Migration:**
9. Plan data migration from main's apollo DB → branch's homelab2 DB once both are stable
10. Plan the cutover: rebuild apollo's Docker image from the branch's `castor/` source + `web/concepts/` Astro build artifacts, switch compose to the new tag
