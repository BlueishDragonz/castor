# Castor Migration Plan — Astro + ShadCN from NiceGUI/Quasar

> Companion to `docs/architecture/00-overview.md`. Read both before
> starting any work. This plan is the contract; deviations require
> an updated ADR in `docs/architecture/decisions/`.

## Strategy in one sentence

**Replace the NiceGUI front-end entirely with an Astro 7 SSR + shadcn/ui
React-islands app, keeping the Python backend untouched. Migrate by
business feature, not by file. Establish feature parity with `castor
main` in five slices (P0–P2), then harden and decouple from upstream
(P3–P4).**

---

## Decisions already made (do not revisit without an ADR)

| Decision | Why |
|---|---|
| Astro 7 SSR + `@astrojs/node` (standalone) | Per-user preference; existing scaffolding; SSR pages are reachable from the proxy without a separate static origin |
| shadcn/ui (React) as the only component library | User explicitly rejects "best-of-breed per surface"; one library covers buttons, sheets, dialogs, dropdowns, tabs, scroll-area, context-menu, command palette |
| Keep Python backend as-is | All hardening (token_version, audit, WebAuthn self-binding, BrowserOriginMiddleware) lives there; rewriting would lose security guarantees |
| JWT in httpOnly cookie | `castor_token`; `SameSite=Lax` (not `Strict` — see §1.3); `Secure` only when HTTPS terminates |
| Server-side `backendFetch` (Astro pages) | All writes hit the backend over the network; proxy is for client-side only |
| TypeScript pinned to `^5.9.3` | `@astrojs/check@0.9` requires TS 5.x/6.x programmatic API. TS 7 silently hides type errors. |
| `astro/tsconfigs/base` not `strict` | `strict` enforces React-style prop names on React components in `.astro` files; `base` + `strict: true` inside compilerOptions avoids that footgun while keeping strict checks |
| Domain layer in TypeScript (`src/lib/`) | Re-implement habit/streak/auth business rules once, in one place, so future Private Circle work has a real model to extend |
| No `:latest` from Docker Hub | Compose must reference a local `castor:custom-…` tag. Apollo skill records this; do not undo. |

---

## Phase 0 — Discovery & documentation (✅ shipped, see `docs/architecture/`)

Already produced:
- `docs/architecture/00-overview.md` — repository layout, route status table, divergence list
- This plan
- `ASTRO_MIGRATION_GAP_REPORT.md` (pre-existing; treat as one input)
- `TODO.md` (pre-existing; treat as one input)

No additional discovery needed; the divergence list (D1–D16) is the
single source of work for Phase 1–3.

---

## Phase 1 — Security and risk baseline

**Goal:** Before any new feature lands, lock the security posture so
that feature work can't drift. Output: a header policy, a cookie
policy, a CI gate, and a checklist that runs before every deploy.

### 1.1 Current front-end security posture (inventory)

| Control | State | Action |
|---|---|---|
| HTTPS at proxy | ✅ (TLS_TERMINATED or APP_URL https) | Verify in deploy |
| HSTS | ❌ not set on Astro responses | Add `Strict-Transport-Security: max-age=63072000; includeSubDomains` |
| CSP | ❌ not set | Add strict CSP — see §1.2 |
| `castor_token` cookie | 🟡 `httpOnly=true`, `sameSite=Lax`, `secure=false` (dev); needs `secure=true` when HTTPS | Env-driven flip |
| `castor_user` cookie (email for UI greeting) | 🟡 `httpOnly=false` (intentional), `sameSite=Lax`, `secure=false` | Same env flip; also drop it server-side rendering so the UI greeting comes from `Astro.locals.session.email` instead |
| `beaver_webauthn` cookie | ❌ not bridged — Astro doesn't set it, so WebAuthn begin/complete will fail origin check via proxy | Bridge via the same secure-cookie pattern in §1.3 |
| Secrets in source | ✅ none — `BACKEND_URL` is an env var, JWT secret stays on the backend | Document in `docs/security/secrets.md` |
| Dependency versions | ✅ all in `package.json`, locked with `pnpm-lock.yaml`, all on current majors | Add `pnpm audit` to CI |
| Third-party scripts | ✅ none beyond the long-press library (to be ported or replaced); Umami not yet wired | Document the allowlist |

