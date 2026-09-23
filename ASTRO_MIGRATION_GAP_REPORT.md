# Astro Migration Gap Report
**Comparison: Main branch (NiceGUI) → Astro branch (concepts)**

---

## Executive Summary
The Astro migration covers **auth flows, habit CRUD, basic stats, and settings**, but is **missing all mobile-first navigation chrome**, **multi-day habit completion grid**, **passkey/WebAuthn support**, and **several data/export features**. The app is functional for desktop happy-paths but not feature-complete for mobile or power users.

---

## 1. Navigation Structure

| Feature | NiceGUI (Main) | Astro (Concepts) | Status |
|---------|---------------|------------------|--------|
| **Global header/menu** | Desktop: hamburger menu (Add, Tools, Security, Help, Logout) | None — pages are islands with only back links | ❌ Missing |
| **Mobile bottom nav** | Fixed 3-item: Home / Stats / More (⋮) | None | ❌ Missing |
| **More sheet (⋮)** | Sectioned bottom sheet: Account (Security, Help), Data (Import, Export), Session (Log out) | None — no sheet component used | ❌ Missing |
| **Page-to-page links** | Full nav graph via menu + bottom nav | Only `/habits` → `/habits/[id]` and `/habits` → `/habits/new`; Stats/Settings are dead ends | ⚠️ Partial |
| **Breadcrumbs/back** | Implicit via menu | Only habit detail has "← Back to habits" | ⚠️ Partial |

**Nav graph (NiceGUI):**
```
/gui (Home) ←→ /stats ←→ /security ←→ /import, /export, /order
     ↓
   More (⋮) → Security, Help, Import, Export, Log out
```

**Nav graph (Astro):**
```
/habits → /habits/new
  ↓
/habits/[id] → (back only)
/stats → (back to /habits only)
/settings → (logout, delete account)
/login, /register, /forgot-password, /reset-password → /habits
```

**Missing:** No way to reach `/stats` or `/settings` from `/habits` without typing the URL.

---

## 2. Login Flow — Password vs Passkey

| Feature | NiceGUI (Main) | Astro (Concepts) | Status |
|---------|---------------|------------------|--------|
| **Email/password login** | ✅ `/login` | ✅ `/login.astro` (form-encoded POST) | ✅ Parity |
| **Registration** | ✅ `/register` | ✅ `/register.astro` (auto-login after) | ✅ Parity |
| **Forgot password** | ✅ Email → 12-digit code | ✅ `/forgot-password.astro` | ✅ Parity |
| **Reset password** | ✅ Code + new password | ✅ `/reset-password.astro` | ✅ Parity |
| **Passkey registration** | ✅ WebAuthn `navigator.credentials.create()` | ❌ **Missing entirely** | ❌ Missing |
| **Passkey authentication** | ✅ WebAuthn `navigator.credentials.get()` | ❌ **Missing entirely** | ❌ Missing |
| **Passkey list/management** | ✅ Security page: list, add, remove (with password confirmation) | ❌ **Missing** | ❌ Missing |
| **Password change** | ✅ Security page: current + new + confirm, revokes sessions | ❌ **Missing** (only delete account) | ❌ Missing |
| **Credential count badge** | ✅ Shows "1 passkey" in More sheet | N/A | ❌ Missing |

**NiceGUI implementation:** `security_page.py` (469 lines), `security_passkeys.js` (client WebAuthn), `security_actions.py` (server). Uses `fastapi-users` WebAuthn integration.

**Astro:** No `/security` page, no WebAuthn routes, no `webauthn` proxy path in `backendFetch`.

---

## 3. Habit Tracking UI — Multi-Day "Ticks" (Critical Gap)

| Feature | NiceGUI (Main) | Astro (Concepts) | Status |
|---------|---------------|------------------|--------|
| **Habit grid** | ✅ 2D grid: habit rows × date columns (configurable 7–30 days) | ❌ **List only** — one "Mark done" button per habit | ❌ Missing |
| **Past-day checkboxes** | ✅ Each cell = `HabitCheckBox` for that habit+day; toggles completion | ❌ **Cannot mark past days** | ❌ Missing |
| **Today highlight** | ✅ Sticky date headers; today column visually distinct | ❌ N/A | ❌ Missing |
| **Streak badge in grid** | ✅ `IndexStreakBadge` per row (configurable) | ❌ Only on detail page | ❌ Missing |
| **Total count badge** | ✅ `IndexTotalBadge` per row (configurable) | ❌ Missing | ❌ Missing |
| **Tag filtering** | ✅ `tag_filter_component` + `filter_habits_with_tags` | ❌ Missing | ❌ Missing |
| **Habit notes** | ✅ Long-press → `habit_notes` (textarea) | ❌ Missing | ❌ Missing |
| **Calendar heatmap** | ✅ `CalendarHeatmap` (15 weeks) on detail page | ❌ Missing | ❌ Missing |
| **Habit history (1 year)** | ✅ `habit_history` component | ❌ Missing | ❌ Missing |
| **Best streaks** | ✅ `habit_streak` card on detail | ✅ Streak computed but no "best streaks" UI | ⚠️ Partial |

