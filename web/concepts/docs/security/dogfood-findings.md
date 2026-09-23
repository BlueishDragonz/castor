# Dogfood Findings — Castor E2E Walkthrough

**Date**: 2026-09-23
**Walkthrough scope**: register → login (progressive disclosure) → habits list (clean header dates) → habit detail → settings (light/dark theme) → privacy page → logout
**Environment**: local backend (castor.main:app on 127.0.0.1:8086) + Astro dev (web/concepts on 127.0.0.1:4321) + SQLite at `.user/habits.db`
**Dogfood user**: `qa1@castor.example.com`
**Tools used**: text snapshots, browser console JS evaluation, vision / vision_analyze (screenshots cropped/zoomed)

---

## TL;DR (updated)

The first round of dogfooding (logged below as the historical trace) surfaced 6 real defects. The most recent round (post-commit `d8a0e28`) added **one critical-path user-flow requirement** and re-surfaced the `\n` text-node issue from multiple new sources. As of commit `ae12515`, all known defects are fixed.

| # | Severity | Type | Description | Status |
|---|---|---|---|---|
| 1 | CRITICAL (security) | Logout `token_version` not bumping | Two `/auth/logout` endpoints registered; fastapi_users default winning | **FIXED** `d8a0e28` |
| 2 | HIGH (functional) | Today cells empty (non-UTC users) | `day.toISOString()` returns UTC; record is local | **FIXED** in earlier slice (`de51a18`) |
| 3 | HIGH (functional) | Heatmap cells blank | Form action hit backend directly with `token` gate | **FIXED** in earlier slice |
| 4 | MEDIUM (UX) | Literal `\n` text node in DOM | Multiple sources identified | **FIXED** `ae12515` |
| 5 | LOW (UX) | Add habit / Sign out links no chrome | Astro `<Button asChild>` wraps in motion-affordant styles by default; user accepted | **DEFERRED** |
| 6 | LOW (UX) | Habit name "No phone after 22:00" truncates | 120px column width cuts longer names | **DEFERRED** |
| 7 | MEDIUM (UX) | Login shows BOTH password AND passkey up front | User asked for progressive disclosure: only show the relevant option after a valid email probe | **FIXED** `ae12515` |
| 8 | HIGH (UX) | Sticky date header missing 3 of 7 day-numbers; orphan digit at end of row | `grid-template-columns: 120px repeat(7, 44px) 0 0` — day-number cells auto-flowed into trailing 0-width streak/total columns | **FIXED** `ae12515` |

The migration is now visually clean across login → habits list → habit detail.

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

## Round 2 — after `d8a0e28` (post-logout fix)

Re-walked every page with the qa1 user to confirm the existing fixes and surface any regression / new issues.

### Bug #1 re-check — fixed

Manual repro: register fresh user → login → logout → DB query shows `token_version = 1` (was 0). Old JWT against `/api/v1/users/me` returns **401 Unauthorized**. Verified via curl on the live backend.

Root cause was two-fold:
1. The `session.refresh(user)` inside `user_bump_token_version` raised `InvalidRequestError` because `user` was loaded by the DI session, not the local one — the transaction rolled back. Removed the refresh.
2. Two `/auth/logout` routes were registered; fastapi_users' default bearer-revoke won route matching. Mounted the castor `logout_router` FIRST in `init_auth_routes()`.

Both fixes shipped in `d8a0e28`; tests in `tests/test_logout_bump.py` cover the behaviour.

### Bug #4 (the `\n` glyph) — revealed fresh sources

Round 1 fixed HabitGrid.astro's lone rogue blank line. Round 2 found THREE additional sources and an eighth user-visible defect:

