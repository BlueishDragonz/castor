# Slice 24d — Circles member shares own habit with a circle they're in

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: dogfood-2026-09-25 finding E — after joining a circle, the
invitee is asked if they'd like to share any of their habits. Pre-24d the
welcome page's POST went to the owner-only `/api/v1/circles/{id}/habits`
endpoint and 403'd.

## Scope

Before slice 24d, only the circle owner could share a habit. New members
joining a circle had no way to share their own habits back, so the
"share your habits" wizard on `/circles/[id]/welcome` was effectively
dead-end (404 if they tried sharing their own habit, 403 if the API
existed and required owner).

Slice 24d extends the share model so any circle member can share a habit
that lives in their own HabitListModel, while preserving the owner's
ability to share their own habits through the existing endpoint.

## Backend changes

| File | Change |
| --- | --- |
| `castor/app/circles.py` | `CircleHabit` unique constraint went from `(circle_id, habit_id)` to `(circle_id, habit_id, owner_user_id)` so two members can each share a habit with the same string id. |
| `castor/app/db.py` | `create_db_and_tables` now drops + recreates `circle_habit_unique` as a 3-column unique index on every startup. Idempotent — safe to run on fresh and migrated DBs. |
| `castor/app/circle_routes.py` | New `_require_member` helper. New endpoints: `GET/POST /{circle_id}/members/me/habits`, `DELETE /{circle_id}/members/me/habits/{habit_id}`. `GET /{circle_id}` now returns `owner_user_id` per shared habit. `GET /{circle_id}/feed` iterates per-owner, groups by `owner_user_id`, fetches each owner's habit list once (avoids N+1). |

### Endpoint contracts

```
POST   /api/v1/circles/{id}/members/me/habits
       body: { habit_id, visibility }
       auth: any member of the circle (incl. owner)
       201  { habit_id, visibility }
       403  if not a member of the circle
       404  if the habit isn't in the caller's HabitListModel
       200  if updating an existing share (idempotent upsert)

GET    /api/v1/circles/{id}/members/me/habits
       auth: any member of the circle
       200  [{ habit_id, visibility }, ...] — caller's shares only

DELETE /api/v1/circles/{id}/members/me/habits/{habit_id}
       auth: any member of the circle
       204  if a row was removed
       404  if the caller hasn't shared that habit in this circle
```

## Frontend changes

| File | Change |
| --- | --- |
| `web/concepts/src/pages/circles/[id]/welcome.astro` | Server-side fetch `/members/me/habits` → `mySharedHabits[]`. `alreadyShared` filter now combines `circle.shared_habits` + `mySharedHabits`. `mySharedCount` is now `mySharedHabits.length`, not the previous `0` placeholder. POST URL in the wizard now points at `/members/me/habits` (was `/habits`, owner-only). |

## Tests (`tests/test_slice24d_member_shares.py`, 9 tests)

| Test | Pins |
| --- | --- |
| `test_member_can_share_own_habit` | A new member can share a habit from their own HabitListModel. |
| `test_member_cannot_share_owners_habit` | Sharing a habit id the caller doesn't own returns 404 (not silent). |
| `test_list_my_shared_habits_returns_only_mine` | `GET /members/me/habits` returns only the caller's shares — alice's "Run" doesn't show in bob's list. |
| `test_unshare_my_habit_removes_only_my_row` | DELETE removes only the caller's row; the owner's row in the same circle survives. |
| `test_non_member_cannot_share_into_circle` | A registered user not in the circle gets 403. |
| `test_owner_can_also_use_member_endpoint` | The owner is auto-seeded as a member, so the member endpoint works for them too. |
| `test_shared_habits_response_includes_owner_user_id` | `GET /{id}.shared_habits[]` now carries `owner_user_id` so the welcome page can tell owner's vs member's. |
| `test_feed_returns_per_owner_entries` | `GET /{id}/feed` returns one entry per shared habit, each with its actual `owner_email`. |
| `test_two_members_same_habit_id_no_collision` | Two members with a habit of the same string id (e.g. `'shared_id'`) can both share it — the new 3-column unique index permits it; the old 2-column constraint would have raised `IntegrityError`. |

## Verification

- `astro check`: 0 errors / 0 warnings / 58 hints (106 files).
- `pytest tests/ --ignore=tests/test_batch4_live.py`: **392 passed, 12 skipped** (baseline 383 + 9 new slice-24d tests, 0 regressions).

## Migration notes

`create_db_and_tables` runs `DROP INDEX IF EXISTS circle_habit_unique`
followed by `CREATE UNIQUE INDEX IF NOT EXISTS circle_habit_unique ON
circle_habit (circle_id, habit_id, owner_user_id)`. This is idempotent
and applies to both fresh DBs (`create_all` creates a 3-column unique
constraint named `circle_habit_unique`; the DROP+CREATE then runs as a
no-op redefinition) and to pre-24d DBs (drops the old 2-column index,
creates the 3-column one).

If a real (non-SQLite) DB has rows that violate the new constraint,
the CREATE will fail. On the dev SQLite and on apollo's production
SQLite, no such rows exist because pre-24d only one user per circle
(the owner) could ever insert into `circle_habit`.

## Still open toward the standing goal

- The "What you've shared" sidebar inside `/circles/[id]` (the OWNER's
  view of their circle) doesn't yet list per-member shares. The
  `/feed` endpoint now returns per-owner entries, but the
  `/circles/[id]/index.astro` page renders only `shared_habits` (from
  `GET /{id}`), not the feed. That's a ~10-line Astro follow-up:
  fetch `/feed` in addition to `/{id}` and render a "Members who've
  shared their habits" section. Out of scope here.
- The cutover image tag (`docker-compose.yml:8` is still
  `castor:custom-2026-09-XX`) and SMTP provider credentials on apollo
  remain the two blockers before flipping DNS.