### 1.2 Security headers (Astro middleware)

Add `src/middleware.ts` security-headers branch:

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'unsafe-inline';          # 'unsafe-inline' for Astro's inline scripts; tighten when ported to external files
  style-src 'self' 'unsafe-inline';           # shadcn injects style attrs
  img-src 'self' data:;                        # SVG favicon, no remote images yet
  font-src 'self';                             # Self-hosted Inter (no Google Fonts)
  connect-src 'self';                          # All fetches go through the proxy
  frame-ancestors 'none';
  base-uri 'self';
  form-action 'self';
  object-src 'none';
  upgrade-insecure-requests;
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-site
Strict-Transport-Security: max-age=63072000; includeSubDomains
```

> When Umami is wired (D10), add `script-src` and `connect-src` entries
> for the Umami origin.

### 1.3 Cookie policy (revised `lib/auth.ts`)

```ts
function isProd() {
  return (import.meta.env.PUBLIC_BACKEND_URL ?? '').startsWith('https://')
      || process.env.TLS_TERMINATED === 'true';
}

export function writeSession(cookies, token, email) {
  const secure = isProd();
  cookies.set(TOKEN_COOKIE, token, {
    httpOnly: true,
    sameSite: 'lax',         // Lax not Strict: passkey begin/complete
                              // redirect from RP → IdP and back needs it.
                              // The browser-origin middleware on the backend
                              // is the real CSRF gate.
    secure,
    path: '/',
    maxAge: 60 * 60 * 24 * 7,
  });
  // Drop castor_user entirely — UI greeting comes from Astro.locals.session.email
}

export function writeWebAuthnBrowserCookie(cookies, value) {
  cookies.set('castor_webauthn_browser', value, {
    httpOnly: true,
    sameSite: 'strict',     // WebAuthn ceremonies don't navigate
    secure: isProd(),
    path: '/',
    maxAge: 60 * 60 * 24 * 30,
  });
}
```

### 1.4 CI gate (`.github/workflows/security.yml`)

```yaml
name: security
on: { push: { branches: [main, migration-to-shadcn-astro] }, pull_request: }
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with: { node-version: '22.12.0', cache: 'pnpm' }
      - run: pnpm install --frozen-lockfile
      - run: pnpm audit --audit-level=high
      - run: pnpm exec astro check
      - name: header check (smoke)
        run: |
          pnpm exec astro dev &
          sleep 8
          curl -sI http://localhost:4321/ | grep -E '^(Content-Security-Policy|Strict-Transport-Security|X-Content-Type-Options|X-Frame-Options|Referrer-Policy):' \
            && echo "OK: security headers present" \
            || (echo "FAIL: missing security headers"; exit 1)
```

### 1.5 Pre-deploy checklist (run before every phase deploy)

```
[ ] pnpm audit clean at high/critical
[ ] astro check 0 errors 0 warnings
[ ] Smoke: register → login → add habit → tick → logout → re-login fails as expected
[ ] Cookie attributes match isProd()
[ ] CSP, HSTS, X-Frame-Options present in dev response
[ ] No secrets in committed .env files (.env is gitignored)
[ ] Backend /health returns 200
[ ] Migration branch pushed; image tag pinned in /opt/wg-net/docker-compose.yml
```

---

## Phase 2 — Target architecture & UX design

### 2.1 Routing (final)

```
/                              → redirect: /habits (if session) or /login
/login                         → email/password + passkey button
/register                      → email + password (≥12)
/forgot-password               → email → 12-digit code email
/reset-password                → code + new password
/logout                        → POST clears cookies, redirects /login
/habits                        → grid; mobile bottom nav + desktop top menu
/habits/new                    → add habit form
/habits/[id]                   → detail (streak, heatmap, history, best streaks, notes)
/habits/[id]/edit              → edit habit
/habits/order                  → drag-drop reorder
/stats                         → per-habit cards + 15-week heatmap
/settings                      → theme, custom CSS, danger zone
/security                      → passkeys, password change, recovery email
/import                        → import JSON/CSV
/export                        → download JSON/CSV
/account/delete                → confirm delete

