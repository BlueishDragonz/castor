# Dogfood Findings — Castor E2E Walkthrough

**Date**: 2026-09-23
**Walkthrough scope**: register → login → habits list → habit detail → settings (light/dark theme) → privacy page → logout
**Environment**: local backend (castor.main:app on 127.0.0.1:8085) + Astro dev (web/concepts on 127.0.0.1:4321) + SQLite at `.user/habits.db`
**Dogfood user**: `dogfood@castor.example.com` (registered fresh; pre-existing `fixture@example.com` left alone)
**Tools used**: text snapshots, browser console JS evaluation, vision_analyze (screenshots cropped/zoomed)

---

## TL;DR

**The migration is not shippable in its current state.** Dogfooding surfaced 5 real defects that the existing 178-test backend suite and 0-error astro check did NOT catch. Some are user-visible regressions vs. the NiceGUI baseline; at least one is a security regression.

| # | Severity | Type | Description |
|---|---|---|---|
| 1 | **CRITICAL** (security) | Logout token-version bump doesn't persist | `/auth/logout` returns 204 but DB `token_version` stays at 0; stolen JWT remains valid after logout. The whole D14 story is broken. |
| 2 | **HIGH** (functional) | Tick marks invisible in non-UTC timezones | `day.toISOString()` returns UTC; record dates are local. Cell comparison fails by 1 day for any user not in UTC. Affects both the habit list AND the detail-page heatmap. |
| 3 | **HIGH** (functional) | Habit detail heatmap is read-only | Heatmap cells only render the button form if `token` prop is truthy; token is the JWT which is in an httpOnly cookie (intentionally, by the migration). Result: zero visible cells in the heatmap for any real user. |
| 4 | **MEDIUM** (UX) | Literal `\n` rendered in DOM | Multiple pages show a stray text node `"\n"` at the top-left, visible to the user. Comes from a client-side mutation (possibly hydration mismatch). |
| 5 | **LOW** (UX) | Add habit / Sign out buttons lack visual emphasis | Rendered as plain text links with no button chrome. |
| 6 | **LOW** (UX) | Habit name "No phone after 22:00" truncates | 120px column width cuts "No phone aft…" |

---

## Walkthrough trace

### Step 1: Register `dogfood@castor.example.com`

- API: `POST /auth/register` returns 201 with new user ID
- Vision: `/login` renders cleanly; form fields visible
- No issues

### Step 2: Login

- API: `POST /auth/login` returns access_token
- Browser: enter email + password + click "Sign in"
- Redirected to `/habits` ✅
- No issues

### Step 3: Create 4 habits via API + tick today with notes via API

- 4 habits created (Drink water, Read 30min, Stretch, No phone after 22:00)
- POST `/api/v1/habits/{id}/completions` with `{date: "23-09-2026", done: true, text: "morning completion via dogfood test"}` returns 200 for all 4
- Subsequent GET `/api/v1/habits/{id}` confirms records exist with `data.day = "2026-09-23"`
- **Bug discovered here**: the page then showed ALL CELLS as empty.

### Step 4: Open `/habits` in browser

- **Bug #2 confirmed**: All 7 cells × 4 habits show "Not done" despite the API confirming records.
- Root cause: Astro HabitGrid uses `day.toISOString().split('T')[0]` for cell comparison. In BST (UTC+1), `new Date()` for local Sep 23 00:00 = UTC Sep 22 23:00, so `toISOString()` returns `"2026-09-22"`. Records store `"2026-09-23"`. No match → cell appears empty.
- **Bug #4 confirmed**: A literal `\n` text node is `document.body.firstChild`, visible in the top-left of every page.
- **Bug #6 confirmed**: "No phone aft..." truncation visible.
- Vision revealed: the leftmost checkbox column is significantly larger and darker than the day columns — the "Today" switch has different sizing from the date cells, making the layout feel broken.

### Step 5: Open `/habits/{id}` (Drink water detail)

