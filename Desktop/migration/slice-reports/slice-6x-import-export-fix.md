# Slice 6.x — Fix the three Slice 6 schema/contract gaps — Status Report

**Branch**: `release/astro-migration` (parent `3afb42d` from Slice 6)
**Date**: 2026-09-24
**Scope**: Resolve the three findings documented in `slice-6-import-export.md`

---

## What changed

Three schema/contract gaps surfaced by Slice 6 testing were all fixed:

### Gap #1: Export strips `status`, `period`, `star`
**File**: `castor/routes/api.py` (`_habit_list_export_data`)
**Fix**: Guarantee `status` is always present in the export. `period`, `star`, `tags` were already written by the PUT path; only `status` was missing because the DictHabit default lives only in the Python object.
**Tests added**: `test_export_returns_habit_metadata`, `test_export_archived_habit_preserves_status`, `test_export_default_status_is_active_for_new_habit`

### Gap #2: Records schema mismatch (`/habits/export` flat vs `/habits/{id}` nested)
**File**: `castor/routes/api.py` (`_habit_list_export_data`)
**Fix**: Normalise records to the canonical nested shape `{data: {day, done, timestamp, text?}}` matching `/habits/{id}`. The Astro UI (8+ uses of `r.data?.day` across 4 pages) already expects the nested shape; the export was the outlier.
**Tests added**: `test_export_record_shape_matches_habit_detail`

### Gap #3: BFF import loop silently drops `tags`/`status`/`period`
**File**: `web/concepts/src/pages/api/v1/habits/import.ts`
**Fix**: POST `{name}` first, then PUT `{tags, period, status, star}` on the new habit id. Records are POSTed after metadata. This was the documented "3-line fix" — actually 25 lines including the conditional + records flatten.
**Tests added**: `test_import_loop_preserves_tags_period_status`, `test_import_loop_preserves_records_with_nested_shape`. The old `test_import_loop_drops_records` is now a stub pointing to the new tests.

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && .venv/bin/python -m unittest discover -s tests -p test_slice6_import_export.py -v
... 19 tests ...
Ran 19 tests in 10.984s
OK
# Was 14 → now 19; delta = +5 from new tests, one renamed

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
288 passed, 1 warning in 205.83s (0:03:25)
# Was 283 → now 288; delta = +5 (matches the slice-6 test deltas)

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (92 files):
- 0 errors
- 0 warnings
- 58 hints
```

## What changed (file diff)

| File | Change |
|---|---|
| `castor/routes/api.py` | +46 / −7: `_habit_list_export_data()` guarantees `status`; normalises records to nested shape. |
| `web/concepts/src/pages/api/v1/habits/import.ts` | +25 / −14: POST `{name}` only, then PUT metadata, then POST records (flattened). |
| `tests/test_slice6_import_export.py` | +307 / −67: extended Slice 6 tests with the new contract assertions. |
| `Desktop/migration/parity-matrix.md` | Updated rows 6.1, 6.3 with the fix evidence. |

## Failed checks

None.

## Round-trip verified end-to-end

The most important new test is `test_import_loop_preserves_records_with_nested_shape`:

1. Create a habit
2. Tick today with a note
3. Export → JSON includes the record (nested shape)
4. Wipe the habit (DELETE)
5. Re-import via the BFF pattern (POST + PUT + records loop)
6. Verify the new habit has the tick AND the note AND the tag AND the period

This was failing in Slice 6 (records dropped, tags dropped, period dropped). It now passes end-to-end.

## Open items (still deferred)

- **CSV export** (parity row 6.2) — backend doesn't expose it; parity matrix already says ❌.
- **Backend `POST /api/v1/habits/import`** — the BFF loop with POST + PUT + records works correctly for the common case. A real backend endpoint would be needed for very large imports (1k+ habits) where the loop's HTTP overhead matters. **Not a regression** — Slice 6 also documented this gap as low-priority.
- **Browser E2E for /export and /import** — Playwright territory, deferred to Slice 9.

## Next proposed slice

**Slice 7 — `/help` + `/tokens` + `/settings` + `/account/delete`**: 6 parity-matrix rows of mostly Astro UI; backend contracts mostly already pinned. Estimated 3–4 hours.

Alternatives: `/stats`, `/admin`, Playwright scaffold for Slice 9.

## Sign-off request

Per the brief, do not move to Slice 7 until you confirm:
1. Slice 6.x evidence sufficient (the three gaps are now closed)?
2. Schema changes are acceptable (records nested shape, export adds status, BFF does POST+PUT)?
3. Slice 7 is the right next slice — or switch?
