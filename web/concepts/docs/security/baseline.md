# Castor Security Baseline

> Read this before every phase deploy. The pre-deploy checklist
> (bottom of the file) is what you actually run; the rest is the
> "why".

## What we already have (must keep)

Every item below exists in `castor main` (the fork). The Astro
front-end must NOT regress any of them.

| Control | Where | Behaviour |
|---|---|---|
| Token-versioned JWT | `beaverhabits/app/users.py` `VersionedJWTStrategy` | Every password change bumps `User.token_version` in the same SQL UPDATE; `read_token` re-SELECTs and rejects mismatched JWT `ver` claims |
| 12-char password policy | `UserManager.validate_password` | Register, change-password, reset, webauthn-credential-delete confirmation all require ≥12 |
| Browser-origin CSRF | `beaverhabits/app/http_security.py` `BrowserOriginMiddleware` | Cross-origin browser writes rejected; native clients (no Origin header) pass; engine.io handshake guarded |
| WebAuthn self-binding | `beaverhabits/app/webauthn_routes.py:58` `_self_binding` | Passkey register/login must be for the signed-in email |
| Stable browser cookie | `beaverwebauthn` cookie | One cookie per browser; per-tab concurrent registration/login work; not rotated per begin |
| Audit log | `beaverhabits/app/audit.py` | Every register/login/change/delete/passkey event recorded |
| Cookie attributes | `get_cookie_settings` | `httponly=True`, `samesite=strict` (backend), `secure=APP_URL.startswith("https://") or TLS_TERMINATED` |
| Custom CSS sanitizer | `css_sanitizer.py` | TinyCSS2-backed; rejects unsupported stylesheets entirely |
| Integrity check | `integrity.py` + `habits.db.integrity.json` | Post-deploy verify |

## What the Astro front-end adds (must keep)

| Control | Where | Behaviour |
|---|---|---|
| httpOnly bearer cookie | `web/concepts/src/lib/auth.ts` `writeSession` | `castor_token` httpOnly; client JS never sees it |
| SameSite=Lax | `lib/auth.ts` | Lax (not Strict) so passkey begin/complete redirect chains work; backend's BrowserOriginMiddleware is the real CSRF gate |
| Server-side `backendFetch` | `lib/auth.ts` | All writes go server-side; client never holds the bearer |
| `Astro.locals.session` | `src/middleware.ts` | Populated from cookies once per request; pages read it |
| PWA meta tags | `Layout.astro` (TBD) | apple-touch-icon, theme-color, manifest |

## What we add in Phase 1 (security baseline, this doc)

### Astro security headers (`src/middleware.ts`)

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'unsafe-inline';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data:;
  font-src 'self';
  connect-src 'self';
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

When Umami (D10) ships, add its origin to `script-src` and
`connect-src`.

### Cookie policy (`src/lib/auth.ts`)

```ts
const isProd = () =>
  (import.meta.env.PUBLIC_BACKEND_URL ?? '').startsWith('https://')
  || process.env.TLS_TERMINATED === 'true';

// castor_token: bearer JWT
// httpOnly (no XSS access), SameSite=Lax (passkey redirect chains),
// Secure when behind HTTPS, Path=/, Max-Age=7d

// castor_user: DROPPED. UI greeting comes from Astro.locals.session.email,
// which is populated by middleware from a signed token check, not from
// a client-readable cookie. Removes one cookie-attack surface.

// castor_webauthn_browser: bridge for WebAuthn ceremonies
// httpOnly, SameSite=Strict (no nav during ceremony), Secure when
// behind HTTPS, Path=/, Max-Age=30d
```

### Dependency & supply chain

- `pnpm install --frozen-lockfile` in CI (lockfile drift = build
  fail)
- `pnpm audit --audit-level=high` in CI (high/critical = build fail)
- `package.json` versions are explicit majors; no `latest` or wildcards
- All `@radix-ui/*` versions pinned to a known-compatible set with
  React 19 (dialog/popper 1.x, context-menu 2.x, scroll-area 1.2+)
