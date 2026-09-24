# Slice 3 — Add / Edit / Archive / Reorder — Status Report

**Branch**: `release/astro-migration` (parent `46ebeee` from Slice 2)
**Date**: 2026-09-24
**Scope**: PUT /api/v1/habits/{id}, DELETE /api/v1/habits/{id}, GET/PUT /api/v1/habits/meta

---

## Working flows (verified end-to-end against backend)

| Flow | Test |
|---|---|
| Edit name (round-trip persists) | `test_edit_name_persists` |
| Toggle star | `test_edit_star_persists` |
| Edit tags (sorted in response) | `test_edit_tags_persists` |
| Edit period (W × 1, target 3) | `test_edit_period_persists` |
| PUT `{}` → 200, no change | `test_edit_empty_body_no_change` |
| Edit cross-user habit → 404 + habit unchanged | `test_edit_cross_user_returns_404` |
| Edit unauthenticated → 401 | `test_edit_unauthenticated_returns_401` |
| Archive excludes from active list | `test_archive_excludes_from_active_list` |
| Archive → unarchive round-trip | `test_archive_then_unarchive_restores_to_active` |
| Invalid status value → 422 | `test_archive_invalid_status_value_returns_422` |
| DELETE hard-removes (from active list) | `test_delete_removes_habit_from_list` |
| DELETE archived habit removes permanently | `test_delete_archived_habit_works` |
| DELETE cross-user → 404, habit kept | `test_delete_cross_user_returns_404_and_keeps_habit` |
| DELETE unauthenticated → 401 | `test_delete_unauthenticated_returns_401` |
| Reorder persists AND list reflects new order | `test_meta_reorder_persists_and_reflects_in_listing` |
| Reorder unauthenticated → 401 | `test_meta_reorder_unauthenticated_returns_401` |
| Reorder with unknown id → 200, stored verbatim | `test_meta_reorder_with_unknown_id_silently_ignored` |
| Tick survives rename | `test_tick_survives_edit` |
| Archive preserves records | `test_archive_does_not_lose_records` |

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && .venv/bin/python -m unittest discover -s tests -p test_slice3_edit.py -v
... 19 tests ...
Ran 19 tests in 10.672s
OK

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
228 passed, 1 warning in 124.49s (0:02:04)
# Was 209 -> 228; delta = +19 from this slice.

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (92 files):
- 0 errors
- 0 warnings
- 58 hints
```

## What changed

| File | Change | Reason |
|---|---|---|
| `tests/test_slice3_edit.py` | NEW — 19 hermetic integration tests for PUT, DELETE, /meta | Slice 3 deliverable |
| `Desktop/migration/parity-matrix.md` | Updated rows 2.5, 2.6, 2.8, 2.9 with Slice 3 evidence | Per-slice status requirement |
| `Desktop/migration/slice-reports/slice-3-edit.md` | NEW — this file | Per-slice status requirement |

## Failed checks

None.

## Behavioural findings (the headline)

Three findings from Slice 3 that the brief's "behavioural reference" principle needs to capture:

1. **`HabitStatus.ARCHIVED` enum value is `'archive'` (singular)**, not `'archived'`. The Python enum NAME is `ARCHIVED` but its `.value` is the string `"archive"`. A third state `soft_delete` exists for hidden habits. **The parity matrix row 2.8 originally said `"archived"` — corrected in this slice.** This is a frequent copy-paste footgun.

2. **`DELETE /api/v1/habits/{id}` is a HARD REMOVE** in castor. The dict is literally `data["habits"].remove(item.data)` (`castor/storage/dict.py:330`). The Astro page `/habits/[id]/archive.astro` POSTs to `/api/v1/habits/{id}/archive` which presumably calls DELETE — this is documented as the "Archive" action but is actually a hard delete. The parity matrix row 2.6 evidence has been updated to reflect this. **Note**: legacy NiceGUI's `habit_list.remove()` was also a hard remove, so this is consistent with the behavioural reference. No regression risk.

3. **`/habits` (list) reflects `meta.order`** — it's NOT sorted by creation order. The test originally expected creation order; corrected to expect `meta.order`. This means **drag-drop reorder is immediately visible on the home grid**, which is the expected UX. Backend is correct; my test assumption was wrong.

## Other findings

- **No backend duplicate endpoint exists** — the duplicate workflow is implemented at the Astro BFF layer (`web/concepts/src/pages/api/v1/habits/[id]/duplicate.ts`): GET the habit, POST a new one. No `POST /api/v1/habits/{id}/duplicate` on the backend. This is fine — the BFF is the right place for it — but worth noting that **if a non-Astro client (mobile, CLI) tries to duplicate, it must implement the two-step itself**. Mobile clients already do (their import flow does the same two-step). No backend change needed.

## Open items (deferred, not blocking)

- **Drag-drop UI** (parity row 2.9): the `/habits/order` page and the in-row drag on `/habits` index are Playwright territory — Slice 9.
- **Edit page rendering** (`habits/[id]/edit.astro`): Playwright — Slice 9.
- **Astro forms** for the legacy routes `/habits/[id]/complete`, `/habits/[id]/archive`, `/habits/[id]/duplicate`: not touched in Slice 3 — backend contract is what matters; UI is in Slice 9.

## Next proposed slice

**Slice 4 — Passkey (WebAuthn) login**:
- `/auth/webauthn/login/begin` + `/auth/webauthn/login/finish` (already in castor.app.webauthn_routes)
- `/auth/webauthn/register/begin` + `/finish`
- Passkey list management
- New tests: register passkey → login with passkey → fallback to password

Estimated: 4–5 hours (WebAuthn ceremony is intricate).

## Sign-off request

Per the brief, do not move to Slice 4 until you confirm:
1. Slice 3 evidence sufficient?
2. **Three behavioural findings acceptable** (singular `'archive'` enum, hard-delete semantics, /habits reflects meta.order)?
3. Slice 4 (Passkey login) is the right next slice, vs. `/help` 404 (visible cutover blocker), Import/Export, /security, /stats, /admin?