- Heatmap card visible with column headers (Jun/Jul/Aug/Sept) but NO actual day cells render in the heatmap.
- DOM investigation: cells exist (`<td role="gridcell">`) but have ZERO children. The button form inside is gated on `interactive && habitId && token` — and `token` is undefined because the JWT is in an httpOnly cookie.
- **Bug #3 confirmed**: heatmap is read-only. The interactive mode requires the JWT to be embedded in the page, which the migration correctly disallowed (XSS fix).
- "Best streaks" correctly shows "1d ended 23 Sept" — streak calculation works on backend data.
- "Last year" correctly shows "Sept: 1 completions" — backend aggregation works.
- "Notes" correctly shows "debug log · 23 September 2026 · done" — note text from API is rendered.
- **Bug #2 again**: even though the data is there, the heatmap's `isDone(date)` check uses `date.toISOString().slice(0,10)` which produces the wrong day string.

### Step 6: Settings (light/dark theme)

- `/settings` renders cleanly with all sections (Appearance, Custom CSS, Help, Account, Danger zone).
- Click "Dark" → click "Save theme" → page reloads in dark mode (vision confirmed).
- **Bug #5 confirmed**: "Save theme" button has minimal styling, looks like a plain text link.

### Step 7: Privacy policy

- `/privacy` renders the full content with proper headings, lists, links.
- "← Back to home" link works.
- No issues.

### Step 8: Logout

- Click "Sign out" → redirected to `/login` ✅ (visual)
- **Bug #1 confirmed**: POST `/auth/logout` with the session JWT returns 204 but DB `token_version` stays at 0.
- Re-using the JWT after logout returns 200 from `/users/me` — the stolen-JWT threat model that D14 was supposed to close is open.

---

## Defect detail

### Bug #1 — Logout token-version bump silently fails (CRITICAL, security)

**Where**: `castor/app/auth.py:248-266` (`user_bump_token_version`) called from `castor/app/webauthn_routes.py:586` (logout endpoint).

**Symptom**: `POST /auth/logout` returns 204 but DB column `user.token_version` is unchanged. A JWT issued before logout remains valid after logout (verified by `GET /users/me` with the post-logout JWT returning 200).

**Investigation**:
- Function looks correct: opens session, `UPDATE user SET token_version = token_version + 1 WHERE id = ?`, commits.
- Manual repro via `python -c "..."` raised `sqlalchemy.exc.InvalidRequestError: Instance '<User at 0x...>' is not persistent within this Session` from `session.refresh(user)`. The `refresh` happens against the wrong session because `user` is from `current_active_user` (a different session), not from `get_async_session_context()` inside the function. The UPDATE itself should still commit though.
- Despite the raised exception, the `async with session.begin()` block exited normally — but the transaction was apparently rolled back by the unhandled exception bubbling up. The `await record("logout", ...)` after the bump never runs.

**Why tests didn't catch it**: there is **no test for the `/auth/logout` endpoint**. The function `user_bump_token_version` is only called from one place (the logout endpoint) which has no test.

