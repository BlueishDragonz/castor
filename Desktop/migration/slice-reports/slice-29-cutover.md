# Cutover report — 2026-09-26 (slice 29): legacy sunset, Astro deploy live

**Branch**: `release/astro-migration` @ `a867213`
**Image**: `castor:custom-2026-09-26` (1.5GB, includes slice-28 circles BFF fix)
**Downtime**: ~4 minutes (02:34:24Z backup → 02:36:18Z new container up)
**Result**: CUTOVER COMPLETE — serving at http://10.8.0.1:8080, healthy.

## Deployment-reality corrections found DURING cutover (critical)

The runbook + skills described a deployment that no longer existed:

1. **Live compose is `/opt/castor/docker-compose.yml` (project `castor`)**,
   NOT `/opt/wg-net/docker-compose.yml`. The wg-net beaverhabits entry is a
   dead relic (image `castor:custom-2026-09-14`, volume `beaver_data`).
2. **Live volume is `beaverhabits_beaver_data`** (15 users, current). The
   external `beaver_data` volume was a STALE ORPHAN (Sep-16 snapshot,
   4 old users incl. e2e@/rotate-probe@). Dry-run v1 accidentally used it
   and caught the mismatch via user-count divergence (4 vs 15). Both
   orphan volumes (`beaver_data`, `castor_beaver_data`) were removed in
   the sunset cleanup.
3. Live env had `WEBAUTHN_RP_ID=localhost` (the pre-slice-20 value),
   not the 10.8.0.1 the runbook assumed was already applied.

Lesson: `docker inspect <ctr> --format {{index .Config.Labels ...}}` is the
source of truth for which compose file is live, before believing any doc.

## Sequence actually executed (all on apollo)

| # | Step | Evidence |
|---|---|---|
| 1 | Pre-flight: pytest | 392 passed / 12 skipped |
| 2 | Pre-flight: astro check, both audits | 0 errors; BFF audit `(none)`; parity 7 intentional gaps |
| 3 | Build fresh image from HEAD `a867213` at `/opt/castor-build/` | `castor:custom-2026-09-26`, 1.5GB |
| 4 | Dry-run v2: new image on a copy of the REAL volume | booted, 15 users intact, 5 circles tables added additively, login/webauthn-check 200 |
| 5 | Production backup (in-container `VACUUM INTO`) | `habits.db.pre-cutover-20260926T023424Z` (176KB) |
| 6 | Stop legacy `beaverhabits` | downtime starts 02:34 |
| 7 | token_version bump on real volume (validated sidecar cmd) | `Bumped 15 users` → `[(1,6),(2,4),(3,5)]` |
| 8 | Rewrite live compose: service `castor`, image `custom-2026-09-26`, rpId `10.8.0.1`, FRONTEND_URL, same volume | `/opt/castor/docker-compose.yml` (legacy copy: `docker-compose.yml.legacy-20260926`) |
| 9 | `docker compose up -d` (project `castor`) | healthy after ~15s |
| 10 | Data preservation | users 15/15, habit_list 2/2, webauthn 1/1, audit 56→64 (grew only) |
| 11 | Page smoke via wg netns | `/`, `/login`, `/help` 200; `/health` OK |
| 12 | E2E via VPN URL (curl sidecar in wg netns): register probe → login → tick → SQLite record | login 302 offer_passkey; habit `635f50` created; tick record `{2026-09-26, done:true}` in DB |
| 13 | Restart persistence on PROD | cookie + habit + tick all survived `docker restart`; healthy 10s |
| 14 | Logout / revocation | `POST /logout` 302; token_version 3→4 for probe; revoked cookie → 302 |
| 15 | Probe cleanup via app's own `/api/account/delete` | 200; tombstoned `deleted+cdb67b02…`, is_active=0, habit list removed; back to 15 real users + tombstones |
| 16 | Sunset: remove legacy container + orphan volumes | `beaverhabits` container removed; `beaver_data` + `castor_beaver_data` volumes deleted (stale data — user approved sunset) |

## Rollback route (intact, ~3 min)

1. `cp /opt/castor/docker-compose.yml.legacy-20260926 /opt/castor/docker-compose.yml`
2. `cd /opt/castor && /usr/bin/docker compose up -d`
3. If data rollback also needed (NOT expected — schema changes were
   additive-only): restore from
   `beaverhabits_beaver_data:/app/.user/habits.db.pre-cutover-20260926T023424Z`
   using the in-container Python heredoc from runbook Rollback §.
4. Keep `castor:custom-2026-09-14` + `castor-beaverhabits:latest` images for
   ≥1 week (per skill rule) before any `docker rmi`.

Note: the legacy compose env had rpId localhost; the legacy-20260926 copy
preserves the file as it was — it boots the legacy image exactly as before.

## User-visible changes

- Everyone must log in again once (token_version bumped). Expected, communicated.
- jrodux's registered passkey row survived (webauthn creds 1/1) and
  `/auth/webauthn/check` reports `has_passkey:true`. rpId is now 10.8.0.1
  (was localhost) — existing passkey may require re-registration depending
  on browser rpId-suffix behaviour; Safari is more permissive. Verify on a
  real device when convenient.
- New surface: Astro UI at the same URL; `/habits`, `/stats`, `/circles`,
  `/security`, `/tokens`, `/admin` (admin-gated), plus help/terms/privacy.

## Monitoring (24h)

Container `castor` has compose healthcheck (10s timeout vs image's 3s
default — the 3s default was tripped by probe contention during dry-runs
with 3 stacks on the 1-vCPU box; with dry-runs removed, streak is 0).
Suggest a quick VPN check from a phone/browser today.

## Leftovers / follow-ups

- `castor:custom-2026-09-25` (superseded same-day) can be untagged anytime.
- SMTP env for circles invite email still unset (slice 23c) — invites are
  link-only until then.
- PROGRESS.md + parity matrix row updates follow in the next commit.
