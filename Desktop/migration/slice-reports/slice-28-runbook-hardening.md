# Slice 28 — Runbook hardening: chown note, T-1h pre-flight update, verbatim-validated commands

**Branch**: `release/astro-migration`
**Date**: 2026-09-26
**Hosts**: homelab2 (repo), apollo (validation, throwaway containers only)

## Trigger

Slice 27's rehearsal surfaced one runbook-relevant finding (root-owned
volume restore bricks boot) and left the slice-19 runbook's T-1h
pre-flight expectations stale (392-test baseline, new audit outputs,
the Docker image gate). This slice updated the runbook — and in the
process validated every rewritten command verbatim on a throwaway
container/volume on apollo. Production (`/opt/castor/`, `beaverhabits`,
`beaver_data`) untouched.

## Discovery while updating the checklist

Running the BFF audit as part of pinning "expected output" caught a
**real production bug**, not just a doc drift:

- `audit_bff_coverage.py` flagged
  `circles/[id]/welcome.astro → /api/v1/circles/{circleId}/members/me/habits (no BFF proxy)`.
- The share-your-habit wizard (slice 24d) POSTs from a browser-side
  `<script>` (`welcome.astro:362`) — in dev the Vite proxy masks the
  missing route; in production the POST would 404.
- The backend route exists and is pinned by
  `tests/test_slice24d_member_shares.py` (`circle_routes.py:459`).
- Fixed with a thin BFF pass-through:
  `web/concepts/src/pages/api/v1/circles/[circleId]/members/me/habits/index.ts`
  (cookie → bearer, JSON relay, 400/401/502 guards). GET/DELETE of
  member shares are server-side (backendFetch) and intentionally
  not proxied.
- Verified live in dev: registered a fresh user via the real form
  flow, then `POST /api/v1/circles/1/members/me/habits` through the
  BFF → `404 {"detail":"Circle not found"}` (the backend's own
  `_require_member` response — proves the full path works).
  `astro check` 0 errors; `audit_bff_coverage.py` now `(none)`;
  `tests/test_slice24d_member_shares.py` 9 passed.
- Same bug class as slices 15 and 20 — third time this audit caught a
  dev-proxy-masks-production-404 gap. The T-1h checklist now states
  this audit's passing output explicitly.

## Verified-against-reality findings baked into the runbook

- **No `sqlite3` CLI anywhere**: `docker exec beaverhabits which
  sqlite3` → not found; stock alpine → not found (verified 2026-09-26).
  The runbook's §3/§4/§6 backup and token_version-bump commands as
  previously written would ALL have failed on cutover day. Rewritten
  to use the app image's venv Python.
- **`Connection.rowcount` doesn't exist** (§4's original command):
  `sqlite3` exposes `rowcount` on the cursor, not the connection. The
  original command crashed BEFORE `c.commit()` — it would have
  printed a traceback and bumped NOBODY, at the worst possible moment.
  Found by executing the rewritten command verbatim on a throwaway
  volume (not by reading). Fixed in both §4 and §6.
- **Nested-quote fragility**: `docker exec … python -c "…VACUUM INTO
  '…'…"` breaks when run through ssh (the inner quotes collapse — I
  hit it myself twice during validation, producing 0-byte "backups").
  All SQL-bearing commands rewritten to the heredoc form
  (`docker exec -i … - <<'PY' … PY`) which is copy-paste-safe.
- **Ownership is load-bearing** (slice-27 rehearsal finding, now
  documented in §3): legacy and migration images both run as
  `nobody` (65534) — verified via `docker inspect beaverhabits
  --format '{{.Config.User}}'`. Any root-sidecar volume copy/restore
  without `chown -R 65534:65534` leaves the app unable to boot
  (`attempt to write a readonly database`, gunicorn worker exit 3).
- **Rollback restore**: now uses `glob` for the newest
  `habits.db.pre-cutover-*` (timestamped names sort
  chronologically) instead of a shell glob in `sh -c` that also
  would have needed the nonexistent sqlite3 CLI.

## Validation performed (throwaway `castor_rbtest` + volume `castor_rbtest`, deleted after)

| Runbook command | Validation result |
|---|---|
| §6 sidecar token_version bump (heredoc) | `Bumped 2 users`; versions `[(0,2)] → [(1,2)]` |
| §4 pre-flight backup (`VACUUM INTO` via venv Python) | 270KB file written as app user; integrity ok; content matches |
| Rollback restore (glob newest, copy, integrity) | dropped user restored (`1 → 2 users`), integrity ok |
| Volume chown requirement | slice-27 finding, restated; no re-rehearsal needed |

## T-1h pre-flight checklist (§0) changes

- Expected outputs pinned to current reality: astro check
  0/0/58 (108 files), pytest 392 passed / 12 skipped, parity audit =
  7 intentional gaps only (3.4, 6.2, 6.4, 7.3, 11.2, 12.2, 12.5),
  BFF audit = `(none)`.
- New **§0b Docker image gate**: build on apollo at
  `/opt/castor-build/` (explicitly NOT `/opt/castor`), import gate,
  full throwaway smoke sequence (pointer to slice-27 report's
  Commands section), cleanup. Records that slice 27 already passed
  every gate on `castor:custom-2026-09-25`.

## Files changed

- `Desktop/migration/slice-reports/slice-19-cutover-runbook.md` —
  §0 expectations + §0b image gate; §3 backup + chown warning; §4
  bump (cursor fix + heredoc); §6 backup + bump; §7 verify; Rollback
  restore.
- `web/concepts/src/pages/api/v1/circles/[circleId]/members/me/habits/index.ts`
  — NEW BFF route (the production-404 fix).
- `Desktop/migration/slice-reports/slice-28-runbook-hardening.md` —
  this file.

## Verification commands + outputs

- `pnpm exec astro check` → 0 errors / 0 warnings / 58 hints
- `python3 Desktop/migration/scripts/audit_bff_coverage.py` →
  client-side gaps: `(none)`
- `python3 Desktop/migration/scripts/audit_parity_matrix.py` →
  open-status rows: 9 (7 genuine, all intentional)
- `.venv/bin/python -m pytest tests/test_slice24d_member_shares.py -q`
  → 9 passed
- BFF route live check in dev → `404 {"detail":"Circle not found"}`
  (backend's own response relayed through the new BFF)
- All §4/§6/rollback commands executed verbatim on throwaway
  containers on apollo (outputs above)

## Open items (unchanged by this slice)

- Cutover date + tag decision (user).
- SMTP env on apollo for circles invite email (slice 23c).
- rpId check from a real VPN client (cutover day).
- Note: a `wizard-test@castor.example.com` user created for the BFF
  live-check remains in the LOCAL DEV database only (its DELETE
  cleanup was blocked awaiting consent); dev DB is throwaway — it
  vanishes with the next `dev-up.sh` reseed. No production impact.