**Fix**: 
- Remove `await session.refresh(user)` from `user_bump_token_version` (it's unnecessary — the function returns the same `user` object that was passed in).
- The UPDATE will commit when the `session.begin()` block exits.
- Add an integration test: register → login → POST /auth/logout → assert old JWT returns 401 from /users/me.

### Bug #2 — Tick marks invisible in non-UTC timezones (HIGH, functional)

**Where**:
- `web/concepts/src/pages/habits/index.astro` (HabitGrid data flow)
- `web/concepts/src/components/HabitGrid.astro` (cell comparison)
- `web/concepts/src/components/Heatmap.astro` (heatmap cell rendering)

**Symptom**: A tick recorded via the backend (date stored as local YYYY-MM-DD) never appears on the Astro front-end for any user not in UTC.

**Root cause**: 
- The Astro server computes `today = new Date(); today.setHours(0, 0, 0, 0)` which gives local midnight. In BST, that is 23:00 UTC the day before.
- `day.toISOString().split('T')[0]` converts that to UTC date, so for local Sep 23 we get `"2026-09-22"`.
- The backend stores `data.day = "2026-09-23"` (the local date I sent in `23-09-2026`).
- The comparison `tickedDays.includes(iso)` returns false.

**Why tests didn't catch it**: the test environment is presumably UTC (CI runners typically are); the dev environment is local BST but tests use direct DB inspection rather than the rendered page.

**Fix**: Replace `day.toISOString().split('T')[0]` with a local-date formatter:
```ts
const y = day.getFullYear();
const m = String(day.getMonth() + 1).padStart(2, '0');
const d = String(day.getDate()).padStart(2, '0');
const iso = `${y}-${m}-${d}`;
```
Apply this in HabitGrid, Heatmap, and any other place that compares local dates.

### Bug #3 — Heatmap is read-only (HIGH, functional)

**Where**: `web/concepts/src/components/Heatmap.astro:110`

**Symptom**: Every cell in the habit detail page heatmap has zero children — the clickable button form is gated on `interactive && habitId && token`. `token` is undefined because the JWT is in an httpOnly cookie that the front-end cannot read (correctly, per the XSS fix in P1).

**Why this is broken**: the Heatmap was designed assuming the JWT could be embedded as a hidden form field. The migration correctly removed all such embed patterns (`__HABIT_TOKEN__`, `__REORDER_TOKEN__`, etc.), but the Heatmap was not updated to use the new server-side POST pattern.

**Fix**: The heatmap form should POST to `/habits/{id}/complete` (the Astro BFF route that forwards to the backend with the httpOnly cookie), not to `/api/v1/habits/{id}/completions` directly. Drop the `token` prop entirely; the form action goes through Astro and the cookie handles auth.

### Bug #4 — Literal `\n` text node in DOM (MEDIUM, UX)

**Where**: Document body first-child on every page.

**Symptom**: `document.body.firstChild` is a text node containing literal characters `\` and `n`. The server-rendered HTML does NOT contain this; it appears after client-side hydration.

**Investigation**:
- Fetched `/habits` via `fetch()` and inspected raw HTML — body opens with `<body class="..."><main...>`; no `\n`.
- DOM shows the text node AFTER page load → client-side JS is appending it.
- Suspects: hydration mismatch in `BottomNav` or `DesktopMenu` (both `client:load`). Both are React islands that may be inserting leading whitespace.

**Fix**: Likely needs a `<slot />` adjustment in Layout or trimming of template whitespace in the React components. Pending investigation with dev-server logs.

### Bug #5 — Add habit / Sign out buttons lack visual emphasis (LOW, UX)

**Where**: `web/concepts/src/pages/habits/index.astro:144-151`, `web/concepts/src/pages/settings.astro`

**Symptom**: Buttons render as plain text with no chrome. Vision described them as "reads as a plain link rather than a primary action".

**Fix**: Wrap with shadcn `<Button>` consistently. The `<a href="/habits/new">+ Add habit</a>` should be `<Button asChild><a href="/habits/new">+ Add habit</a></Button>`.

### Bug #6 — Habit name truncation (LOW, UX)

**Where**: `web/concepts/src/components/HabitGrid.astro` (habit name column width 120px)

**Fix**: Increase column width on desktop or allow wrap; minor.

---

## What DID work

Despite the bugs above, many things function correctly:
- Registration + login + cookie session
- Dark theme switch (server-side save, cookie-based theme)
- `/privacy` and `/terms` public pages
- `/settings` rendering + sign-out redirect
- Habit creation via API
- Tick creation via API (records stored with notes)
- Streak calculation (backend)
- Year history aggregation (backend)
- Note display on detail page (text + date)
- Backend FastAPI routes registered correctly (15 routes)
- py_compile on 10+ files: all OK
- pytest: 178 passed

---

## Recommendations

1. **Do not declare the migration complete.** These bugs would be discovered by any real user on day one. The "READY TO MERGE" verdict in the final report was premature.

2. **Critical path before re-declaring complete**:
   - Fix Bug #1 (logout token-version bump) + add integration test
   - Fix Bug #2 (timezone) + add a unit test that constructs dates in non-UTC and verifies comparison
   - Fix Bug #3 (heatmap interactivity) by routing through Astro BFF
   - Fix Bug #4 (stray `\n` text node) — even if it's purely cosmetic

3. **Quality gap exposed**: the test suite (178 backend pytests + 0-error astro check) does not exercise the rendered Astro front-end. Adding a small smoke test (Puppeteer or Playwright) that registers, creates a habit, ticks it, and asserts the rendered DOM contains a checked cell would have caught Bugs #2 and #3 immediately.

4. **Update the final report** §8 conclusion to remove "ready to merge" and add a "Discovered during dogfood" subsection listing the regressions.