- TypeScript pinned to `^5.9.3` (TS 7 breaks `@astrojs/check@0.9`)

### CI gate (`.github/workflows/security.yml`)

```yaml
name: security
on: { push: { branches: [main] }, pull_request: {} }
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
      - name: header smoke
        run: |
          pnpm exec astro dev &
          sleep 8
          curl -sI http://localhost:4321/ \
            | grep -E '^(Content-Security-Policy|Strict-Transport-Security|X-Content-Type-Options|X-Frame-Options|Referrer-Policy|Permissions-Policy):' \
            && echo OK || (echo FAIL; exit 1)
```

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Cookie `Secure` flip breaks dev (HTTPS in dev is awkward) | Low | Env-driven `isProd()`; dev defaults to `false` |
| CSP blocks Astro's inline scripts | High | `script-src 'unsafe-inline'` until we move to external files; track via TODO |
| Long-press event library loads from `/statics/libs/` and breaks CSP | High | Port to a small inline `<script>` or a vendored file under `public/` with a CSP `script-src` hash/nonce |
| Backend mount path drift (`/api/v1/...` vs `/auth/...`) | Medium | Verify each endpoint against `app.routes` at runtime, not from memory; see D1 in `docs/architecture/00-overview.md` |
| Astro 7 + React 19 + Radix major drift | Medium | Pin `@radix-ui/*` versions in `package.json`; CI audit catches upgrades |
| `secure` cookie + HTTP-only deploy | Low | Apollo deploys over WireGuard at `http://10.8.0.1:8080` — `Secure` would break. Until Astro is behind HTTPS at the proxy, leave `secure=false`. Document the dev/prod delta clearly. |

## Pre-deploy checklist (run before every phase deploy)

```
[ ] pnpm audit --audit-level=high → 0 findings
[ ] pnpm exec astro check → 0 errors, 0 warnings
[ ] git status clean on migration-to-shadcn-astro (no untracked .vscode/, no scaffold leftovers)
[ ] No secrets in committed .env / .env.local / .env.production (git grep -E '(SECRET|TOKEN|PASSWORD)' web/concepts/src → 0 hits)
[ ] Cookie attributes match isProd() (lib/auth.ts readSession in dev console)
[ ] Security headers present on /habits (curl -sI localhost:4321/habits)
[ ] Backend /health returns 200 over VPN (ssh apollo "/usr/bin/docker exec wg-easy wget -q -O- http://10.8.0.1:8080/health")
[ ] Smoke: register → login → add habit → tick → logout → re-login works
[ ] Mobile UA (devtools) → BottomNav visible, ⋮ opens MoreSheet with all 5 entries
[ ] Desktop UA (devtools) → DesktopMenu in app bar, all entries navigate
[ ] /security → add passkey works (over VPN: rpId must be 10.8.0.1)
[ ] /security → remove passkey works (password confirm)
[ ] Image tag in /opt/wg-net/docker-compose.yml is a locally-built castor:custom-YYYY-MM-DD, NOT :latest
[ ] Image tag, source on apollo (/opt/castor), and migration branch all match
```

## Open security questions for the user

1. **Astro deployment surface**: is the Astro front-end deployed on
   apollo (same host as the backend, same wg-easy netns, port 4321) or
   on a separate origin? This affects the CSP `connect-src`, the
   cookie `Domain`, and the `secure` flag.
2. **Umami self-host or hosted**: if self-hosted, what origin? If
   hosted, what domain? The CSP must allow it explicitly.
3. **Reverse proxy choice**: nginx, caddy, or traefik in front of
   both backend and Astro? The headers policy assumes the proxy
   does NOT add its own HSTS/CSP that could conflict.
4. **TLS termination at the proxy or directly on Astro**: if at the
   proxy, Astro's `secure` cookie flag stays `false` and the proxy
   sets `Strict-Transport-Security`; if direct on Astro, both layers
   must agree.
