# Slice 10 — /habits sort menu — Status Report

**Branch**: `release/astro-migration` (parent of Slice 10-alt commit)
**Date**: 2026-09-24
**Scope**: Astro UI work for parity row 2.10 (sort habits). Backend was added in Slice 9-alt (`GET /api/v1/habits?order_by=`); this slice adds the sort `<select>` in the `/habits` page header.

---

## What changed

### Sort menu in `/habits` page header (`web/concepts/src/pages/habits/index.astro`)
- Reads `?order_by=...` from the URL on page load
- Validates against the canonical enum names: `manual`, `name`, `category`
- Invalid values fall back to `manual`
- Passes the value to the backend fetch via the `?order_by=` query param
- Renders a `<select>` next to the "Habits" heading. On change, navigates to `/habits?order_by=<value>` (or `/habits` for manual), reloading the page server-side.

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (94 files):
- 0 errors
- 0 warnings
- 59 hints
```

The backend contract is pinned by `tests/test_slice9alt_realtime.py::Slice9AltSortTests` (4 tests). UI verification is visual / via Playwright (Slice 9 deferred).

## What changed (file diff)

| File | Change |
|---|---|
| `web/concepts/src/pages/habits/index.astro` | +24 / −2: URL query reading + sort `<select>` in the page header + `?order_by=` forwarded to the backend fetch. |
| `Desktop/migration/parity-matrix.md` | row 2.10 updated: status ✅ (was ✅ backend / 🟡 UI). |

## Slice 10 findings worth noting

### Finding #1 — `onchange` with a fallback is fragile
The current onchange uses `window.location.search = this.value ? '?order_by=' + this.value : window.location.pathname`. This loses any existing query params (e.g., tag filters). A more robust version would build the URLSearchParams fresh:
```js
const params = new URLSearchParams(window.location.search);
if (this.value) params.set('order_by', this.value);
else params.delete('order_by');
window.location.search = params.toString();
```
Out of scope for this slice but worth noting. The current behaviour is fine for `/habits` because the page doesn't take other query params (unlike `/stats` which Slice 8 added date range to).

### Finding #2 — Sort doesn't survive a refresh that doesn't include the query
The sort is URL-state only (no localStorage / no cookie). If a user picks "Name" then opens `/habits` in a new tab, it's manual again. The legacy NiceGUI app persisted sort via `app.storage.user`. The parity matrix row 2.10 says "Updates habit_list.order_by" — that's a backend persistence path that doesn't exist yet (`PUT /api/v1/habits/meta` order_by is TODO). Slice 10 documents this; persistence is its own slice.

## Out of scope (still)

- **Sort persistence across sessions** — parity matrix says "Updates habit_list.order_by"; needs `PUT /api/v1/habits/meta`. Out of scope.
- **Browser E2E for sort menu** — Playwright slice territory.
- **Sort indicator on the column header** — nice-to-have; not in the parity matrix.

## Next proposed slice

**Slice 11 — `/admin` users-list pagination + audit-event reconciliation**:

  - `GET /api/v1/admin/users?limit=&offset=` (parity row 7.1)
  - Verify all audited actions emit events (parity rows 4.x, 5.x, 6.x)
  - Emit the missing ones (`recovery_email_request`, etc.)
  - Estimated 3–4 hours.

Alternative: **`PUT /api/v1/habits/meta` order_by persistence** (parity row 2.10 completeness). Estimated 1–2 hours.

## Sign-off request

Per the brief, do not move to Slice 11 until you confirm:
1. Slice 10 evidence sufficient (UI renders, sort menu works, parity 2.10 ✅)?
2. URL-state-only sort acceptable (no persistence across sessions)?
3. Slice 11 = admin pagination + audit reconciliation — or the persistence slice?
