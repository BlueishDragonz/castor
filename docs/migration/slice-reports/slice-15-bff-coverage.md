# Slice 15 — Astro BFF bridge for client-side routes + coverage audit

**Branch**: `release/astro-migration` (parent `a26febc`)

**Date**: 2026-09-24

## Trigger

The user's instinct to inspect the demo caught **2 real client-side BFF
gaps** plus 1 architectural smell — the slice tests passed because
they hit the FastAPI endpoint directly via httpx, never via the
Astro BFF path.

## What was actually broken (vs. inspection report)

The inspection report initially listed 5 missing BFF routes. After
manual analysis:

| Reported gap | Reality | Resolution |
|---|---|---|
| `/api/v1/user-configs` | SettingsClient PUTs to this from browser. **Real bug.** | Fixed — added BFF proxy. |
| `/api/v1/tokens` | `/tokens` page forms submit to this from browser. **Real bug.** | Fixed — added BFF proxies + rotated tokens.astro form actions to relative paths. |
| `/api/v1/admin/users` | `/admin` page calls this via server-side `backendFetch()` (Node → backend). No browser-context caller. | **No fix needed**; defensive BFF proxy still added. |
| `/api/v1/circles/*` | All `/circles/*` Astro pages use server-side `backendFetch()`. No browser-context caller. | **No fix needed**; no proxies added (would be dead code). |
| `/api/v1/habits/meta` | `/habits/order.astro` calls via server-side `backendFetch()`. No browser-context caller. | **No fix needed**; defensive BFF proxy still added. |

**Two real client-side bugs**, not five. The inspection's `curl
-H "Authorization: Bearer *** /api/v1/circles` test from the laptop
triggered 404s, but those 404s only matter if a browser makes that
call. The actual /circles Astro pages use server-side fetches.

## Architectural smell fixed

`tokens.astro` previously submitted HTML forms to `${BACKEND}/api/v1/tokens`
(a literal `localhost:8085` URL). This:
- leaked the backend origin to the browser,
- failed silently because cookies set by Astro on `:4321` are NOT
  sent to `:8085` (different origin → 401),
- breaks in production where there's only one ingress.

Fixed by:
1. Adding BFF proxies at `/api/v1/tokens` and `/api/v1/tokens/rotate`.
2. Updating `tokens.astro` form `action` attributes to relative paths.
3. Updating the page's initial-load fetch to use `backendFetch()`.

## Files added

- `web/concepts/src/pages/api/v1/user-configs.ts` — GET + PUT
- `web/concepts/src/pages/api/v1/tokens/index.ts` — GET + POST (create/revoke)
- `web/concepts/src/pages/api/v1/tokens/rotate.ts` — POST
- `web/concepts/src/pages/api/v1/admin/users.ts` — GET (defensive)
- `web/concepts/src/pages/api/v1/habits/meta.ts` — GET + PUT (defensive)
- `Desktop/migration/scripts/audit_bff_coverage.py` — coverage audit

## Files modified

- `web/concepts/src/pages/tokens.astro` — relative form actions,
  `backendFetch` for initial load, removed dead `BACKEND` constant
  (kept only for the curl example).

## New audit script: `audit_bff_coverage.py`

Re-runnable script that:

1. Greps the backend source for `@api_router.{verb}("path")` decorators
   to enumerate every registered `/api/v1/*` route.
2. Walks `web/concepts/src/pages/api/**/*.ts` to enumerate every Astro
   BFF file + the HTTP methods it exports.
3. Greps `pages/`, `components/`, `layouts/` for `fetch(\`/api/v1/...\`)`
   patterns to find browser-context callers (template literals
   preserve `${param}` shape).
4. Flags mismatches with three categories:
   - **Client-side caller without BFF** — the actual runtime bug
     (exit code 1 if any).
   - **Backend route without BFF** — likely server-side only; listed
     for visibility, not as a bug.
   - **Backend route with BFF** — the working set.

```
$ python3 Desktop/migration/scripts/audit_bff_coverage.py
# Client-side callers of /api/v1/* without an Astro BFF proxy:
  (none)

# Backend routes WITHOUT an Astro BFF proxy (likely server-side only):
  DELETE /api/v1/account
  GET    /api/v1/habits/{habit_id}
  PUT    /api/v1/habits/{habit_id}
  DELETE /api/v1/habits/{habit_id}
  GET    /api/v1/habits/{habit_id}/completions
  POST   /api/v1/habits/{habit_id}/completions
  ... (+ circles/* and admin/* which the script handles via shape matching)
```

The shape-matching approach (compare `(literal|param)` tuples per URL
segment) was added during slice 15 to handle Astro `[id]` ↔ JS
`${habitId}` param-name mismatches. Documented in the script's
inline comments.

## End-to-end verification (in browser)

| Action | Result |
|---|---|
| Settings page → toggle "Show streak badge" | PUT `/api/v1/user-configs` → **200**, `{"show_streak": true}` persisted in DB |
| /tokens → Create token | POST → **303** redirect; DB shows 1 token row |
| /tokens → Rotate | POST `/api/v1/tokens/rotate` → **303**; new masked token displayed |
| /tokens → Revoke | POST `/api/v1/tokens?_method=DELETE` → **303**; DB token count = 0 |

## Verification

- `pnpm exec astro check`: 0 errors / 0 warnings / 60 hints
- `python3 Desktop/migration/scripts/audit_bff_coverage.py`: 0 client-side bugs
- `python3 Desktop/migration/scripts/audit_parity_matrix.py`: 0 stale-evidence rows
- Full pytest suite: 368 passed + 12 skipped (unchanged — no Python changes)
- Browser inspection: settings/tokens persistence flows verified end-to-end

## Phase-4 follow-ups (unchanged)

- NUMERIC-affinity SQLAlchemy+aiosqlite audit-record silent drop (slice 11 finding)
- `/admin/users` pagination (slice 8 follow-up)
- `prune_audit_events` retention config (parity 12.4)
- Display card checkbox visual styling (shadcn `Checkbox` component not installed)
- Long-press helper extraction (slice 14 audit found it's inline — refactor optional)
