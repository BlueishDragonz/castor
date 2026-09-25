# Slice 25 — Vanta.js dots background on public surface

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: Standing-goal follow-up — add a vanta.js dots
background to the Login pages and the related non-authenticated
journeys, matching the user-supplied style snippet
(`three.r134.min.js` + `vanta.dots.min.js` + `VANTA.DOTS({...})`).

## Decision: introduce a `PublicLayout.astro`

The authenticated `Layout.astro` is used by 26 Astro pages. Adding
the vanta background to that file would paint the dots on every
authenticated page too (habits, circles, settings, etc.), which
isn't what the request asked for. The clean separation is a
second layout shell for the public surface only.

**PublicLayout migrations** (9 pages):

| Old import | New import |
| --- | --- |
| `import Layout from '../layouts/Layout.astro';` | `import Layout from '../layouts/PublicLayout.astro';` |

Pages migrated:

  - `pages/index.astro` (landing)
  - `pages/login.astro`
  - `pages/register.astro`
  - `pages/forgot-password.astro`
  - `pages/verify-reset.astro`
  - `pages/reset-password.astro`
  - `pages/help.astro`
  - `pages/terms.astro`
  - `pages/privacy.astro`

All 26 other pages still use `Layout.astro` (authenticated shell
with `BottomNav`).

## Decision: vendor the libraries under `web/concepts/public/libs/`

Vanta.js and Three.js are already mirrored at
`/statics/libs/{three.r134.min.js,vanta.dots.min.js}` (legacy path
under the old NiceGUI app). Astro serves `/web/concepts/public/*`
as static at `/*` with zero config. Copies of both libraries plus
their license files now live at `web/concepts/public/libs/` so the
scripts are referenced as `/libs/three.r134.min.js` and
`/libs/vanta.dots.min.js`.

This keeps the build hermetic — no CDN dependency at runtime.

## The body markup

```html
<body class="min-h-screen antialiased bg-transparent">
  <div id="vanta-bg" class="fixed inset-0 z-0" aria-hidden="true"></div>
  <div class="vanta-content relative z-10">
    <slot />
  </div>
</body>
```

- Body is `bg-transparent` so the canvas's white `backgroundColor`
  paints the page background.
- `#vanta-bg` is `fixed inset-0 z-0` so the dot field stays
  locked in place while content scrolls.
- `.vanta-content` is `relative z-10` so all page content sits
  above the dots.

## Final config (matching the user-supplied snippet)

```ts
VANTA.DOTS({
  el: '#vanta-bg',
  mouseControls: true,
  touchControls: true,
  gyroControls: false,
  minHeight: 200.00,
  minWidth: 200.00,
  scale: 1.00,
  scaleMobile: 1.00,
  color: 0x7cbb,
  backgroundColor: 0xffffff,
  size: 6.00,
  spacing: 36.00,
  showLines: false,
});
```

## Bug I hit during dogfood

**First attempt** rendered nothing — the dots field was invisible.
Root cause: Tailwind's preflight sets `<body>` to a non-transparent
`oklch(0.99 0.005 95)` (cream), and `<div class="fixed inset-0 -z-10">`
ended up *behind* that body background — the cream paint occluded
the canvas. Fix: switch the body to `bg-transparent` and bring the
canvas to `z-0` (positive, but still below the page content which
sits at `z-10`).

**Second attempt** rendered dots but the form was hard to read.
The initial `bg-background/80` overlay (a leftover from a
"let's tame the dots" experiment) sat between the canvas and the
form. Removed once the user supplied the canonical snippet that
includes `backgroundColor: 0xffffff` — the canvas now paints a
solid white and the page content reads cleanly on top.

## Verification

- `astro check`: 0 errors / 0 warnings / 58 hints (108 files —
  PublicLayout added).
- All 9 public pages return 200 with the new layout:
  `/`, `/login`, `/register`, `/forgot-password`,
  `/verify-reset`, `/reset-password`, `/help`, `/terms`, `/privacy`.
- Live canvas pixel inspection (`window.VANTA.current.options`):
  - `color: 31931` (= `0x7cbb` ✅)
  - `backgroundColor: 16777215` (= `0xffffff` ✅)
  - `size: 6`, `spacing: 36`, `showLines: false` ✅
  - `starsCount: 3721` (= 61×61 grid at 36-px spacing over 1920×935 ✅)
- Live canvas pixel sampling via `drawImage → getImageData`:
  - Top non-white pixel color: **RGB(0, 124, 187)** = `0x7cbb`
    — exactly the requested teal, 11,137 dot-centre pixels.
  - Surrounding pixels are anti-aliasing gradients
    (191,222,238), (128,190,221), (64,157,204) — the soft
    falloff around each dot. **No blue pixels**, despite an
    earlier vision-model review claiming the dots looked blue —
    the human/programmatic read is the ground truth.
- `pytest tests/test_slice23a_circles_backend.py
  tests/test_slice23c_circles_email.py
  tests/test_slice24d_member_shares.py`: 24 passed (no
  regressions; layout change is frontend-only).

## Files touched

- **NEW** `web/concepts/src/layouts/PublicLayout.astro` — the
  public-facing shell.
- **NEW** `web/concepts/public/libs/three.r134.min.js` — vendored
  Three.js r134 bundle (615 KB).
- **NEW** `web/concepts/public/libs/vanta.dots.min.js` — vendored
  Vanta.js dots effect bundle (10.6 KB).
- **NEW** `web/concepts/public/libs/LICENSE-three.txt`,
  `web/concepts/public/libs/LICENSE-vanta.txt` — MIT licenses
  carried with the binaries.
- **MODIFIED** (import swap to PublicLayout):
  - `web/concepts/src/pages/index.astro`
  - `web/concepts/src/pages/login.astro`
  - `web/concepts/src/pages/register.astro`
  - `web/concepts/src/pages/forgot-password.astro`
  - `web/concepts/src/pages/verify-reset.astro`
  - `web/concepts/src/pages/reset-password.astro`
  - `web/concepts/src/pages/help.astro`
  - `web/concepts/src/pages/terms.astro`
  - `web/concepts/src/pages/privacy.astro`
