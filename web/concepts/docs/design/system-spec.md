# Castor Design System & Layout Spec

> Single source of truth for the visual layer of the new Astro front-end.
> Companion to `docs/plan/migration-plan.md`. Tokens already live in
> `web/concepts/src/styles/tokens.css`; this spec pins values, not just
> names.

## 1. Breakpoints

Two only:

```
mobile:  0–640px
desktop: 641px+
```

No tablet. Tablet users get the desktop layout.

Implementation:
- CSS: `@media (max-width: 640px)` for mobile-only chrome
- React: a tiny `useMediaQuery` hook from `src/lib/use-media-query.ts`
  for conditional rendering
- Layout grid: `grid-cols-1` (mobile) → `md:grid-cols-2` (desktop) — only
  where two-column actually improves readability (settings, habit detail
  sidebar); otherwise single column

## 2. Content width (desktop)

```
65% of viewport, capped, with a min and max
```

Implementation:

```astro
<main class="mx-auto w-full max-w-[min(65vw,1100px)] min-w-[640px] px-4">
  <slot />
</main>
```

- `min(65vw, 1100px)` — 65% on screens up to ~1692px, then capped at 1100px
- `min-w-[640px]` — never narrower than the smallest comfortable reading
  width (so phones in landscape still feel intentional)
- On mobile, the `min-w-[640px]` is overridden by setting the parent to
  `w-screen` and removing the min — verified in `Layout.astro` via a
  `:where(.mobile)` selector or by stacking the layout differently per
  breakpoint