**NiceGUI `index_page.py`** renders a `ui.grid` with `NAME_COLS + len(days) * DATE_COLS + COUNT_BADGE_COLS` columns. Each `HabitCheckBox` posts to `/habits/{id}/completions` with the target date.

**Astro `habits/index.astro`** renders a flat `<ul>` of cards with a single "Mark done" `<form>` posting to today only. No date parameter.

---

## 4. Mobile Navigation — Bottom Nav + More Sheet

| Feature | NiceGUI (Main) | Astro (Concepts) | Status |
|---------|---------------|------------------|--------|
| **Bottom nav bar** | ✅ Fixed, `position: fixed; bottom: 0; z-50` with safe-area inset | ❌ **Missing** | ❌ Missing |
| **Home button** | ✅ Redirects to `/gui` | ❌ Missing | ❌ Missing |
| **Stats button** | ✅ Redirects to `/stats` | ❌ Missing | ❌ Missing |
| **More (⋮) button** | ✅ Opens `more_sheet.py` dialog | ❌ Missing | ❌ Missing |
| **More sheet — Account** | Security (with passkey badge), Help | ❌ Missing | ❌ Missing |
| **More sheet — Data** | Import, Export | ❌ Missing | ❌ Missing |
| **More sheet — Session** | Log out (red) | ❌ Missing | ❌ Missing |
| **Mobile detection** | `is_mobile(user_agent)` gates bottom nav | ❌ No UA detection in Astro | ❌ Missing |
| **Reorder habits** | Top-right icon (mobile) / menu (desktop) | ❌ Missing | ❌ Missing |

**Astro has:** `Sheet` component (`sheet.tsx`) with `side: 'bottom'` variant — the primitive exists but is **unused**.

---

## 5. Passkey / Security Page — Entirely Missing

| Feature | NiceGUI (Main) | Astro (Concepts) | Status |
|---------|---------------|------------------|--------|
| **Page route** | `/security` | ❌ No file | ❌ Missing |
| **Passkey list** | Shows all credentials with nickname + date added | ❌ Missing | ❌ Missing |
| **Add passkey** | Dialog: nickname → `navigator.credentials.create()` | ❌ Missing | ❌ Missing |
| **Remove passkey** | Menu → Remove → password confirm → `security_actions.remove_passkey` | ❌ Missing | ❌ Missing |
| **Change password** | Dialog: current + new + confirm → revokes all sessions | ❌ Missing (only delete account) | ❌ Missing |
| **Recovery email display** | Shows user.email as recovery contact | ❌ Missing | ❌ Missing |
| **Forgot password link** | In password & delete dialogs | ✅ On login page only | ⚠️ Partial |
| **Session version gate** | `user.token_version` prevents stale writes | ❌ Missing | ❌ Missing |

**Backend endpoints used (missing in Astro proxy):**
- `GET /users/me/webauthn-credentials`
- `POST /users/me/webauthn-credentials/register` (challenge + options)
- `DELETE /users/me/webauthn-credentials/{credential_id}`
- `POST /users/me/password/change`
- `POST /auth/forgot-password`, `POST /auth/reset-password`

---

## 6. Settings Page Completeness

| Feature | NiceGUI (Main) | Astro (Concepts) | Status |
|---------|---------------|------------------|--------|
| **Theme toggle** | ✅ Dark/Light buttons + `set_user_dark_mode` | ✅ Radio + cookie (`castor-theme`) | ✅ Parity |
| **Custom CSS editor** | ✅ CodeMirror editor, persists to DB | ❌ **Missing** | ❌ Missing |
| **Passkey management** | ✅ Via Security page (linked from menu) | ❌ **Missing** | ❌ Missing |
| **Password change** | ✅ Via Security page | ❌ **Missing** | ❌ Missing |
| **Import/Export** | ✅ Menu → Import/Export pages | ❌ **Missing** | ❌ Missing |
| **Data: reorder habits** | ✅ `/order` page + menu | ❌ **Missing** | ❌ Missing |
| **Account delete** | ✅ Menu → Logout | ✅ `/account/delete.astro` (standalone) | ✅ Parity |
| **Sign out** | ✅ Menu + More sheet | ✅ Form POST `/logout` on each page | ✅ Parity |
| **Help dialog** | ✅ Menu → Help (links: Wiki, Supporter, YouTube, Issues) | ❌ **Missing** | ❌ Missing |
| **PWA meta tags** | ✅ `pwa_headers()`: manifest, apple-touch-icon, theme-color | ❌ **Missing** | ❌ Missing |

---

## 7. Additional Missing Features (Data & UX)

