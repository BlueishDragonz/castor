# Deployment boundary and TLS termination (Caddy / Let's Encrypt)

**Status: READY TO INTEGRATE — NOT INTEGRATED.**

Nothing in this repository installs, configures, or depends on Caddy or
Let's Encrypt. There is no Caddyfile, no ACME client, and no certificate
renewal hook. The app runs today on plain HTTP behind whatever the
operator already has, and continues to do so after every change described
here.

This document covers two things: what has been prepared in the
application, and what a future integration would still need to do.

---

## 1. The one setting that decides almost everything

`PUBLIC_URL` (optional, but strongly recommended in production) is the
**externally visible application URL**: the `https://host[:port]` a
browser types to reach Castor.

It is deliberately the single input to every decision that must agree
with the outside world. If it is set, it governs:

| Decision | Where | Effect |
|---|---|---|
| `Secure` flag on the session cookie | `castor/app/users.py:get_cookie_settings` | Set when the public origin is HTTPS |
| `Secure` flag on the WebAuthn browser cookie | `castor/app/webauthn_routes.py` | Same |
| Passkey Relying-Party ID (rpId) | `castor/app/webauthn_routes.py` | Derived from the public host |
| Expected WebAuthn origin | same | Derived from the public origin |
| CSRF `Origin` allowlist | `castor/configs.py:csrf_allowed_origins` | Added automatically |
| HSTS header | `castor/main.py` | Emitted only when the deployment declares HTTPS |

These previously used four different, partly-overlapping rules. They are
now one predicate, `Settings.is_https_public()`, so a cookie can no
longer be issued `Secure` while the passkey ceremony expects a different
host — the mismatch that silently breaks passkeys on a domain change.

**If `PUBLIC_URL` is unset**, the app falls back to `FRONTEND_URL`, then
to the inbound request's own origin. That fallback is correct for local
development and is what makes the app work with no configuration at all.
It is *not* sufficient for a public HTTPS deployment, because the
inbound origin is whatever an untrusted client claims to have used.

---

## 2. Current behaviour without any proxy

```
browser ──HTTP──> host:8080 ──> Astro (0.0.0.0:8080)
                                 └──HTTP──> gunicorn 127.0.0.1:8081
```

- `BACKEND_URL=http://127.0.0.1:8081` — loopback only, not reachable
  externally.
- `FRONTEND_URL=http://10.8.0.1:8080` — the operator-declared public
  origin over the VPN.
- Cookies are issued **without** `Secure` (correct: the transport is HTTP).
- Passkeys work because the Relying-Party ID is derived from the public
  host, not from the loopback backend address.

This is the shape a reverse proxy slots into, unchanged.

---

## 3. Prepared for a future TLS-terminating proxy

Done in this change set, all backward-compatible and all inert until a
proxy is actually placed in front:

1. **Explicit public origin.** `PUBLIC_URL` added, plus
   `is_https_public()`. Replaces three divergent `Secure`-flag
   predicates across the backend and the Astro BFF.

2. **No build-time configuration freezing.** `BACKEND_URL` was read via
   `import.meta.env`, which Vite substitutes at **build** time. The
   container sets `BACKEND_URL` at start-up, long after the image was
   built, so the built-in value silently won — 15 call sites, 6 emitted
   chunks containing a literal `localhost:8085`. Worse, referencing
   `import.meta.env` anywhere made Vite inline the *entire* build-time
   environment into the SSR bundle (observed: `USER`, `TERM`,
   `USERNAME` as literals). All call sites now use
   `backendOrigin()` in `web/concepts/src/lib/auth.ts`, which reads
   `process.env` only. The `ENV BACKEND_URL` in the astro-builder stage
   of the Dockerfile was removed, since it existed only to paper over
   this.

3. **Trusted-proxy header handling is opt-in and off by default.**
   `TRUST_PROXY_HEADERS=false` (the default) means the gunicorn process
   is started **without** `--proxy-headers`, so uvicorn ignores
   `X-Forwarded-Proto` / `X-Forwarded-For` entirely and a client sending
   `X-Forwarded-Proto: https` cannot convince the app it is behind TLS.
   `is_https_public()` therefore never consults a header at all — only
   the operator-declared origin. Setting `TRUSTED_PROXY_IPS='*'` is
   accepted by uvicorn but the backend logs an explicit error at
   start-up, because it re-enables spoofing from any client that can
   reach the socket directly.

4. **HSTS gated on the declared scheme**, not on "not dev". A production
   deployment still on plain HTTP during a migration no longer emits an
   HSTS header that would lock browsers out of the site.

5. **Binds to a local interface.** Backend gunicorn binds
   `127.0.0.1:8081`; only Astro binds `0.0.0.0:8080`. A proxy can reach
   the app without the backend being publicly exposed.

6. **Health endpoint is proxy-safe.** `GET /health` needs no auth, no
   Origin, and no body, so a proxy or orchestrator can probe it.

7. **Timeouts are proxy-friendly.** Gunicorn runs a 30s graceful and 60s
   worker timeout; Astro's standalone server sets its own keep-alive
   timeout. Both exceed any sane proxy read timeout, so the proxy will
   not see a 502 for a slow-but-healthy request.

### Deliberately NOT done

- No `Caddyfile`, no reverse-proxy config file.
- No `PUBLIC_URL` default baked into the image. A default would
  override whatever the deployment actually serves, which is the exact
  failure mode the single-origin change removes.
- No HTTPS enforcement, HTTP→HTTPS redirect, or ACME client.

