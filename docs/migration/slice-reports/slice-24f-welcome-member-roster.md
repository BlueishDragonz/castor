# Slice 24f — Welcome page: invitee sees the full member roster

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: After slice 24d + 24e the welcome page answered
"what you can see" and "what you've shared back" — but didn't show
the new member WHO ELSE was in the circle. The standing goal says
the new member should "confirm they've joined the circle with a
summary of what they can see (what is shared with them) and what
they shared with the inviter (if they selected any habits)".
That privacy picture is incomplete without naming the other people
who will see what the new member shares.

Slice 24f closes that gap with a single new card.

## Frontend change

`web/concepts/src/pages/circles/[id]/welcome.astro`:

  - Frontmatter: derive `otherMembers = circle.members.filter(m =>
    m.email !== Astro.locals.session?.email)`.
  - New `<Card>` "Who else is in this circle" between "What you can
    see" and "What you've shared". Each row shows the member's
    email, a crown SVG next to it if `m.is_owner`, and an "owner"
    label.
  - Uses the same lucide-react `<Crown>` component as the index page
    (`text-amber-500`, `aria-hidden`), so the visual treatment
    matches the owner-badging elsewhere in the app.

The card only renders if `otherMembers.length > 0`, which is the
common case (the owner is always present, so a single-member circle
skips the card since `otherMembers` would be empty).

## Backend changes

None. `circle.members[]` already carries `user_id`, `email`,
`is_owner`, and `joined_at`; the welcome page server-side already
fetches it via `GET /api/v1/circles/{id}`.

## Dogfood trace

Visited `/circles/2/welcome?token=...` as alice (the new member):

```
You joined Family wellness
  Welcome! Here's a quick summary of what you can see and what
  you can share.

  What you can see
    1 habit owned by demo@castor.example.com shared with this circle.
      - Drink water   Ticks + streak

  Who else is in this circle                       ← slice 24f
    When you share a habit with this circle, these people
    will be able to see it according to the visibility you pick.
      👑 demo@castor.example.com  owner

  What you've shared
    0 of your habits shared with this circle so far.
    You don't have any habits to share yet. Create one from
    the new habit page and come back to share it.

  Ready to see the circle?
    [Go to circle]
```

The crown icon renders the same lucide-react component the index
page uses; `browser_console` DOM scrape confirms `svgCount: 1` on
demo's row, `0` on alice's — matches `m.is_owner` flags.

## Verification

- `astro check`: 0 errors / 0 warnings / 58 hints (107 files).
- `pytest tests/test_slice23a_circles_backend.py
  tests/test_slice23c_circles_email.py tests/test_slice24d_member_shares.py`:
  **24 passed** (no regressions; slice 24f is frontend-only).
- Browser walk: confirmed via text snapshot + DOM scrape that the
  card renders, the crown renders, the privacy-reassurance copy
  reads correctly.

## What's still missing toward the standing goal

The goal text mentioned the invitee should "confirm they've joined
the circle with a sumary of what they can see (what is shared with
them) and what they shared with the inviter". Slice 24f covers
"who can see what I share" (the privacy transparency half).
The "what you shared with the inviter" half is shown as a count on
"What you've shared" (slice 24d wired this to the real
`mySharedHabits.length` instead of the placeholder 0). The
acceptable privacy picture is now:

  - **What you can see**: demo's shared habit (alice sees demo's
    Drink water)
  - **Who else is in this circle**: demo, the owner (alice knows who
    will see her habits)
  - **What you've shared**: alice's share count (real, not placeholder)

If alice had joined a multi-member circle, the "Who else" card
would list everyone else (owner + any pre-existing members), giving
her a concrete roster before she decides what to share.