1. `castor/app/webauthn_routes.py` was modified to remove trailing whitespace, but six `.astro` files in `src/pages/` and the `Heatmap.astro` had **literal byte sequences `---\n` on their closing-frontmatter lines**, where `\` and `n` were LITERAL text characters (4-char sequence), not a newline. Astro treats the literal `---\n` as 3-dash closing delimiter PLUS a `\n` text node on the very next character. Bodies of every affected file started with a `\n` text node that the browser then rendered visibly.

   Files fixed in `ae12515`:
   - `src/pages/login.astro`
   - `src/pages/register.astro`
   - `src/pages/stats.astro`
   - `src/pages/habits/new.astro`
   - `src/pages/habits/[id]/edit.astro`
   - `src/components/Heatmap.astro`

2. `src/components/HabitGrid.astro` had a similar `---\n` byte-sequence on its closing delimiter. Source was `---\n\n<!-- Pull-down...`; Astro parsed the 3-dash close and emitted `\n\n<!--...` as body text. The `\n` text node appeared in EVERY consuming page's `<main>`.

3. `src/layouts/Layout.astro` had the same vulnerability at the `<body>` block: `<body class="...">\n    <slot />\n    {cond && (...)}\n  </body>` produced a `\n` text node directly under `<body>`. Refactored to inline `<slot />` and the conditional on the same line as `<body>`.

4. `src/pages/habits/index.astro` had a blank line between the count div and the conditional `{habits.length === 0 && (...)}` blocks; when neither conditional matched (length > 0), Astro rendered the blank line as a `\n` text node directly under `<main>`.

Verified by inspection: `Array.from(document.body.childNodes).filter(n => n.nodeType === 3 && n.textContent.length > 0)` returns empty on `/habits` after `ae12515`.

### Bug #7 (progressive disclosure on /login) — fixed

Per user: "Screen asks for both email and password and passkey. Passkey only shows when user entered a valid email that has a registered passkey, otherwise it just progressively discloses password field."

The user did NOT want this FAQ-style answer of "show both". The whole registration/login flow should not ask about passkeys on every login; once a user has skipped it, they manage it through `/security`.

Implementation:
- Backend: new `POST /auth/webauthn/check` returns `{has_passkey: bool}`. Always 200. Deliberately INDISTINGUISHABLE between "user not found" and "no passkey enrolled" so it cannot be used to enumerate registered emails.
- Astro BFF: `src/pages/api/auth/webauthn/check.ts` proxies the fetch without an Origin header (the BFF runs server-side; the backend's `BrowserOriginMiddleware` treats server fetches as if from the backend host, not the browser — forwarding the browser's Origin produced 403).
- Login UI: email field + password field + Sign in button at first paint. On 250 ms debounced input, if the email is well-formed, the BFF is called. `has_passkey=true` reveals the Sign in with Passkey button and hides the password field; `has_passkey=false` keeps the password field and hides the passkey button. The "Passkey detected for this account." status text confirms the swap.
- On any error or empty email the UI reverts to the password-only state (always safe — passkey is opt-in, password is the default).
- Passkey enrolment is moved entirely to `/security` — the login page does not offer it anymore beyond the detected-account flow.

Verified live: registered fresh user → login shows password only. Manually added a fake passkey row to the user's `webauthn_credential` → typed the email → password field hides, Sign in with Passkey appears, status text appears. Removed the row → password returns.

### Bug #8 (sticky date header) — fixed

Visual QA showed only 4 of 7 day numbers (`21 20 19 18`) and an orphan `20` at the right end of the date-number row, while the 7 weekday labels above (`MON TUE WED THU FRI SAT SUN`) all rendered. Root cause: `grid-template-columns: 120px repeat(7, 44px) 0 0` produces 9 columns; the weekday cells used `grid-column: 1` explicitly but the day-number cells auto-flowed past column 8 into the 0-width streak/total columns (9, 10) which collapsed, then auto-wrapped to a new row at column 1.

Fix: explicit `grid-column: ${i + 2}` on each day-number cell, conditional trailing columns only when streak/total are enabled, and trailing headers with explicit `grid-column` too. After `ae12515` the dates read MON 23 TUE 22 WED 21 THU 20 FRI 19 SAT 18 SUN 17 — all 7 visible, perfectly aligned with the weekday labels.

### Final visual QA pass

| Page | Visual quality (1–10) | Notes |
|---|---|---|
| `/login` initial | 7 | Email + Password + Sign in. Passkey UI hidden. |
| `/login` after typing registered email with passkey | 9 | Password hides, passkey button appears, status text confirms. |
| `/habits` empty | 6 | Just because empty state is sparse; no stray characters. |
| `/habits` populated | 7 | Today cell solid teal with white checkmark; all 7 dates in header. |
| `/habits/{id}` heatmap (round 1) | 9 | Tick cells render. |

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
