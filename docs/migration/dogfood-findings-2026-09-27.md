# Dogfood findings — 2026-09-27

Driven against the deployed service on apollo (`castor:custom-2026-09-26-f1`)
through a real browser: register → login → create habit → tick → stats →
settings → dark mode, plus a crawl of every route and a look at each public
page. Vision was used on the landing page, help, login, 404, the habit grid,
the stats heatmap, settings and the dark theme.

The 466-test suite was green throughout this walkthrough. None of the five
defects below could have been caught by it.

---

## 1. CRITICAL — the application was unusable in a browser

**Every** state-changing request returned `403 {"detail":"Browser origin not
allowed"}`. No user could register, log in, tick a habit, or change a setting.

Neither side of the fault was visible on its own:

- The Astro BFF forwards the **public** `Origin` header to the backend, so
  the backend rather than the frontend decides CSRF (F21).
- The backend listens on `127.0.0.1:8081`. With `CSRF_ALLOWED_ORIGINS` at its
  empty default, `BrowserOriginMiddleware` derives its fallback origin from
  the `Host` header — the loopback address.
- A public origin and a loopback origin can never match.

`CSRF_ALLOWED_ORIGINS` was never set in production, so the two settings were
independent and a deployment could be half-configured into a total outage
that no backend test could detect.

**Fix:** `Settings.csrf_allowed_origins()` always includes `FRONTEND_URL`
alongside the operator-configured list. `tests/test_csrf_allowed_origins.py`
pins it; reintroducing the defect fails 5 of the 10, and the tests also assert
the defence still rejects genuine cross-origin writes, cross-site
`Sec-Fetch-Site`, and cookie-bearing writes with no `Origin`.

**Verified live:** registration and login both succeed after redeploy.

---

## 2. HIGH — every weekday header on /habits was wrong

Rendered: `Mon 27  Tue 26  Wed 25  Thu 24  Fri 23  Sat 22  Sun 21`.
27 September 2026 is a **Sunday**.

Labels came from a static `Mon..Sun` array indexed by column position, but the
columns are a rolling 7-day window ending today (`today - i`), not a calendar
week. The two agree only when today happens to fall on the last day of an
aligned week — so the bug appears and vanishes with the date, which is
presumably why it was never noticed.

This is **data correctness, not cosmetics**: a user reading "Mon 27" and
ticking it records a habit against the wrong day.

It was also invisible to assistive technology. The `aria-label` on each cell
was already derived correctly from the date, so a screen-reader user heard
the right weekday while a sighted user saw the wrong one. Confirmed in the
live DOM:

```
aria-labels : Today, Saturday 26 Sept, Friday 25 Sept ... Monday 21 Sept
visible     : Mon27, Tue26, Wed25, Thu24, Fri23, Sat22, Sun21
```

**Fix:** derive each label from the date it sits above.
`web/concepts/scripts/check-weekday-headers.mjs` is negative-controlled — it
fails when the positional array returns, and asserts the fixture still
reproduces the original mismatch.

---

## 3. HIGH — the empty state was a dead end

The page said *"Click **+ Add habit** to start"*, but that link is only
rendered when the habit grid has rows. With zero habits the page contained
four interactive elements — menu, Home, Stats, More — and none created a
habit.

A brand-new user had no way to make their first habit from the UI. The only
route was knowing the `/habits/new` URL. Confirmed by DOM query, not just by
screenshot: the menu was opened and contains Reorder, Import, Export,
Statistics, Circles, Security, Settings, Log out — no create action.

**Fix:** the empty state now renders its own primary action.

---

## 4. MEDIUM — the 404 page did not belong to the product

No `404.astro` existed, so Astro served its own default: a **dark** page
carrying the **Astro wordmark**, echoing the internal request path back, with
no navigation at all.

Three problems in one screen — it leaked the framework, inverted the light
theme used by every other page (so a mistyped URL looked like a different
application), and left a user who followed a stale link with nothing to
click.

**Fix:** a real 404 on the same `PublicLayout` as `/help`, `/terms` and
`/privacy`, with Home and Help actions and no path echo.

---

## 5. LOW — "1 days ago"

Every habit cell's accessible name pluralised unconditionally. A screen
reader announced "1 days ago".

---

## Design observations (not defects)

- The habit grid defaults to **newest-first** (today in the leftmost column).
  Time therefore runs right-to-left. Defensible, and it is a user setting, but
  the inverted convention is worth reconsidering: most trackers read
  oldest-to-newest, and this is very likely what made the weekday bug easy to
  miss.
- The stats heatmap labels only the first month with its year ("Jun 2026"
  above the June column) while Jul/Aug/Sept are unlabelled. Fine for a
  single-year range, ambiguous when a range spans a year boundary.
- Secondary controls (hamburger menu, "Sign in" on the landing page, the logo)
  are very low contrast. The hamburger in particular is hard to find.
- The heatmap in `/stats` and `/habits/[id]` is **correct** — its rows are
  genuine calendar weekdays, so the static `Mon..Sun` array is right there.
  Only `HabitGrid` mislabelled.

---

## What this says about the test suite

The suite covers the contracts it was written against. It does not cover the
rendered output, and in particular it had no test asserting that a label
matches the data it labels, that a page's primary action exists, or that the
app can be completed by a human at all. The CSRF failure is the sharpest
example: a completely broken application, green in every check that does not
involve a browser.
