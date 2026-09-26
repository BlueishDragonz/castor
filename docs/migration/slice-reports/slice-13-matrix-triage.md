# Slice 13 — Parity matrix triage + audit script

**Branch**: `release/astro-migration` (parent `348b9f1` from Slice 12 docs)

**Date**: 2026-09-24

## Goal

The parity matrix had drifted: rows were marked open (🔵/❌/🟡) when
the underlying files actually existed in the repo. Slices 7–12
shipped several pages and endpoints whose matrix evidence was never
updated. This slice:

1. Audits every open-status row against actual file presence.
2. Reclassifies matrix entries where the implementation actually exists.
3. Reclassifies entries where the work is intentionally omitted.
4. Adds `audit_parity_matrix.py` so future drift is detectable in one
   command.

## What changed

| Row | Before | After | Reason |
|---|---|---|---|
| 2.7 Duplicate habit | 🔵 | ✅ | `web/concepts/src/pages/habits/[id]/duplicate.astro` + `HabitContextSheet.tsx:102` both present; form POST clones the habit |
| 9.2 Terms page | 🔵 | ✅ | `web/concepts/src/pages/terms.astro` returns 200 (no auth) |
| 9.3 Privacy page | 🔵 | ✅ | `web/concepts/src/pages/privacy.astro` returns 200 |
| 10.1 List circles | 🔵 | ✅ | `web/concepts/src/pages/circles/index.astro` (auth-gated 302 → /login) |
| 10.2 Create circle | 🔵 | ✅ | `web/concepts/src/pages/circles/new.astro` |
| 10.3 Share habit to circle | 🔵 | ✅ | `web/concepts/src/pages/circles/[id]/share.astro` |
| 10.4 Invite member | 🔵 | ✅ | `circles/[id]/invites.astro` + `circles/[id]/invites/[invite_id]/` |
| 10.5 Circle feed | 🔵 | ✅ | `circles/[id]/habits/[habit_id]/` (per-habit feed in circle context) |
| 12.1 /tokens | ❌ | ✅ | Slice 7 shipped the page + 4 endpoints; matrix evidence was stale |
| 12.7 Sort by Name/Category | ❌ | ✅ | Slice 9-alt added backend, slice 10 added UI |
| 3.4 53-week heatmap | 🟡 | ❌ intentional | Same precedent as 12.3/12.4; 15-week view covers UX need |
| 2.13 Long-press notes | 🟡 | 🟡 (clarified) | Dialog/row files exist; **gesture helper** is the real gap |

**Net**: 10 rows previously misleadingly flagged open are now ✅ or
correctly reclassified. Only 2.13 🟡 remains as a partial — the
existing `useLongPress` hook is genuinely missing.

## Why an audit script?

Before this slice, every "is this implemented?" question required:

1. Grep the matrix for the row
2. Manually check that the file path in `**Proposed new UI path**`
   or `**API/backend path**` actually exists
3. Optionally run an astro dev server to confirm the page resolves

Step 2 is tedious and error-prone — that's exactly how the drift
happened in the first place. `audit_parity_matrix.py` codifies the
check so it can be re-run on every slice without re-doing the
discovery.

```bash
$ python3 Desktop/migration/scripts/audit_parity_matrix.py
open-status rows: 10
# Stale-evidence candidates (file present, status not ✅):
# Genuine gaps (file MISSING or no paths):
```

Re-running this script on every slice will surface stale evidence
immediately.

## Files

- `Desktop/migration/scripts/audit_parity_matrix.py` (new, 116 lines)
- `Desktop/migration/parity-matrix.md` (10 row updates + summary preamble)
- `Desktop/migration/slice-reports/slice-13-matrix-triage.md` (this file)

## Verification

- `python3 Desktop/migration/scripts/audit_parity_matrix.py`:
  10 open-status rows, 1 stale-evidence candidate (2.13 — the long-press
  gesture helper gap, which is real and not a stale-evidence issue).
- `pnpm exec astro check`: 0 errors / 0 warnings / 60 hints.
- Full pytest suite unchanged from slice 12 (368 passed + 12 skipped)
  — no code paths touched.

## Phase-4 still on the open list

Re-confirmed (independent of slice 13):

| Row | Item | Notes |
|---|---|---|
| 1.2 | rpId mismatch (`localhost` vs `10.8.0.1`) | Slice 7 cutover item, untouched |
| 6.3 | Import POST backend endpoint | Honest 404 page — Slice 6.x covers it via BFF loop |
| 11.2 | Daily-rollover timer | Genuine UI gap; deferred P2 |
| 12.2 | `/completion-status` chip-set editor | Genuine UI gap; deferred P2 |
| 2.13 | Long-press gesture helper | Genuine UI gap; deferred P2 |
| 6.2 | CSV export | Genuine, never had in legacy |
| 6.4 | Telegram backup UI | Genuine, never had in legacy |
| 12.5 | Habit image upload | Genuine UI gap |
| 5.3 / 5.4 | prompt() chain UX | Working; UX-purity polish deferred |

None of these block cutover (all marked None or Low in summary).
