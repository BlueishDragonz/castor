# Slice 23b — Circles frontend (welcome page + copyable invite + a11y)

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: dogfood findings B, D, E, F, G, I

## Changes

### Copyable invite URL after mint (finding B)

`web/concepts/src/pages/circles/[id]/index.astro` now renders the
just-minted invite URL with a **Copy link** button. The token is
shown once via `?new_token=...&new_invite_url=...` query params and
the UI displays a warning that it's the only chance to copy it.

### /register path from invite link (finding D)

`web/concepts/src/pages/circles/[id]/join.astro` redirects
unauthenticated users to `/login?next=/circles/{id}/join?token=...`
preserving the invite context. (Login already handles `?next=` for
post-login redirect.) The login page itself was not modified; the
existing `?next=` handling covers this case.

### Visible error UX (finding E)

`web/concepts/src/components/ErrorBanner.astro` now surfaces
`join_self_blocked` ("You can't accept your own invite to a circle
you own") and `join_failed` ("We couldn't add you to that circle")
as destructive Alerts when the join page redirects with `?err=...`.

The `join.astro` page maps backend statuses to these keys:
- `404` → `join_invalid_token`
- `409` → `join_self_blocked` (covers both self-join blocked and
  already-a-member, both of which produce 409 from the backend)
- `410` → `join_expired`
- anything else → `join_failed`

### Share wizard + join summary (findings F + G)

New page `web/concepts/src/pages/circles/[id]/welcome.astro`. Shown
to a brand-new member right after they accept an invite.

The page:
1. Reads `?token=` from the URL.
2. Fetches the circle detail and the owner's feed so we can show
   "What you can see: X habits owned by Y".
3. If the new member has any habits, shows a wizard:
   - Habit picker (single-select dropdown)
   - Visibility selector (Ticks only / + streak / + notes)
   - "Share" button calls `POST /api/v1/circles/{id}/share`. On
     success, the "What you shared" count updates inline (client-side
     `localStorage` of last-shared habit + a re-fetch of the
     member's habit list).
4. Shows "You joined {name}" + a "Go to circle" CTA linking to
   `/circles/{id}`.

The `/circles/[id]/join.astro` page now redirects to `/welcome?token=...`
on success instead of `/circles/{id}`, so the new member sees the
summary immediately.

### Crown icon a11y leak (finding I)

The decorative crown icon (`lucide-react`'s `Crown`) on
`/circles/{id]` previously had `aria-label="You own this circle"`,
which leaked into the page's h1 text for screen readers (the
accessibility tree read "You own this circle Family wellness" as
the heading). The icon now uses `aria-hidden="true"` so it is
purely decorative; the "owner" badge still appears inline in the
Members list.

## Verification

- `cd web/concepts && pnpm exec astro check` → 0 errors.
- Browser walkthrough on demo@castor.example.com:
  - Owner creates circle, mints invite, sees the copyable link.
  - Self-join blocked (owner mints + immediately tries their own
    token → friendly error on /circles/1?err=join_self_blocked).
  - Welcome page renders the summary correctly.

## Known gaps left for slice 23c

- **Email delivery (dogfood finding A + H)**: not implemented.
  See `slice-23a-circles-backend.md` for the deferred items.
- **/register from invite link (finding D)**: the join page now
  redirects to `/login?next=...` with the full join URL preserved.
  But if the email isn't registered, the user gets a "wrong
  password" error on /login and has to navigate to /register
  manually. A "Create an account instead" link on /login with
  `?next=...` would close this fully — small followup.

## Rollback

Revert commit `b349f96`. The welcome page lives in its own route;
no other page depends on it. The join redirect can be reverted by
changing the last line of `join.astro` back to `/circles/${circleId}`.