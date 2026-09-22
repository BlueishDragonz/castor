# Castor — Astro front-end

This directory is the new front-end for Castor, the active fork of
`daya0576/beaverhabits`. The Python backend (under `../../beaverhabits/`)
stays; only the user-facing UI has been replaced.

## Where to start

Read in this order — they are the contract for everything else:

1. **`docs/architecture/00-overview.md`** — what Castor is, what's
   shipped, what's in flight, what diverges from `castor main`. The
   divergence list (D1–D16) is the work queue.
2. **`docs/plan/migration-plan.md`** — phased migration plan, slice
   ordering, rollback strategy, Private Circle design, slice-by-slice
   tasks. This is what you work from when picking the next task.
3. **`docs/security/baseline.md`** — security posture, header policy,
   cookie policy, CI gate, pre-deploy checklist. Run the checklist
   before every deploy.
4. **`docs/design/system-spec.md`** — design tokens, breakpoints, the
   65% viewport width rule, component mapping, accessibility, PWA.

## Status

```
P0 mobile nav chrome    ✅ mostly shipped (gap on D13)
P0 multi-day grid       🟡 in flight
P1 auth flows           ✅ shipped
P1 settings             ✅ shipped (D14 backend gap deferred)
P1 habit detail         ✅ shipped (long-press notes deferred to P2)
P1 import/export        ✅ shipped (backend POST /habits/import gap documented)
P2 polish               not started
P2 Private Circle       not started
P3 decoupling           not started

Last slice shipped: P1 habit detail (commit pending). Next: P2 polish (long-press notes, tag filter chips, calendar heatmap polish) or P2 Private Circle.
```

`pnpm exec astro check` reports 0 type errors. CI gate rejects on type errors.

## Local dev

```bash
# Backend (Castor main, on apollo over VPN)
ssh apollo "/usr/bin/docker exec wg-easy wget -q -O- http://10.8.0.1:8080/health"

# Front-end
cd web/concepts
pnpm install --frozen-lockfile
BACKEND_URL=http://localhost:8085 pnpm dev    # if backend tunneled to localhost
# or, with the backend on apollo via VPN, set BACKEND_URL=http://10.8.0.1:8080
```

The dev server runs on `http://localhost:4321` by default
(see `astro.config.mjs`).

## Conventions

- TypeScript `^5.9.3` only — TS 7 silently breaks `@astrojs/check`.
- `astro/tsconfigs/base` + `strict: true` — not `astro/tsconfigs/strict`.
- One UI library (shadcn/ui). No second-component-library PRs.
- Server-side fetches for writes; client-side fetches only for things
  that need the user's browser (WebAuthn, file upload).
- Don't `:latest` from Docker Hub. Build a local tag, pin in
  `/opt/wg-net/docker-compose.yml`.
