# Repo alignment — 2026-09-26 (slice 30): D1–D5 post-cutover cleanup

**Branch**: `release/astro-migration` @ `577aaf4`
**Production**: untouched throughout (`castor Up 16 hours (healthy)`,
image `castor:custom-2026-09-26`, no redeploy)
**Result**: all five audit decisions executed.

This slice executes the D1–D5 decisions taken after the repo-alignment
audit. It is repo hygiene, not migration work — the Astro app was already
live and serving. The one functional change is `robots.txt`, which
previously 404'd.

## Commits

| Commit | Decision | Content |
|---|---|---|
| `f7848ac` | D2 (B) | `Desktop/migration/` → `docs/migration/` (48 files) |
| `5bcd6fe` | D5 (A) | audits filed under `docs/migration/`, `.hermes/` ignored |
| `4af7f3c` | D1 (C) | ENABLE_PLAN cluster removed, robots.txt moved to Astro |
| `577aaf4` | D3 (A) | CI triggers on push to main, not just PRs |
| (no commit) | D4 (C→A) | apollo image hygiene — 14 images deleted, 2 retained |

## D1 — the ENABLE_PLAN cluster (removed)

`ENABLE_PLAN` has never been enabled on apollo, and Paddle billing was
dropped during the migration (parity 12.3). Removed:

- `castor/plan/{paddle,plan}.py` — Paddle billing
- `castor/routes/astro.py` — only mounted the `statics/astro` landing page
- `statics/astro` submodule + `.gitmodules` — upstream's separate
  landing-page repo (`daya0576/beaverhabits-landing`), never built here
- `statics/{sitemap,sitemap_index}.xml` — hardcoded `beaverhabits.com`
  URLs that no longer describe this deployment
- `statics/images/{favicon,apple-touch-icon}*` — Astro ships its own
- `docker/Dockerfile:101` `COPY statics ./statics` — the last `statics/`
  consumer. Left in place it would have **failed the build** on a
  missing directory, not silently degraded.

`robots.txt` was **moved, not deleted**: `statics/robots.txt` →
`web/concepts/public/robots.txt`, rewritten for the current route surface
(`/habits`, `/circles`, `/security`, `/tokens`, `/account` disallowed;
the old `beaverhabits.com` sitemap pointers and `/demo` allowance dropped).

`ENABLE_PLAN` and the `PADDLE_*` entries stay in `configs.py` as
upstream-merge ballast. Nothing reads them.

### robots.txt verification

Astro's `output: 'server'` puts public assets in `dist/client/`, not
`dist/` — worth knowing, because a naive `ls dist/robots.txt` looks like
the move failed. Verified end to end against the standalone server:

    /robots.txt            → 200 text/plain, Castor content
    /manifest.webmanifest  → 200 (control: pre-existing public asset)
    /sitemap.xml           → 404 (correctly gone)

`docker/Dockerfile:105` copies all of `dist/`, so `dist/client/robots.txt`
ships. **This is not live in production yet** — it ships on the next
image build.

## D2 — migration record relocated

`Desktop/migration/` → `docs/migration/`, 48 files via `git mv` (rename
detected, history preserved). The `Desktop/` prefix was a local-sync path
accident; the original migration spec named `docs/migration/`.

Updated: both audit scripts' run-instructions and `MATRIX` constant,
plus `parity-matrix.md` / `PROGRESS.md` / `dogfood-2026-09-25.md`.

**Left alone deliberately:** 17 files under `slice-reports/` still say
`Desktop/migration/`. Those record commands as they were run at the time;
rewriting them would falsify the migration record.

Both scripts re-run green against the new path.

## D3 — CI now runs on push to main

`pr.yml` fired on `pull_request` only. This repo is pushed to directly, so
all four post-cutover commits landed with zero CI verification — which is
exactly why the latent CI bug fixed on 2026-09-25 (23 spurious failures
from env overrides in `pre-deployment-test.yml`) survived undetected: CI
never ran on the path actually used.

Now triggers on `pull_request` **and** `push: branches: [main]`. The three
jobs (pytest, astro check+build, smoke) are unchanged.

## D4 — apollo image hygiene

Deleted 14 images, all provably dead:

- 13 × `castor-beaverhabits:*` (531MB each, 8–9 days old) — the sunset
  NiceGUI stack. No container referenced them; no file in `/opt` names
  them.
- `castor:custom-2026-09-25` (1.5GB) — superseded same-day by the live
  image, unreferenced by any container.

**Retained (rollback set, per decision C-then-A):**

- `castor:custom-2026-09-26` — live
- `castor:custom-2026-09-14` (528MB) — last known-good legacy image
- `/opt/castor/docker-compose.yml.legacy-20260926`
- `beaverhabits_beaver_data` volume (live data, 15 users)
- `habits.db.pre-cutover-20260926T023424Z` inside the volume

Disk: 19G → 18G used (45G total, 40%). ~1G reclaimed net — the legacy
images shared layers with each other, so the naive 8GB estimate was
optimistic. Worth recording: the VM was never under real pressure here.

## D5 — root-level files

- `AUDIT-2026-09-23.md` → `docs/migration/audit-2026-09-23-branch-vs-main.md`
- `AUDIT-2026-09-26-SECURITY-RELIABILITY.md` →
  `docs/migration/audit-2026-09-26-production-security.md`
- `.hermes/` added to `.gitignore` (agent scratch, not repo content)

The 09-23 audit predates the migration and is the evidence base for
several parity-matrix claims, so it belongs with the migration record.

## Gate

    pytest tests/ --ignore=tests/test_batch4_live.py -q
    → 392 passed, 12 skipped  (byte-identical to cutover baseline)

    pnpm exec astro check  → 0 errors, 0 warnings, 58 hints
    pnpm exec astro build  → clean

## Open items (not this slice)

1. **Passkey rpId re-registration is still unverified.** The last audit
   event in production is `2026-09-26 02:51:53` — my own probe account.
   No real user has logged in since cutover. The `WEBAUTHN_RP_ID`
   correction (`localhost` → `10.8.0.1`) is the last cutover risk that
   only a real device login can clear.
2. **F18 (Critical) from the production security audit** — confirmed
   authorisation bypass on `api/account/delete.ts`. Unfixed. It does not
   wait for a bad day.
3. **Off-host backup** — the only production change in the audit with
   unbounded cost of delay.
4. `.dockerignore` lacks `node_modules`, and the Dockerfile copies
   `web/concepts` after `pnpm install --frozen-lockfile`. The running
   image is clean; any build from a dev checkout would not be.
5. `Node 22.12.0` (~21 months stale) is PID 1 on the public port.