# Future (Private Circle)
/circles                       → list of private circles user belongs to
/circles/[id]                  → circle detail (shared habits, members, invite)
/circles/[id]/invite           → invite by email or shareable link
```

### 2.2 Layout shell

```
┌─ Layout.astro ──────────────────────────────────────────────────────┐
│  <head>: tokens.css, security headers, PWA tags, theme cookie       │
│  <body>:                                                         │
│   ├─ <slot/>                                                    │
│   ├─ <BottomNav />      (mobile only, ≤640px)                   │
│   └─ <DesktopMenu />    (desktop ≥641px, top-right of header)   │
└─────────────────────────────────────────────────────────────────────┘
```

Mobile: content stacked vertically; `pb-24` to clear the bottom nav;
secondary actions go to `MoreSheet` (Account/Data/Session sections).

Desktop:
- Content area constrained to **65% of viewport width** with a
  `min-w-[640px]` cap. Implementation: `mx-auto max-w-[min(65vw,1100px)]
  min-w-[640px] px-4`. The 65% rule leaves space for ambient chrome
  (background imagery, contextual panels) without making the page
  feel empty on ultrawide.
- Top app bar: title (left), `DesktopMenu` (right)
- No bottom nav
- `MoreSheet` accessible from the menu, not the bottom bar

### 2.3 Design tokens (`src/styles/tokens.css`)

Castor's brand stays emerald-on-warm. All tokens already live in the
migration branch's `tokens.css`; verify and pin:

```
--background, --foreground            (oklch)
--primary, --primary-foreground       (emerald: oklch(0.62 0.13 163))
--muted, --muted-foreground
--border, --input, --ring
--destructive, --destructive-foreground  (red-700)
--success, --warning                  (for tick states)
--radius                               (0.5rem)
--castor-transition-duration           (150ms)
--castor-transition-easing             (cubic-bezier(0.2, 0.8, 0.2, 1))
```

Two breakpoints only:
- `mobile`: 0–640px
- `desktop`: ≥641px

No tablet breakpoint. The user's two-target rule simplifies the
component matrix (see §2.5).

### 2.4 Interaction model (replaces dropdowns with drawsheets)

| Legacy (NiceGUI) | Replacement (Astro) | Where |
|---|---|---|
| Dropdown menu on habit name | Long-press → `HabitContextSheet` (slide-up) | `/habits` rows, `/habits/[id]` |
| Right-click context menu | `DropdownMenu` (Radix) on the kebab icon | Desktop only |
| Top-right hamburger menu | `DropdownMenu` (Radix) in app bar | Desktop |
| Help dialog (modal) | `Dialog` from shadcn | `/security`, `MoreSheet → Help` |
| Confirmation dialogs | `Dialog` with confirm/cancel | delete habit, delete account, remove passkey |
| Date picker for stats | Native `<input type="date">` (no third-party picker needed for v1) | `/stats` |
| Tag filter chips | `DropdownMenu` w/ checkboxes on desktop, `Drawer` on mobile | `/habits` top-right |

Drawsheet pattern (mobile) = shadcn `Sheet` with `side="bottom"`,
rounded top corners, drag handle, snap-to-half on long content.
Modal pattern (desktop confirmation) = shadcn `Dialog` centered.

### 2.5 Component matrix (mobile vs desktop)

| Component | Mobile | Desktop |
|---|---|---|
| Nav | `BottomNav` | `DesktopMenu` in app bar |
| Habit row actions | `HabitContextSheet` (long-press) | `DropdownMenu` (kebab) |
| More sheet | In `BottomNav` (⋮) | In `DesktopMenu` (bottom item) |
| Reorder | Drag handle on row + sheet entry | Drag handle + menu entry |
| Habit add | FAB or `Sheet → New` | App-bar "+" |
| Tag filter | `Drawer` | `DropdownMenu` (chips) |
| Date picker | Native `<input type="date">` | Native `<input type="date">` |
| Theme toggle | Settings only | Settings only |

### 2.6 Private Circle (capture for Phase 3 P2+)

**Concept:** small (3–8 member) trusted group who can see each
other's habit completion. Not a social feed; not a leaderboard. The
shared surface is "I did this today" and "you're on a 12-day streak".

**Data sensitivity:** completion state is binary (done / not done).
No free-text notes are ever shared unless the user explicitly opts
in per-habit per-circle. Streak numbers are not shared by default;
opt-in per-habit.

**User types:**
- `Owner` — created the circle, can invite/remove members, delete
- `Member` — sees shared habits of others, can share their own
- `Invite` (pending) — has a token, has not accepted

**Domain entities:**
- `Circle { id, name, owner_id, created_at }`
- `CircleMember { circle_id, user_id, role, joined_at }`
- `CircleHabit { circle_id, habit_id, owner_user_id, visibility: 'ticks'|'ticks+streak', share_notes: bool }`
- `CircleInvite { id, circle_id, email, token, expires_at, accepted_at }`

**Permissions:**
- Owner can read/edit circle, read member list, revoke invites
- Member can read circle, read member list, read shared habits of others, edit their own CircleHabit entries
- Non-members see nothing (no public circles in v1)

**Where it plugs in:**
- Domain layer (`src/lib/`) gets new types; the backend gains
  `app/circles.py` + `routes/circles.py` (Phase 3 P2)
- Astro: new pages `/circles`, `/circles/[id]`, `/circles/[id]/invite`;
  `MoreSheet` gains a "Circles" section
- Backend mount: `/api/v1/circles/*`, behind the same JWT + token_version gate

**Why it's a Phase 3 P2 deliverable, not Phase 1:** the core
migration's risk surface is "do we keep the existing security and
UX guarantees". Adding a new social feature before parity is
reached multiplies the risk. Private Circle work begins after
`/security`, `/settings`, `/import`, `/export`, and the multi-day
grid are parity-complete.

---

## Phase 3 — Phased migration slices

Slices are ordered by **user impact × blast radius**. Lower-numbered
slices ship first; each slice ends with a rollback gate.

### Slice P0 — Mobile navigation chrome (✅ mostly shipped, gap in D13)

What this slice delivers:
- `BottomNav.astro` (✅ done)
- `MoreSheet.astro` + `MoreSheet.tsx` (✅ done — needs wiring test)
- `DesktopMenu.tsx` in app bar (✅ done — needs wiring test)
- Tag filter chip UI in `HabitGrid.astro` (D13 — not done)

Tasks:
1. Wire `BottomNav` and `DesktopMenu` from `Layout.astro` (already done; verify on a fresh build)
2. Add tag filter chip UI to `HabitGrid` header bar (when `enableTagFilter`)
3. Smoke test: mobile UA in devtools → bottom nav visible, ⋮ opens sheet, all 5 sheet entries (Security, Help, Import, Export, Log out) navigate
4. Smoke test: desktop UA → top app bar, `DesktopMenu` opens, all entries navigate

Rollback: feature flag `BOTTOM_NAV_ENABLED` in `lib/auth.ts` env
(read by `Layout.astro`); defaults to `true` in dev.

### Slice P0 — Multi-day habit grid (🟡 in flight, fix D5/D12/D13/D15)

What this slice delivers:
- 2D grid (habit rows × date columns, configurable 7–30)
- Per-cell toggle (`HabitCheckBox`) that posts to
  `/api/v1/habits/{id}/completions?date=YYYY-MM-DD`
- Sticky date headers, today highlight
- Streak + total badges (per `INDEX_SHOW_HABIT_STREAK`/`INDEX_SHOW_HABIT_COUNT`)

Tasks:
1. Finish `HabitGrid.astro` — confirm 7 default; add UI control to switch 7↔30 via `/api/v1/habits/meta` PUT
2. Wire `HabitCheckBox` to a single form per cell that POSTs to `/api/v1/habits/{id}/completions` (currently in flight; verify)
3. Add `IndexStreakBadge` and `IndexTotalBadge` per row (currently missing)
4. Wire long-press → `HabitContextSheet` for Edit/Duplicate/Archive/Reorder (port the legacy menu items)
5. Smoke test: tick today, untick, tick yesterday, observe streak badge updates

Rollback: feature flag `HABIT_GRID_ENABLED`; falls back to flat list.

### Slice P1 — Auth flows complete (✅ shipped — D1/D2/D3/D11 all closed)

What this slice delivers:
- `/login` accepts both email/password AND passkey
- `/register` accepts email/password; auto-login after
- `/security` lists/adds/removes passkeys, changes password, shows recovery email
- WebAuthn works over the proxy (D11 — server routes for completion)

Tasks (✅ all done):
1. ✅ **D1**: rewrite `SecurityContent.tsx` to call `/auth/webauthn/credentials` (not `/api/v1/auth/webauthn/credentials`); same for `/auth/webauthn/change-password` and `/auth/webauthn/credentials/{id}`
2. ✅ **D3**: fix TS errors in `login.astro` and `register.astro` — `userHandle`, `signature`, `authenticatorData`, `attestationObject` are not on `AuthenticatorResponse`; narrow with `instanceof PublicKeyCredential` then `instanceof AuthenticatorAssertionResponse` / `AuthenticatorAttestationResponse`
3. ✅ **D11**: bridge `castor_webauthn_browser` cookie via `mirrorWebAuthnBrowserCookie()` in `lib/auth.ts`; new server routes `/api/auth/webauthn/login/complete.ts` and `/register/complete.ts` write `castor_token` httpOnly (was previously impossible from JS via `document.cookie`)
4. ✅ Deleted `security.astro.bak` (D2)
5. Smoke test: register → logout → login with passkey → /habits reachable (TODO — needs a real authenticator in dev)
6. Smoke test: add passkey from `/security`, observe it in list; remove with password confirm (TODO)

Rollback: feature flag `PASSKEY_ENABLED`; falls back to password-only.

### Slice P1 — Settings completeness (✅ shipped — D6/D8/D14/D16 closed; habit-display toggles deferred to P2)

What this slice delivers:
- `/settings` has Help, custom CSS (read-only-until-backend), Account actions
- Cookie `secure=true` flip behind HTTPS (D7 — already shipped with the WebAuthn bridge slice)
- Logout bumps token_version (D14 — backend gap, documented as Phase 3 TODO)

Tasks (✅ / 🟡):
1. ✅ Moved "Import" / "Export" to `MoreSheet → Data` per the migration plan; settings retains Help and Account
2. ✅ Help button → shadcn `Dialog` with the four links from `show_help_dialog` (Wiki, Supporter, YouTube, Issues) — `src/components/HelpDialog.tsx`
3. ✅ Added PWA meta tags in `Layout.astro` + `public/manifest.webmanifest`
4. ✅ Verified `secure: isProd()` in `lib/auth.ts` (already shipped with the WebAuthn bridge)
5. ✅ Added `/account/delete` confirmation `Dialog` (D16) — password + typed "DELETE" + new server route `/api/account/delete`
6. 🟡 Removed habit-display toggles from `/settings` until the backend exposes a user-configs write endpoint (D14-related)
7. Smoke test: theme toggle persists across reload; delete-account flow requires password + confirm

Rollback: settings changes are isolated; no flag needed beyond a
per-component `isMounted` guard if a feature rolls back.

### Slice P1 — Habit detail completeness (✅ shipped — D5/D12 closed; long-press event notes deferred to P2)

What this slice delivers:
- `/habits/[id]` shows: streak, history (1 year), best streaks, calendar heatmap (15 weeks)
- `/habits/[id]/edit` form (already shipped on the branch)
- `/habits/order` is reachable from the context sheet
- New server-side routes: `/habits/{id}/complete` (arbitrary date), `/habits/{id}/archive`, `/habits/{id}/duplicate`

Tasks (✅ all done):
1. ✅ Added "Best streaks" card (computed in-page; off-by-one bug from the old code fixed)
2. ✅ Replaced ECharts-based charts with inline CSS bars (no new dep, accessible, fast)
3. ✅ Used the existing `Heatmap.astro` component for the 15-week view
4. ✅ Wired `HabitContextSheet` to real actions: Edit → `/habits/{id}/edit`, Duplicate → `/habits/{id}/duplicate`, Reorder → `/habits/order`, Archive → `/habits/{id}/archive`
5. ✅ Date picker is a real `<form>` posting to `/habits/{id}/complete`; no client-side fetch
6. ✅ Removed `__HABIT_TOKEN__` window global (XSS exposure — bearer now reaches backend only via httpOnly cookie)
7. ✅ Action-error query params (from the action routes) surface as `<Alert>` components
8. ✅ Removed ECharts reference entirely (was never loaded, would have silently no-op'd)
9. ✅ Applied 65vw / 640px-min content cap to match the design system
10. 🟡 Long-press → notes textarea deferred to P2 (needs the long-press helper component first)
11. Smoke test: open detail, observe heatmap renders; date-pick today; verify it persists; archive and verify the habit disappears from /habits

### Slice P1 — Import / Export (✅ shipped — D5 closed; backend POST /habits/import gap documented)

What this slice delivers:
- `/export` works end-to-end (server-side GET to `/api/v1/habits/export`, streams JSON with `Content-Disposition: attachment`)
- `/import` page is server-side POST; surfaces honest "endpoint not available" message until the backend ships POST `/api/v1/habits/import`
- `__IMPORT_TOKEN__` window global removed; the bearer reaches the backend only via the httpOnly `castor_token` cookie

Tasks (✅ all done):
1. ✅ Verified backend mount points: only `GET /api/v1/habits/export` exists; no POST import endpoint
2. ✅ Rewrote `/export` as server-side proxy with proper Content-Disposition header
3. ✅ Rewrote `/import` as server-side POST handler with shape validation + 404 honesty
4. ✅ Removed `__IMPORT_TOKEN__` window global — XSS exposure; bearer now reaches backend only via httpOnly cookie
5. ✅ Removed `innerHTML` rendering of preview/result blobs (XSS vector if backend ever returns attacker-controlled HTML)
6. Smoke test: export → import round-trip (TODO — requires backend POST endpoint to land)
7. Smoke test: 65vw content cap applied; danger-zone Alert renders

Rollback: feature flag `EXPORT_ENABLED`/`IMPORT_ENABLED` in env; defaults to on once backend POST lands.

### Slice P2 — Calendar heatmap polish, tag filter, long-press

What this slice delivers:
- 15-week heatmap on `/stats` (✅ done; needs polish)
- 1-year history component (D12)
- Tag filter chip UI (D13)
- Long-press on row opens `HabitContextSheet` (D9)

Tasks:
1. Extract `HabitStreakBadge` and `HabitTotalBadge` to shared component
2. Add `LongPress` directive helper (replaces `long-press-event.min.js`)
3. Polish heatmap colours against design tokens

### Slice P2 — Polish (✅ shipped — D9, D13, D15 closed; long-press notes-textarea is the only remaining P2 item)

What this slice delivers:
- Tag-filter chip toggle bug fixed (was effectively never visible)
- `/habits/order` is server-side POST → backend `PUT /api/v1/habits/meta`
- `HabitCheckBox` is a real `<form action="/habits/{id}/complete">` (no client fetch)
- Long-press / right-click on a habit name opens the context sheet (was already wired; callbacks were empty)

Tasks (✅ all done):
1. ✅ Fixed `updateFilter()` toggle in `HabitGrid.astro`: visible at top OR when scrolling up; hidden when scrolling down
2. ✅ Rewrote `/habits/order` as a server-side handler: drag-and-drop updates hidden `habit_ids`; submit POSTs to the same route; backend call is `PUT /api/v1/habits/meta {order: [...]}`. `__REORDER_TOKEN__` window global removed.
3. ✅ Rewrote `HabitCheckBox` as a `<form action="/habits/{id}/complete">`. `token` prop and `window.toggleDone` global removed. Bearer never reaches the client.
4. ✅ Dropped the now-unused `token` prop on `HabitGrid` and the `token={token}` argument from `/habits/index.astro`
5. 🟡 Notes-textarea-on-long-press deferred (needs the long-press helper to differentiate notes vs context sheet; out of scope for this slice)

### Slice P2 — Private Circle (initial design)

What this slice delivers (deferred from earlier slices so P1 lands first):
- Backend: `app/circles.py` (SQLAlchemy models), `routes/circles.py` (mounted at `/api/v1/circles/*`), `app/audit.py` gains circle events
- Astro: `/circles` (list), `/circles/[id]` (detail), `/circles/[id]/invite` (shareable link)
- `MoreSheet` gains "Circles" entry
- Domain layer: `src/lib/circles.ts` (types, pure functions for visibility filtering)

Tasks:
1. **Schema:** `circle`, `circle_member`, `circle_habit`, `circle_invite` SQLAlchemy tables; `create_db_and_tables` adds them
2. **Routes:** POST/GET/DELETE `/api/v1/circles`, POST/DELETE `/api/v1/circles/{id}/members`, POST/DELETE `/api/v1/circles/{id}/habits`, GET/POST `/api/v1/circles/{id}/invites`
3. **Auth:** reuse `VersionedJWTStrategy` + `token_version`; new `registration_user` dependency scoped to circle membership
4. **Visibility:** circle members can ONLY read the `Habit` rows whose `id` is in their circles' `circle_habit` rows AND `visibility` permits
5. **Astro UI:** see §2.6; uses the same `Sheet`/`Dialog`/`Drawer` primitives
6. **Smoke:** create circle, invite by email, accept (token URL), add shared habit, observe member's tick visible to owner

Rollback: feature flag `CIRCLES_ENABLED`. Default off in production
until at least one full release cycle has been run with the flag on
in dev/staging.

### Slice P3 — Decoupling and hardening (Phase 4 in the goal)

What this slice delivers:
- Remove the `frontend/` (NiceGUI) directory from Castor entirely
- Rename `beaverhabits` package to `castor` (or keep the package name
  but rewrite the brand: imports, strings, PWA name, README)
- Remove unused Paddle, admin page references from `configs.py`
  (`paddle_page.py`, `admin.py` — admin role not used)
- Add CI gate for `pnpm audit` + `astro check` + `pytest` (Python)
- Add an ADR for each removed feature

Tasks:
1. Remove `paddle_page.py`, `pricing_page.py` (no Stripe/Paddle in self-host)
2. Remove `chip_sets_page.py` (deferred)
3. Remove `admin.py` and the `/admin` mount
4. Rewrite `IDENITY` and brand strings in `main.py`/`__init__.py`
5. Update PWA manifest (`statics/pwa/manifest.json`) to `Castor`
6. Update README + `docs/architecture/00-overview.md` to mark upstream references as historical
7. CI: `pytest`, `astro check`, `pnpm audit` all green on `main`

---

## Phase 4 — Decoupling & hardening

Covered by Slice P3 above. The deliverable here is the final report
(see `docs/security/final-report.md` template in §1).

### 4.1 Final report template

```
# Castor v1 — Independence, Security, UX Improvements

## Independence from beaverhabits
- 0 NiceGUI files in active deployment (was: 25 files, ~6000 LOC)
- 0 imports of upstream `frontend/` (was: every page)
- Brand fully on Castor (name, PWA, manifest, README, OG tags)

## Security posture
- Astro middleware emits CSP, HSTS, X-Frame-Options, Referrer-Policy
- Cookie policy: httpOnly, secure-when-HTTPS, SameSite=Lax
- JWT version enforcement preserved (VersionedJWTStrategy)
- Audit log preserved (register, login, change, delete, passkey)
- WebAuthn self-binding preserved
- Browser-origin CSRF middleware preserved
- 0 high/critical `pnpm audit` findings
- 0 secrets in source tree

## UX improvements
- Single UI library (shadcn/ui) — was: NiceGUI + Quasar + ad-hoc Tailwind
- 2 breakpoints (mobile / desktop) — was: 3 (mobile/tablet/desktop)
- Content constrained to 65% viewport on desktop (new)
- Drawsheet pattern replaces dropdowns (mobile + desktop where appropriate)
- Persistent nav guarantees return-to-habits (BottomNav + DesktopMenu)
- PWA meta tags ported
- Custom CSS editor + sanitizer preserved
```

---

## Slice ordering & estimated effort

| Slice | Status | Effort (calendar days) | Dependencies |
|---|---|---|---|
| P0 mobile nav chrome | ✅ shipped (D13 closed in P2 polish) | 0.5 | none |
| P0 multi-day grid | 🟡 in flight | 2 | P0 nav chrome |
| P1 auth (D1/D3/D11) | ✅ shipped | 0 (already done this slice) | none |
| P1 settings (D6/D7/D14/D16) | ✅ shipped (D14 backend gap deferred) | 0 (already done this slice) | P1 auth |
| P1 habit detail (D5/D12) | ✅ shipped (long-press notes deferred to P2) | 0 (already done this slice) | P0 grid |
| P1 import/export (D5) | ✅ shipped (backend POST /habits/import gap documented) | 0 (already done this slice) | none |
| P2 polish (D9/D13/D15) | ✅ shipped (long-press notes-textarea is the only remaining P2 item) | 0 (already done this slice) | all P1 |
| P2 Private Circle | not started | 5 | all P1; new backend code |
| P3 decoupling | not started | 1 | all P2 |

Total calendar (best case, sequential): **~16 days**. With
subagent fan-out for independent slices (P1 settings + P1 import/export
+ P1 habit detail), calendar compresses to **~10 days**.

---

## Open questions for the user (before starting P2)

1. **Private Circle scope**: shared ticks only, or shared ticks + streak
   numbers + opt-in notes? (My default: ticks + opt-in streak, no notes
   in v1.)
2. **Tablet handling**: confirm we can collapse to 2 breakpoints
   (mobile/desktop). Tablet users get the desktop layout. (My default: yes.)
3. **Backend rename**: rename `beaverhabits` Python package to `castor`?
   Big diff, no functional change. (My default: keep package name `beaverhabits`
   for v1, rewrite brand strings; do the rename in a separate Phase 4
   commit so the diff is reviewable.)
4. **Logout semantics**: should the Astro logout POST also bump
   `token_version` server-side, or is the cookie-clearing sufficient
   (server still rejects stale cookies because there's no session in
   the first place)? (My default: add a server-side `POST /auth/logout`
   that bumps `token_version`; the existing TODO in the fork is the
   right place to land it.)