Rationale: 65% leaves room for ambient content (a future "Private Circle
sidebar", an "Activity" panel, background imagery) without forcing an
empty margin on wide screens. Reading line-length stays ≤75ch.

## 3. Tokens

```
:root {
  --background: oklch(1 0 0);
  --foreground: oklch(0.145 0 0);
  --card: oklch(1 0 0);
  --card-foreground: oklch(0.145 0 0);
  --primary: oklch(0.62 0.13 163);            /* emerald — castor brand */
  --primary-foreground: oklch(0.985 0 0);
  --secondary: oklch(0.97 0 0);
  --secondary-foreground: oklch(0.145 0 0);
  --muted: oklch(0.97 0 0);
  --muted-foreground: oklch(0.45 0 0);
  --accent: oklch(0.97 0 0);
  --accent-foreground: oklch(0.145 0 0);
  --destructive: oklch(0.55 0.22 27);          /* red-700 */
  --destructive-foreground: oklch(0.985 0 0);
  --success: oklch(0.65 0.15 145);
  --warning: oklch(0.78 0.15 75);
  --border: oklch(0.92 0 0);
  --input: oklch(0.92 0 0);
  --ring: oklch(0.62 0.13 163);
  --radius: 0.5rem;

  --castor-transition-duration: 150ms;
  --castor-transition-easing: cubic-bezier(0.2, 0.8, 0.2, 1);
}

[data-theme="dark"] {
  --background: oklch(0.145 0 0);
  --foreground: oklch(0.985 0 0);
  --card: oklch(0.18 0 0);
  --card-foreground: oklch(0.985 0 0);
  --primary: oklch(0.72 0.13 163);
  --primary-foreground: oklch(0.145 0 0);
  --secondary: oklch(0.22 0 0);
  --secondary-foreground: oklch(0.985 0 0);
  --muted: oklch(0.22 0 0);
  --muted-foreground: oklch(0.65 0 0);
  --accent: oklch(0.22 0 0);
  --accent-foreground: oklch(0.985 0 0);
  --destructive: oklch(0.45 0.22 27);
  --destructive-foreground: oklch(0.985 0 0);
  --success: oklch(0.7 0.18 145);
  --warning: oklch(0.78 0.15 75);
  --border: oklch(0.25 0 0);
  --input: oklch(0.25 0 0);
  --ring: oklch(0.72 0.13 163);
}
```

Tailwind v4 reads these via the standard shadcn pattern; no
`tailwind.config.js` needed.

## 4. Typography

Self-hosted Inter (no Google Fonts; matches castor main).

```
--font-sans: 'Inter', system-ui, -apple-system, sans-serif;
--font-mono: 'JetBrains Mono', ui-monospace, monospace;

text-xs:    0.75rem / 1.4     (12px)
text-sm:    0.875rem / 1.45   (14px)
text-base:  1rem / 1.55       (16px)
text-lg:    1.125rem / 1.5    (18px)
text-xl:    1.25rem / 1.4     (20px)
text-2xl:   1.5rem / 1.3      (24px)
text-3xl:   1.875rem / 1.2    (30px)
```

Headings: `font-weight: 600`, `letter-spacing: -0.01em`.

## 5. Spacing scale

Tailwind's default (`0.25rem` base) — keep it. Component padding
guidance:
- Card: `p-4` mobile, `p-6` desktop
- Sheet content: `p-4` mobile, `p-6` desktop
- Dialog content: `p-6` (centered, no responsive change)
- Form row gap: `space-y-4`
- Section gap (between cards): `space-y-4` mobile, `space-y-6` desktop

## 6. Component mapping (one library rule)

| Need | Use |
|---|---|
| Button | `<Button variant=...>` from `ui/button` |
| Card / dialog shell | `<Card>`, `<CardHeader>`, `<CardContent>` from `ui/card` |
| Modal | `<Dialog>` from `ui/dialog` |
| Bottom/up/left/right panel | `<Sheet side=...>` from `ui/sheet` |
| Dropdown (desktop) | `<DropdownMenu>` from Radix via `ui/dropdown-menu` |
| Right-click / kebab menu | `<ContextMenu>` via Radix |
| Tabs | `<Tabs>` from `ui/tabs` |
| Toast / notification | `<Alert>` (inline) + `useToast` (transient) |
| Confirm | `<Dialog>` with destructive variant on the action button |
| Form inputs | `<Input>`, `<Label>`, `<Textarea>` from `ui/` |
| Scrollable list | `<ScrollArea>` from `ui/scroll-area` |
| Drag-drop reorder | `@dnd-kit/core` (single dep, no UI lock-in) |

**Do not** introduce a second component library. If a primitive is
missing, write it against Radix + the tokens. The single-library rule
was explicit user feedback; see `references/single-ui-library-criterion.md`
in the modern-spa-on-existing-backend skill.

## 7. Interaction patterns

| Pattern | When | Implementation |
|---|---|---|
| Long-press → context sheet | Habit row, mobile | Pointer-down timer (500ms) → opens `<Sheet side="bottom">` |
| Kebab menu | Habit row, desktop | Right-side `<DropdownMenu>` icon button |
| Drawer | Tag filter, mobile | `<Sheet side="bottom">` with chip grid |
| Modal confirm | Destructive actions | `<Dialog>` centered, single CTA, escape closes |
| Inline edit | Habit name on detail | Click → text input + save on blur / enter |
| Sheet for "More" | Overflow entries on mobile | Sectioned `<Sheet side="bottom">` (Account / Data / Session) |
| Bottom nav | Mobile, primary nav only | `position: fixed; bottom: 0;` with safe-area inset |
| Top app bar | Both | Title left, action icon(s) right |

## 8. Layouts (mobile vs desktop)

### Mobile (≤640px)

```
┌─────────────────────────┐
│  Header (sticky)        │  ← page title, back arrow, kebab
├─────────────────────────┤
│                         │
│  Content                │  ← w-full, px-4, pt-4
│  (single column)        │
│                         │
│                         │
│                         │
├─────────────────────────┤
│  Bottom nav             │  ← fixed, safe-area-inset-bottom
│  Home │ Stats │ ⋮       │
└─────────────────────────┘
```

### Desktop (≥641px)

```
┌────────────────────────────────────────────────────┐
│  Header (sticky)            │  (spacer)           │
│  Title                      │                     │
│                             │   [+ Add] [⋮ menu]  │
├────────────────────────────────────────────────────┤
│                                                    │
│   ┌────── 65% viewport ────────────┐               │
│   │  Content                       │               │
│   │  (max 1100px, min 640px)       │               │
│   │                                │               │
│   └────────────────────────────────┘               │
│                                                    │
│                                                    │
└────────────────────────────────────────────────────┘
```

## 9. Empty / loading / error states

Three explicit states for every data view. Never show a blank page.

```
loading:    Skeleton (shadcn `Skeleton` from `ui/skeleton`)
            + aria-busy="true" on the container
empty:      Centered icon + heading + 1-line copy + primary CTA
            e.g. /habits empty: "No habits yet" + "Add your first habit"
error:      `<Alert variant="destructive">` with retry action
            + aria-live="assertive" for screen readers
```

## 10. Accessibility checklist (every component)

- [ ] All interactive elements reachable via keyboard (Tab order sane)
- [ ] Visible focus ring uses `--ring`
- [ ] `aria-label` on icon-only buttons
- [ ] `aria-live="polite"` for status messages, `aria-live="assertive"` for errors
- [ ] Color contrast ≥ 4.5:1 for body text, ≥ 3:1 for large text — verify
      against the dark theme variant
- [ ] `prefers-reduced-motion` respected: disable sheet/dialog transitions
- [ ] Tap target ≥ 44×44 CSS px on mobile
- [ ] Form fields have associated `<Label htmlFor=...>` (React) or `for=...`
      (Astro native HTML)

## 11. Performance budget

```
LCP   < 1.5s on 4G mobile
CLS   < 0.05
INP   < 200ms
JS bundle per route  < 80KB gzipped (React islands only on pages that need them)
```

shadcn primitives ship with Radix's runtime; tree-shaking via named
imports + `client:visible` for anything that doesn't need to load
immediately.

## 12. PWA (port from `layout.py::pwa_headers`)

In `Layout.astro`:

```html
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />
<meta name="apple-mobile-web-app-title" content="Castor" />
<meta name="application-name" content="Castor" />
<meta name="theme-color" content="#f9f9f9" media="(prefers-color-scheme: light)" />
<meta name="theme-color" content="#121212" media="(prefers-color-scheme: dark)" />
<meta name="mobile-web-app-capable" content="yes" />
<link rel="manifest" href="/manifest.webmanifest" />
```

Manifest at `public/manifest.webmanifest`:

```json
{
  "name": "Castor",
  "short_name": "Castor",
  "start_url": "/habits",
  "display": "standalone",
  "background_color": "#f9f9f9",
  "theme_color": "#19795b",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

## 13. Asset strategy

- Self-hosted: Inter font, app icons, no Google Fonts, no remote images
- Brand mark: inline SVG in `src/components/BrandMark.tsx`
- Image optimization: `astro:assets` with `<Image />` for any raster
- Long-press event: vendored as `public/vendor/long-press.js` so the
  CSP allowlist can use `script-src 'self'` only
