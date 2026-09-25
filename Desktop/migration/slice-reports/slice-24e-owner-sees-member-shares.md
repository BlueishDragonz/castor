# Slice 24e — Circles: owner sees member shares (per-viewer split)

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: After slice 24d (member-shares-own endpoint), the owner-view
of `/circles/{id}` listed every share with a trash icon — even shares
belonging to members. Two problems:
1. The trash button on a member-shared habit pointed at the owner-only
   `DELETE /api/v1/circles/{id}/habits/{habit_id}` and 403'd when
   clicked, leaving the owner thinking they could remove it.
2. The "Yours / Members" split (committed in slice 24d) was hardcoded
   to `circle.owner_id`, which made the section meaningless for
   member-viewers — alice saw "Yours: Drink water (demo's habit)" and
   "Members: Morning walk (alice's own share)".

Slice 24e fixes both. The split is now per-viewer; both owners and
members see "Yours" as their own shares and "Members" as everyone
else's. Member-side trash posts to the slice-24d member endpoint;
owner-side trash posts to the same member endpoint (the backend's
caller-scoping in `unshare_my_habit` makes the same endpoint safe for
the owner).

## Frontend changes

| File | Change |
| --- | --- |
| `web/concepts/src/lib/circles.ts` | `CircleSharedHabit` gains an optional `owner_user_id` field. |
| `web/concepts/src/pages/circles/[id]/index.astro` | (a) `/feed` is now fetched for both owners and members; was member-only. (b) The split logic uses the **viewer's** user_id (looked up by email in `members[]`, since the cookie only carries email), not `circle.owner_id`. (c) The "Yours" trash posts to the slice-24d member endpoint, which the backend scopes by the caller's user_id. (d) The "Yours" rows display the habit name (looked up from `/feed`) instead of the truncated id; "Members" rows also get the name with attribution. (e) Docstring updated. |
| `web/concepts/src/pages/circles/[id]/members/me/habits/[habit_id]/delete.astro` | **New** — POST-only Astro page that calls `DELETE /api/v1/circles/{id}/members/me/habits/{habit_id}` and redirects to `/circles/{id}?err=unshare_failed` on error. This is the trash-button form action. |

## Backend changes

None — slice 24e is entirely a frontend fix. The slice-24d backend
endpoints already support caller-scoped un-share, so reusing them for
the owner's own shares works without further changes.

## Why not a backend owner-can-unshare-any endpoint?

Two options were considered:

  - **Option A (chosen):** the owner uses the same member endpoint to
    remove their own share. The trash button is hidden on
    member-shared rows, so the owner never tries to remove a row that
    isn't theirs. This keeps the audit story clean (`circle_habit_unshare`
    always means "the caller removed their own row").
  - **Option B:** add `DELETE /api/v1/circles/{id}/habits/{habit_id}`
    to remove *any* share by id. This would let the owner wipe alice's
    share without her consent, which conflicts with the design
    intent of slice 24d (per-member shares are personal contributions,
    not pool entries the circle owner curates).

## Dogfood trace

Visited `/circles/1` twice (once as demo/owner, once as alice/member):

**demo/owner view:**

```
Shared habits (2)
  YOURS
    Drink water  cc4146…  Ticks + streak  [trash]
  MEMBERS
    Morning walk  5309cf…  alice-test@example.com  Ticks + streak

Recent activity (last 14 days)
  Morning walk  (alice-test@example.com)
  Drink water  (demo@castor.example.com)  🔥 21-day streak
  12 13 14 15 16 17 18 19 20 21 22 23 24 25
```

**alice/member view:**

```
Shared habits (2)
  YOURS
    Morning walk  5309cf…  Ticks + streak  [trash]
  MEMBERS
    Drink water  cc4146…  demo@castor.example.com  Ticks + streak
```

Clicked alice's trash → POST `/circles/1/members/me/habits/5309cf/delete`
→ 204 → redirect to `/circles/1` → "Yours" subsection disappears, header
count drops from 2 to 1, Recent activity loses alice's row. Confirmed
via `browser_console` DOM scrape.

## Verification

- `astro check`: 0 errors / 0 warnings / 58 hints (107 files — +1 for the new delete.astro page).
- `pytest tests/test_slice23a_circles_backend.py tests/test_slice23c_circles_email.py tests/test_slice24d_member_shares.py`: **24 passed** (full backend circle surface).
- Live curl + browser walk: confirmed for both viewer perspectives.

## Pitfalls worked through

1. **`use replace_all` accidentally inserted `---` mid-file** in the
   new delete.astro — Astro 7 then complained "expected `---` but
   the file ends" because the second `---` was treated as a frontmatter
   closer with no body. Re-issued the whole file via write_file (after
   reading the full current contents) to recover.
2. **Wrong relative import depth** — `delete.astro` sits 7 directories
   deep from `src/lib/auth.ts`, not 6. Astro check immediately caught it.
3. **Per-viewer split was the right design choice** — initially I made
   the split `sh.owner_user_id === circle.owner_id`, which was correct
   for the owner but wrong for members (alice saw demo's share in
   "Yours"). The fix is `sh.owner_user_id === viewerId` where
   `viewerId = members.find(m => m.email === cookie.email)?.user_id`.
   The cookie carries only `email`, but `members[]` always includes
   the viewer (since `/circles/{id}` 403s if you're not a member), so
   `members` lookup is sufficient — no JWT decoding needed.