---

## 4. What a future Caddy integration still requires

None of this is done; it is the checklist for whoever adds it.

1. **DNS.** An A/AAAA record for the chosen domain pointing at the host.
   Let's Encrypt HTTP-01 and TLS-ALPN-01 both need the domain publicly
   resolvable and port 80/443 reachable from the internet.

2. **A real domain — and not an IP address.** Passkeys require a secure
   context, and a bare IP over HTTPS does not give a registrable RP ID.
   This was verified directly rather than assumed: on a dev host served at
   `http://127.0.0.1:4321`, Firefox rejected every ceremony with
   `SecurityError: The operation is insecure` for `rp.id: "127.0.0.1"`,
   and accepted `rp.id: "localhost"` on the same page. A WebAuthn
   relying-party ID must be a registrable domain suffix; browsers reject a
   literal IP.

   **This affects the current apollo deployment.** `docker-compose.yml`
   pins `WEBAUTHN_RP_ID=10.8.0.1`, a bare IP, so passkeys cannot work
   there today — the app is effectively password-only over the VPN. The
   backend now logs an explicit warning at start-up when it detects this.
   Fixing it requires a hostname, which is the same prerequisite as item
   1, so it resolves itself when the domain is chosen. It is not changed
   here because that would alter the live deployment's configuration.

3. **A stable RP ID.** The RP ID must be a registrable suffix of
   the origin. Changing the hostname invalidates every registered
   passkey — users must re-enrol. Choose the final domain before
   launch, and set `PUBLIC_URL=https://<domain>` at the same time.

4. **Set these on the container** at first start with HTTPS:

   ```
   PUBLIC_URL=https://<domain>
   FRONTEND_URL=https://<domain>
   BACKEND_URL=http://127.0.0.1:8081     # unchanged, still loopback
   ```

   `PUBLIC_URL` and `FRONTEND_URL` must match the domain exactly —
   scheme, host, and port. A mismatch produces passkeys that appear to
   register and then fail to sign in, which is the single most
   confusing failure mode in this area.

   Leave `TRUST_PROXY_HEADERS` at its default `false` unless something in
   the app genuinely needs `request.url` to reflect the public origin.
   It does not today: cookies, the WebAuthn origin and the CSRF allowlist
   all read the configured origin. Turning it on adds a spoofing surface
   for no benefit. If you do turn it on, also set `TRUSTED_PROXY_IPS` to
   the proxy's address or CIDR — never `*`.

5. **Proxy configuration.** Forward the whole request to
   `127.0.0.1:8080`; do not split paths between Astro and the backend.
   Astro's middleware proxy already routes `/auth/webauthn/*`, `/users/*`,
   `/webauthn/*` and `/health` onward to the backend, so a single
   catch-all `reverse_proxy` target is sufficient and is the least
   error-prone arrangement.

6. **Header overwrite, not append.** Only relevant if you raised
   `TRUST_PROXY_HEADERS`. The proxy must *set* `X-Forwarded-Proto` and
   `X-Forwarded-Host`, replacing any client-supplied value. With the
   default `false` no proxy cooperation is needed at all.

7. **Verify before declaring done:**
   - `curl -I https://<domain>/health` → 200.
   - `Set-Cookie` on login carries `Secure` and `HttpOnly`.
   - A response header shows `Strict-Transport-Security`.
   - Register a passkey, sign out, sign back in with it.
   - `docker compose logs` shows no `Browser origin not allowed`.

---

## 5. Configuration reference

| Variable | Where | Purpose | Default |
|---|---|---|---|
| `WEBAUTHN_RP_ID` | backend | Bare hostname of the relying party, no scheme or port. Derived from the public origin when left at its default; an explicit value is never overwritten | `localhost` |
| `WEBAUTHN_ORIGIN` | backend | Full expected browser origin, scheme included. Derived from the public origin when left at its default | `http://localhost:8080` |
| `PUBLIC_URL` | backend + Astro | Externally visible app URL; drives Secure cookies, RP ID, CSRF allowlist, HSTS | unset → falls back to `FRONTEND_URL`, then `APP_URL` |
| `FRONTEND_URL` | backend + Astro | Public origin of the browser-facing app. Ignored by the public-origin resolution while it still holds its `http://localhost:4321` default, so the legacy `APP_URL` path stays reachable | `http://localhost:4321` |
| `BACKEND_URL` | Astro only | Internal backend base URL, read at **runtime** | `http://127.0.0.1:8081` |
| `TLS_TERMINATED` | backend + Astro | Legacy switch; still honoured as an HTTPS signal | `false` |
| `TRUST_PROXY_HEADERS` | backend | Start gunicorn with `--proxy-headers` so uvicorn honours `X-Forwarded-*`. Off means forwarded headers are ignored entirely | `false` |
| `TRUSTED_PROXY_IPS` | backend | Hop count or CIDR passed to uvicorn's `--forwarded-allow-ips`. Only read when `TRUST_PROXY_HEADERS=true` | `""` (trust nothing) |
| `CSRF_ALLOWED_ORIGINS` | backend | Extra origins permitted to make state-changing requests | `[]` |
| `REQUIRE_ADMIN_FOR_REGISTRATION` | backend | Gate `/auth/register` behind an admin session | `false` |

None of these are secrets. The secrets are `JWT_SECRET`,
`RESET_PASSWORD_TOKEN_SECRET` and `NICEGUI_STORAGE_SECRET`, which must be
set to strong random values — the backend refuses to start on the `dev`
placeholder.