| Feature | NiceGUI | Astro | Status |
|---------|---------|-------|--------|
| **Import (JSON/CSV)** | `/import` page | ❌ Missing | ❌ Missing |
| **Export (JSON/CSV)** | `/export` page | ❌ Missing | ❌ Missing |
| **Habit reorder** | `/order` page (drag-drop) | ❌ Missing | ❌ Missing |
| **Admin page** | `/admin` (superuser only) | ❌ Missing | ❌ Missing |
| **Pricing/Supporter** | `/pricing` + Paddle checkout | ❌ Missing | ❌ Missing |
| **Chip sets / tags UI** | `chip_sets_page.py` | ❌ Missing | ❌ Missing |
| **Long-press context menus** | `long-press-event.min.js` + `HabitCheckBox` | ❌ Missing | ❌ Missing |
| **Analytics (Umami)** | Conditional script inject | ❌ Missing | ❌ Missing |
| **SEO / OG / JSON-LD** | `custom_headers()` injects full meta | ❌ Missing | ❌ Missing |

---

## 8. Backend Proxy Gaps (Astro `backendFetch`)

The Astro `backendFetch` in `lib/auth.ts` proxies `/auth/*` and `/api/v1/*` but **does not proxy**:
- `/users/*` (needed for `/users/me`, `/users/me/webauthn-credentials`)
- `/webauthn/*` (WebAuthn challenge/registration endpoints)
- `/admin/*`

---

## 9. Priority Fix Order (Suggested)

| Priority | Area | Rationale |
|----------|------|-----------|
| **P0** | Bottom nav + More sheet | Mobile app is unusable without navigation; blocks all mobile testing |
| **P0** | Multi-day habit grid | Core habit-tracking UX; "Mark done" only for today breaks the mental model |
| **P1** | Security page + Passkeys | Differentiator feature; password-only is a regression |
| **P1** | Settings completeness (Import/Export/Help/PWA) | Data portability + discoverability |
| **P2** | Calendar heatmap + history + best streaks | Power-user retention features |
| **P2** | Tag filtering + notes | Nice-to-have organization |
| **P3** | Admin, Pricing, Chip sets | Non-core, can defer |

---

## 10. Files to Create / Modify (Astro)

### New Pages
- `src/pages/security.astro` — passkey list, add, remove, password change
- `src/pages/import.astro`, `src/pages/export.astro`
- `src/pages/order.astro` — drag-drop reorder
- `src/pages/help.astro` (or dialog component)

### New Components
- `src/components/BottomNav.astro` — fixed 3-item bar (Home/Stats/More)
- `src/components/MoreSheet.astro` — uses `Sheet` (side='bottom')
- `src/components/HabitGrid.astro` — 2D grid with date columns, `HabitCheckBox` per cell
- `src/components/HabitCheckBox.astro` — posts to `/habits/{id}/completions?date=YYYY-MM-DD`
- `src/components/CalendarHeatmap.astro` — 15-week SVG/Canvas
- `src/components/HabitNotes.astro` — long-press → textarea

### Layout Changes
- `src/layouts/Layout.astro` — inject `BottomNav` on mobile (detect via `navigator.userAgent` in client script, or CSS `@media (max-width: 640px)` + slot)
- Add PWA meta tags (`pwa_headers` equivalent)

### Lib / Auth
- `src/lib/auth.ts` — add `/users/*` and `/webauthn/*` to proxy allowlist
- `src/lib/webauthn.ts` — client helpers for `navigator.credentials.create/get`

### API Routes (if needed)
- `src/pages/api/auth/webauthn/...` — or rely on backend proxy entirely

---

## 11. Verification Checklist (Definition of Done)

- [ ] Mobile: Bottom nav visible on ≤640px, links work
- [ ] Mobile: ⋮ opens More sheet with 6 items (Security, Help, Import, Export, Log out)
- [ ] Desktop: Header menu with Security, Help, Import, Export, Log out
- [ ] `/habits` shows grid with 7–30 date columns, each cell toggles completion
- [ ] Past days can be checked/unchecked; today highlighted
- [ ] Streak + total badges render in grid rows
- [ ] `/habits/[id]` has calendar heatmap (15 weeks), 1-year history, best streaks, notes
- [ ] `/security` lists passkeys, adds via WebAuthn, removes with password confirm
- [ ] `/security` changes password, revokes sessions, shows recovery email
- [ ] `/settings` has Custom CSS editor, Import, Export, Help, PWA manifest
- [ ] Login supports passkey (`navigator.credentials.get`) as alternative to password
- [ ] All nav links reachable without typing URLs

---

## 12. Effort Estimate (Rough)

| Epic | Estimate |
|------|----------|
| Bottom nav + More sheet | 2–3 days |
| Habit grid (multi-day) | 3–4 days |
| Security page + WebAuthn | 4–5 days |
| Settings completeness | 2 days |
| Calendar heatmap + history | 2–3 days |
| Import/Export/Reorder | 2 days |
| **Total** | **15–19 days** |

---

*Generated from code audit: NiceGUI `/beaverhabits/frontend/` vs Astro `/web/concepts/src/pages/`*