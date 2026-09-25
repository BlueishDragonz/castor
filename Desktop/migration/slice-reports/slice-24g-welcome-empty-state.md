# Slice 24g — Welcome page: distinguish "no habits" from "all shared"

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: Final dogfood walk as part of the standing-goal verification.
After carol joined "Morning run crew" and shared her only habit
("Yoga"), the welcome page rendered:

  - "What you've shared" header: **1** of your habit shared with this
    circle so far. (correct — slice 24d's `mySharedHabits.length`).
  - Below it, the empty-state card: **"You don't have any habits to
    share yet."** (wrong — she has 1 habit, just shared).

The empty-state was gated on `myHabits.length === 0` only — and
`myHabits` is `allHabits.filter(h => !alreadyShared.has(h.id))`,
so once all habits are shared, `myHabits` is empty and the
"no habits yet" copy shows. Two distinct states collapsed into
one.

## Frontend change

`web/concepts/src/pages/circles/[id]/welcome.astro`:

  - Frontmatter: derive two booleans alongside `myHabits`:
    - `allHabitsEmpty` = `allHabits.length === 0` (genuine no-habits
      state)
    - `allShared` = `allHabits.length > 0 && myHabits.length === 0`
      (has habits, all already shared)
  - The empty-state now splits into two branches:
    - `allHabitsEmpty` → "You don't have any habits to share yet.
      Create one from the new habit page and come back to share it."
      (original copy, still correct for the genuine-no-habits case)
    - `allShared` → "You've shared all {n} habit(s) with this circle.
      You can revisit your visibility choices or remove a share from
      the circle page any time." (new copy, links to the circle page
      where carol can use the slice-24e trash button to un-share)

## Backend changes

None. Slice 24g is purely a frontend copy/conditional fix.

## Dogfood trace

**Dave (no habits) on `/circles/2/welcome`:**

  - "What you've shared": **0** of your habits shared with this
    circle so far.
  - Empty-state: "You don't have any habits to share yet. Create one
    from the new habit page and come back to share it." ← original
    copy, correct branch.

**Carol (one habit, just shared) on `/circles/2/welcome`:**

  - "What you've shared": **1** of your habit shared with this
    circle so far.
  - Empty-state: "You've shared all your habit with this circle.
    You can revisit your visibility choices or remove a share from
    the circle page any time." ← new branch, honest copy.

DOM scrape with `document.querySelectorAll('main [class*="text-muted-foreground"]')`
confirmed both branches render the correct text.

## Verification

- `astro check`: 0 errors / 0 warnings / 58 hints (107 files).
- `pytest tests/test_slice23a_circles_backend.py
  tests/test_slice23c_circles_email.py tests/test_slice24d_member_shares.py`:
  **24 passed** (no regressions).
- Live browser walk as both dave (no habits) and carol (all habits
  shared) confirms each branch renders the right copy.
