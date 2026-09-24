# Slice 2 — Home grid (read + tick) — Status Report

**Branch**: `release/astro-migration` (parent `791ee1e` from Slice 1)
**Date**: 2026-09-24
**Scope**: `/habits` list, `/habits/{id}` detail, `/api/v1/habits` POST, tick round-trip

---

## Working flows (verified end-to-end against backend)

| Flow | Test |
|---|---|
| New user → `GET /api/v1/habits` → `[]` (empty state, NOT 404) | `test_get_habits_empty_returns_empty_list` |
| `GET /api/v1/habits` without bearer → 401 | `test_get_habits_unauthenticated_returns_401` |
| Create 2 habits → list returns both in creation order | `test_get_habits_lists_active_in_creation_order` |
| `GET /api/v1/habits/{id}` returns `{id, name, records: []}` | `test_get_habit_detail_returns_records` |
| Cross-user habit_id → 404 (no enumeration) | `test_get_habit_detail_for_other_users_habit_returns_404` |
| Tick today → next detail fetch shows `data.done=true` for today | `test_tick_today_persists_and_appears_in_records` |
| Un-tick today → `data.done=false` (preserves record) | `test_untick_removes_done_flag` |
| Tick with invalid date → 400 | `test_tick_invalid_date_format_returns_400` |
| Tick with note text → persists | `test_tick_with_note_persists_text` |
| Tick without bearer → 401 | `test_tick_unauthenticated_returns_401` |
| Tick other user's habit → 404 | `test_tick_other_users_habit_returns_404` |
| `POST /api/v1/habits` → `{id, name}` | `test_create_habit_returns_id_and_name` |
| 6th active habit → 400 "Maximum habit count (5) reached" | `test_create_habit_enforces_max_habit_count` |
| Create habit without bearer → 401 | `test_create_habit_unauthenticated_returns_401` |
| Tick → list → habit appears in listing | `test_tick_survives_list_round_trip` |

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/test_slice2_habits.py -v
... 16 tests ...
Ran 16 tests in 8.903s
OK

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
209 passed, 1 warning in 127.11s (0:02:07)
# Was 193 → now 209; delta = +16 from this slice.

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (92 files):
- 0 errors
- 0 warnings
- 58 hints
```

## What changed

| File | Change | Reason |
|---|---|---|
| `castor/routes/api.py` | Added `status` to the `from fastapi import (...)` block (line 14) | **Bug fix**: `post_habits` (line 106) used `status.HTTP_400_BAD_REQUEST` but `status` was not imported — would 500 on 6th habit in production. Slice 2's MAX_HABIT_COUNT test surfaced this. |
| `tests/test_slice2_habits.py` | NEW — 16 hermetic integration tests pinning the `/api/v1/habits*` contracts | Slice 2 deliverable per brief: valid/invalid/unauthorised/empty/persistence + cross-user 404 |

## Failed checks

None.

## Bug found and fixed (the headline)

**`castor/routes/api.py:106` referenced `status.HTTP_400_BAD_REQUEST` without importing `status` from `fastapi`.**

The MAX_HABIT_COUNT enforcement was effectively unreachable in production: the 6th habit POST would raise `NameError: name 'status' is not defined` (500), not a 400. This was latent because no existing test exercised this code path against an actual user with 5 habits. Slice 2's `test_create_habit_enforces_max_habit_count` surfaced it; the fix is a one-line import.

**Why this is a Slice 2 finding and not deferred**: the brief says "Refactor or rewrite only the code needed to preserve the specified behaviour" — but the specified behaviour is "6th habit → 400 with MAX_HABIT_COUNT message", and the existing code was incapable of delivering it. The fix is one line, isolated, and the test would have failed without it. Recording it here so the cutover review sees the bug discovery and the fix together.

## Open items (deferred, not blocking)

- **Cross-user habit_id 404 for cross-user detail/tick**: covered by 2 tests; broader cross-user audit (admin endpoints, completion history with timestamps) is in Slice 9 (admin).
- **Drag-and-drop reorder** (parity row 2.9): not in Slice 2; needs a different endpoint (`PUT /api/v1/habits/meta` body `{order: [...]}`) — that's Slice 7 (Settings/Help) or a dedicated slice.
- **WebSocket fan-out verification**: the `HabitListChanged` event publish is in `castor/routes/api.py` and a real-time broadcast code path exists, but the cross-socket behaviour is not unit-tested (would require two test websockets). Deferred to Slice 9.
- **`/habits` page rendering** (sticky headers, today highlight, badge toggles, tag filter, scroll-to-reveal): requires Playwright; deferred to Slice 9.

## Next proposed slice

**Slice 3 — Add / Edit / Archive**:
- `POST /api/v1/habits` (already covered in Slice 2 for the backend contract)
- `PUT /api/v1/habits/{id}` (edit name/star/period/tags)
- `DELETE /api/v1/habits/{id}` (archive)
- `PUT /api/v1/habits/meta` (reorder)
- New Astro pages: `habits/new.astro`, `habits/[id]/edit.astro`
- Backend tests for PUT, DELETE, reorder

Estimated: 3–4 hours (mostly backend tests + small Astro page wiring).

## Sign-off request

Per the brief, do not move to Slice 3 until you confirm:
1. Slice 2 evidence sufficient
2. **Bug fix acceptable** (`status` import in `castor/routes/api.py`) — or want it split into its own commit?
3. Slice 3 (Add/Edit/Archive) is the right next slice, vs. `/help` 404, Import/Export, or /security (WebAuthn)
