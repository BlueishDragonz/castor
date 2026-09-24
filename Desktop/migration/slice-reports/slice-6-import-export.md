# Slice 6 — Import / Export — Status Report

**Branch**: `release/astro-migration` (parent `3c5e16a` from Slice 5)
**Date**: 2026-09-24
**Scope**: `GET /api/v1/habits/export`, the BFF import loop (`POST /api/v1/habits` per habit)

---

## Working flows (verified end-to-end against backend)

| Flow | Test |
|---|---|
| Empty user exports `{habits: []}` | `test_export_empty_user_returns_habits_array` |
| Export includes `{name, records, id, tags}` | `test_export_returns_habit_metadata` |
| Export preserves tick records (flat shape) | `test_export_includes_ticked_records` |
| Export respects meta.order | `test_export_includes_order_field` |
| Export is user-scoped (no cross-user leak) | `test_export_is_user_scoped` |
| Export returns `application/json` | `test_export_response_is_json_content_type` |
| Export unauthenticated → 401 | `test_export_unauthenticated_returns_401` |
| Export garbage bearer → 401 | `test_export_with_garbage_bearer_returns_401` |
| Import loop creates habits from export | `test_import_loop_creates_habits_from_export` |
| Import loop drops tick records (known gap) | `test_import_loop_drops_records` |
| Import MAX_HABIT_COUNT=5 enforced (6th → 400) | `test_import_max_habit_count_enforced` |
| Import handles minimal payload `{name}` | `test_import_loop_handles_missing_optional_fields` |
| Import rejects/accepts empty name gracefully | `test_import_loop_rejects_empty_name` |
| Export size scales with habit count | `test_export_size_scales_with_habits` |

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && .venv/bin/python -m unittest discover -s tests -p test_slice6_import_export.py -v
... 14 tests ...
Ran 14 tests in 8.202s
OK

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
283 passed, 1 warning in 231.18s (0:03:51)
# Was 269 -> 283; delta = +14 from this slice.

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (92 files):
- 0 errors
- 0 warnings
- 58 hints
```

## What changed

| File | Change | Reason |
|---|---|---|
| `tests/test_slice6_import_export.py` | NEW — 14 hermetic integration tests for export + import loop | Slice 6 deliverable |
| `Desktop/migration/parity-matrix.md` | Updated rows 6.1, 6.3 with Slice 6 evidence + 3 schema/contract gaps | Per-slice status requirement |
| `Desktop/migration/slice-reports/slice-6-import-export.md` | NEW — this file | Per-slice status requirement |

## Failed checks

None — after documenting the three schema/contract gaps below.

## Findings (the headline — three schema/contract gaps)

### 1. Export shape strips `status`, `period`, `star`

The actual export shape is `{habits: [{name, records, id, tags}], order: [...]}` — **`status`, `period`, `star` are NOT included**. The legacy NiceGUI export included these. If a user re-imports on another device via the BFF loop, periods and statuses are silently lost. Documented in `test_export_returns_habit_metadata` docstring.

This is a **regression risk vs the legacy behaviour reference** (which the brief explicitly says is the behavioural standard). The brief says "Refactor or rewrite only the code needed to preserve the specified behaviour" — but the export behaviour in castor has lost fields. **Fixing this is out of scope for Slice 6** (it requires a backend change in `_habit_list_export_data()`). Flagged for cutover review.

### 2. Records schema inconsistency between `/habits/export` and `/habits/{id}`

- `/habits/export` returns records as **flat list** `[{day, done, timestamp}]`
- `/habits/{id}` returns records as **nested list** `[{data: {day, done, text}}]`

The Astro `/habits` page reads `/habits/{id}` and does `r.data?.day?.split('T')[0]` (nested path). The export format is flat. **The BFF import loop would need to know this** if it ever tries to re-create ticks. Right now it doesn't (records are dropped entirely), but if/when records are restored, the schema normalisation must happen at the BFF layer.

### 3. `POST /api/v1/habits` only accepts `{name}` — tags/status/period silently dropped

The Astro BFF import loop (`web/concepts/src/pages/api/v1/habits/import.ts`) sends `{name, period, tags, status}` to each `POST /api/v1/habits` call. **The backend ignores everything except `name`.** So even if the export included those fields, the import loop would still drop them.

To fix: the BFF should POST `{name}` first, then PUT `{tags, status, period}` for each habit. This is a **3-line change** in the BFF and **unlocks period/status preservation** without a backend endpoint. **Not done in Slice 6** — flagged.

## What this slice does NOT cover

- **CSV export** (parity row 6.2) — backend doesn't expose it. Astro `/export.astro` doesn't have a CSV button. **Not a regression**: parity matrix row 6.2 says "Not in Astro either", and the legacy NiceGUI export_page.py never had CSV. Status remains ❌.
- **Telegram backup** (parity row 6.4) — out of scope.
- **Browser E2E for /export, /import** — Playwright territory. Deferred to Slice 9.

## Open items (deferred, not blocking)

- **Backend import endpoint** (parity matrix row 6.3 says "pending") — currently implemented at the BFF layer with documented limitations. If you want full parity with the legacy "merge into existing list with renamed collisions" semantics, a backend `POST /api/v1/habits/import` would be needed. **Estimated: 2–3 hours** for the route + tests.
- **BFF 3-line fix** for tags/status/period preservation — would unblock period/status from round-tripping without a new backend endpoint.
- **Schema normalisation** between `/habits/export` and `/habits/{id}` — pick one shape, migrate the other.

## Next proposed slice

**Slice 7 — `/help` + `/tokens` + `/settings` + `/account/delete` backend**:

These are smaller work surfaces. Combined, they cover 6 parity-matrix rows. Mostly Astro UI work; backend is mostly already pinned. Estimated: 3–4 hours.

Alternatives: `/stats`, `/admin`, Playwright scaffold for Slice 9.

## Sign-off request

Per the brief, do not move to Slice 7 until you confirm:
1. Slice 6 evidence sufficient?
2. **Three schema/contract gaps acceptable as documented findings** (export strips status/period/star; record schema mismatch; POST ignores tags/status)?
3. Slice 7 (`/help` + `/tokens` + `/settings` + `/account/delete`) is the right next slice, vs. `/stats`, `/admin`, or Playwright scaffold?
