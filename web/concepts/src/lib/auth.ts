/**
 * castor auth helpers — shared between server-side Astro pages and
 * client-side islands. Talks to the FastAPI/NiceGUI backend through the
 * Vite proxy (which forwards /auth, /api/v1, /users, /webauthn paths to
 * BACKEND_URL).
 *
 * Auth model:
 *   - Backend issues a JWT bearer on POST /auth/login (form-encoded
 *     username + password).  fastapi-users' default BearerTransport.
 *   - We store the bearer in an httpOnly cookie so client JS never sees
 *     it. XSS-resistant.
 *   - On every server-side fetch to /api/v1/* we re-attach the bearer
 *     via the Authorization header.
 *   - Logout: client clears the cookie. Backend still rejects stale
 *     tokens via the VersionedJWTStrategy's `token_version` check, so
 *     a leaked JWT remains bound to the user's current password epoch.
 *   - WebAuthn browser cookie (`castor_webauthn_browser`): the backend
 *     sets `beaver_webauthn` on /login/begin and /register/begin so a
 *     browser can run a ceremony across multiple tabs. The proxy
 *     round-trips the Set-Cookie header; we mirror the value under a
 *     same-origin name so middleware can see it on subsequent requests.
 */

import type { AstroCookies } from 'astro';

const TOKEN_COOKIE = 'castor_token';
const USERNAME_COOKIE = 'castor_user';
const WEBAUTHN_BROWSER_COOKIE = 'castor_webauthn_browser';
// Backend's own cookie name. The proxy passes through Set-Cookie; we
// listen for this on responses and mirror it under WEBAUTHN_BROWSER_COOKIE
// so that all subsequent same-origin requests (including from middleware)
// carry the value in a single canonical cookie.
const BACKEND_WEBAUTHN_COOKIE = 'beaver_webauthn';

// Cookie max-age aligned with backend JWT lifetime (castor.configs.JWT_LIFETIME_SECONDS=2592000).
// Single source of truth so login + logout + mirror stay in lockstep.
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days

/**
 * Read a server-side configuration value.
 *
 * Astro/Vite expose `.env` values through `import.meta.env`, NOT through
 * `process.env`. Reading process.env.FRONTEND_URL therefore returned
 * undefined even with the variable present in web/concepts/.env, which
 * made publicOrigin() return '' and left backendFetch sending no Origin
 * at all — the backend then rejected every state-changing BFF request
 * with 403 "Browser origin not allowed" and sign-in was impossible.
 * Verified directly:
 *   import.meta.env.FRONTEND_URL = http://localhost:4321
 *   process.env.FRONTEND_URL     = undefined
 *
 * `import.meta.env` is statically replaced by Vite, so it is read
 * literally here rather than through a variable lookup. process.env is
 * still consulted first because that is what a container/CLI env
 * provides.
 */
function serverEnv(key: 'FRONTEND_URL' | 'PUBLIC_BACKEND_URL'): string {
  const fromProcess = process.env[key];
  if (fromProcess) return fromProcess;
  if (key === 'FRONTEND_URL') {
    // Vite statically replaces this member expression.
    return import.meta.env.FRONTEND_URL ?? '';
  }
  return import.meta.env.PUBLIC_BACKEND_URL ?? '';
}

/**
 * The public origin the browser uses to reach this app (scheme://host[:port]).
 *
 * Single source of truth for both `isProd()` and the F21 `Origin` header, so
 * the cookie's `Secure` decision and the CSRF origin assertion can never
 * disagree about which origin is public.
 *
 * BACKEND_URL is deliberately NOT consulted here. In dev the Vite proxy
 * rewrites Host/Origin to the backend, so BACKEND_URL resolved to
 * http://127.0.0.1:8085 and this function returned the *backend's own
 * address* as the browser's origin. The backend's BrowserOriginMiddleware
 * then compared http://127.0.0.1:8085 against an allowlist of frontend
 * origins, found no match, and rejected every state-changing BFF request
 * with 403 "Browser origin not allowed" — so sign-in was impossible in a
 * default checkout. Observed directly via instrumenting the middleware:
 *   [origin-probe] path=/auth/login origin='http://127.0.0.1:8085' rejected=True
 *
 * Returning '' is safe: backendFetch then falls back to inboundOrigin(),
 * which reads the origin off the request actually being served.
 */
function publicOrigin(): string {
  const raw = serverEnv('FRONTEND_URL') || serverEnv('PUBLIC_BACKEND_URL');
  if (!raw) return '';
  try {
    const url = new URL(raw);
    return url.origin;
  } catch {
    // A malformed URL must not become an `Origin: undefined` header.
    return '';
  }
}

/**
 * The origin the *inbound browser request* came from, as seen by this
 * server-side BFF.
 *
 * Used when FRONTEND_URL is not configured. Reading it off the request
 * being served is authoritative — the origin of the request is by
 * definition the origin being acted for — whereas deriving it from
 * BACKEND_URL is wrong: in dev the Vite proxy rewrites Host/Origin to
 * the backend, so the BFF was asserting http://127.0.0.1:8085 as the
 * browser's origin. The backend's BrowserOriginMiddleware then compared
 * that against a list of frontend origins, found no match, and rejected
 * every state-changing request with 403 "Browser origin not allowed",
 * so sign-in was impossible in a default checkout. Confirmed by
 * instrumenting the middleware:
 *   [origin-probe] path=/auth/login origin='http://127.0.0.1:8085' rejected=True
 *
 * The backend still decides whether the origin is allowed; this only
 * makes the assertion possible.
 */
function inboundOrigin(): string {
  const astro = (globalThis as {
    Astro?: { request?: Request; url?: URL };
  }).Astro;
  // Astro.url is derived from the inbound request's Host header, so it
  // reflects the origin the browser actually used.
  const url = astro?.url;
  if (url) return url.origin;
  const header =
    astro?.request?.headers.get('origin') ??
    astro?.request?.headers.get('referer');
  if (!header) return '';
  try {
    return new URL(header).origin;
  } catch {
    return '';
  }
}

/**
 * Detect production from env. Two flags accepted so both Astro-native
 * (PUBLIC_BACKEND_URL) and Docker-compose-style (TLS_TERMINATED) deploys
 * work without coordination.
 *
 * F6: this must reflect the PUBLIC origin the browser actually uses, not the
 * internal backend address. Production runs the backend on
 * BACKEND_URL=http://127.0.0.1:8081 (plain HTTP, loopback-only) while the
 * browser reaches the app over VPN at FRONTEND_URL=http://10.8.0.1:8080.
 * Deriving `Secure` from BACKEND_URL therefore made isProd() false in
 * production and shipped a 30-day session cookie without the Secure flag —
 * the code's stated intent ("Strict + Secure in production") was not what ran.
 * FRONTEND_URL is the operator-declared public origin, so it is the correct
 * input. BACKEND_URL is no longer consulted at all: it is an internal address
 * that says nothing about where the browser is, and using it here previously
 * both shipped a non-Secure production cookie (F6) and made the CSRF
 * Origin assertion name the backend instead of the frontend. A dev deploy
 * with neither var set is correctly treated as non-production.
 */
function isProd(): boolean {
  const origin = publicOrigin();
  if (origin.startsWith('https://')) return true;
  if (process.env.TLS_TERMINATED === 'true') return true;
  return false;
}

/**
 * Constrain a caller-supplied redirect target to a same-site path (F22).
 *
 * Returns `fallback` unless the value is a path that stays on this origin.
 * Rejected:
 *   - absolute URLs (`https://evil.tld`, `//evil.tld` protocol-relative)
 *   - backslash variants the browser normalises to authority
 *     (`/\evil.tld`, `\\evil.tld`) — these are the bypasses that make a naive
 *     "must start with /" check fail
 *   - control characters, which enable header splitting
 *
 * This is the same check `login.astro` already used correctly; it is now
 * shared so the token BFF and login cannot drift apart.
 */
export function safeRedirectTarget(
  value: string | null | undefined,
  fallback = '/',
): string {
  if (typeof value !== 'string' || value === '') return fallback;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return fallback;
  // Must be an absolute path, but not protocol-relative (`//host`).
  if (!value.startsWith('/')) return fallback;
  if (value.startsWith('//')) return fallback;
  // `\` and `/` are equivalent to the WHATWG URL parser for authority
  // detection, so normalise before the check above's sibling test.
  if (value.startsWith('/\\') || value.startsWith('\\\\')) return fallback;
  return value;
}

/**
 * Reject an unauthenticated BFF request with a 401.
 *
 * Returns the session when the request is authenticated, or `null` when it is
 * not. Kept as a helper so every BFF route fails the same way instead of each
 * inventing its own check.
 */
export function requireSession(cookies: AstroCookies): Session | null {
  if (!readToken(cookies)) return null;
  return readSession(cookies);
}

/**
 * Relay a backend JSON response verbatim (status + body + content type).
 *
 * The Authorization header is deliberately NOT copied back: it would leak the
 * backend's auth material into a response the browser can read, which is the
 * exact class of problem F7 is about.
 */
export function jsonProxy(res: Response): Response {
  return new Response(res.body, {
    status: res.status,
    headers: { 'Content-Type': res.headers.get('Content-Type') ?? 'application/json' },
  });
}

/** Read the bearer token from the request cookie, or null if absent. */
export function readToken(cookies: AstroCookies): string | null {
  return cookies.get(TOKEN_COOKIE)?.value ?? null;
}

/**
 * Persist the bearer token + email after a successful login.
 *
 * `SameSite=Lax` (not Strict): passkey begin/complete can do a
 * cross-origin POST as part of a redirect chain; Strict would block
 * those requests. The backend's BrowserOriginMiddleware is the real
 * CSRF gate for state-changing requests.
 *
 * `Secure` is driven by `isProd()` so dev (http://localhost) keeps
 * working without manual env juggling.
 */
export function writeSession(
  cookies: AstroCookies,
  token: string,
  email: string,
): void {
  const secure = isProd();
  cookies.set(TOKEN_COOKIE, token, {
    httpOnly: true,
    sameSite: 'lax',
    secure,
    path: '/',
    // Token lifetime is enforced by the upstream JWT strategy.
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
  cookies.set(USERNAME_COOKIE, email, {
    // F18: httpOnly. This cookie is a display/greeting convenience only — it is
    // never an authorization input. It was previously readable and writable by
    // page JavaScript, which is what let an attacker with a stolen session
    // token redirect the account-deletion step-up check onto an account they
    // controlled. Nothing in the client bundle reads it (grep-verified: the
    // only document.cookie readers touch habit_* display prefs), and the UI
    // greeting is served from Astro.locals.session, so this costs no function.
    httpOnly: true,
    sameSite: 'lax',
    secure,
    path: '/',
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
}

/** Clear the session (logout). */
export function clearSession(cookies: AstroCookies): void {
  cookies.delete(TOKEN_COOKIE, { path: '/' });
  cookies.delete(USERNAME_COOKIE, { path: '/' });
  // Drop the WebAuthn browser cookie so the next user on this browser
  // (e.g. shared device) starts a fresh ceremony context.
  cookies.delete(WEBAUTHN_BROWSER_COOKIE, { path: '/' });
}

/**
 * Detect a stale cookie: the browser still has a `castor_token` cookie
 * (so `Astro.locals.session` is non-null), but the backend no longer
 * recognises it. This happens when the user account was wiped (test
 * fixture cleanup, account deletion) or the JWT was revoked. Returning
 * a plain 401 here lets the user stare at "Could not load (HTTP 401)"
 * forever; clearing the cookie + redirecting to /login gives them a
 * clear path forward.
 *
 * Pages that fetch backend data with the bearer should call this when
 * they get a 401 from a known-authenticated request.
 */
export function clearStaleSession(cookies: AstroCookies): void {
  clearSession(cookies);
}

/**
 * Mirror the backend's WebAuthn browser cookie into our own cookie.
 * The backend sets `beaver_webauthn` on `/auth/webauthn/{login,register}/begin`
 * for concurrent-tab ceremonies. After the proxy round-trips that
 * Set-Cookie back to the browser, the next same-origin request must
 * include it; mirroring under a stable, same-origin name keeps the
 * value visible to middleware and to client-side WebAuthn flows.
 */
export function mirrorWebAuthnBrowserCookie(
  response: Response,
  cookies: AstroCookies,
): void {
  const setCookies = response.headers.getSetCookie?.() ?? [];
  for (const raw of setCookies) {
    const [pair, ...attrParts] = raw.split(';').map(s => s.trim());
    const [name, ...valueParts] = pair.split('=');
    const value = valueParts.join('=');
    if (name !== BACKEND_WEBAUTHN_COOKIE) continue;
    // Parse the backend's cookie attrs and re-apply them with our name.
    let secure = isProd();
    let path = '/';
    let maxAge: number | undefined;
    for (const attr of attrParts) {
      const [k, v] = attr.split('=');
      if (k.toLowerCase() === 'secure') secure = true;
      else if (k.toLowerCase() === 'path') path = v ?? '/';
      else if (k.toLowerCase() === 'max-age' && v) maxAge = Number(v);
    }
    cookies.set(WEBAUTHN_BROWSER_COOKIE, value, {
      httpOnly: true,
      sameSite: 'strict', // WebAuthn ceremonies don't navigate
      secure,
      path,
      maxAge,
    });
    return;
  }
}

/**
 * Forward an authenticated fetch to the backend. If the request has
 * a bearer cookie, attach it as `Authorization: Bearer *** Otherwise
 * the call is anonymous.
 *
 * F21 — this function re-activates the backend's CSRF gate, which was
 * silently inert for all BFF traffic.
 *
 * `BrowserOriginMiddleware` (castor/app/http_security.py) decides on
 * `Origin`/`Referer`, and this helper previously built its headers from
 * scratch and forwarded neither. The middleware therefore saw no Origin AND
 * no Cookie, fell through to the "no evidence" branch, and allowed every
 * request unconditionally — the CSRF layer did nothing on exactly the path it
 * was written to protect, leaving only `SameSite=Lax`.
 *
 * The value forwarded is the *public frontend origin* the BFF is acting on
 * behalf of, taken from FRONTEND_URL. That is the same origin the browser
 * sends, and the one `ALLOWED_ORIGINS` is configured with. Deriving it here
 * — at the single choke point every BFF route already goes through — means a
 * new endpoint added later cannot reintroduce F21 by forgetting to pass
 * anything. It is deliberately not derived from BACKEND_URL: the backend
 * talks to itself over loopback, so that address says nothing about where the
 * browser is, and asserting it would compare the wrong tuple against the
 * allowlist.
 *
 * An explicit `origin` (the real inbound header) takes precedence, for the
 * cases where the caller genuinely has the request in hand.
 *
 * Side effect: if the response carries a `beaver_webauthn` Set-Cookie
 * header, mirror it onto `castor_webauthn_browser` so subsequent
 * requests see it.
 */
export async function backendFetch(
  cookies: AstroCookies,
  path: string,
  init: RequestInit = {},
  origin?: string | null,
): Promise<Response> {
  const token = readToken(cookies);
  const headers = new Headers(init.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const effectiveOrigin = origin ?? publicOrigin() ?? inboundOrigin();
  if (effectiveOrigin) {
    // F21: the backend's origin allowlist can only be evaluated against a
    // real same-origin value. Also send Referer as a fallback for the
    // middleware's `origin if origin is not None else referer` lookup.
    headers.set('Origin', effectiveOrigin);
    if (!headers.has('Referer')) {
      headers.set('Referer', effectiveOrigin);
    }
  }
  // The /auth/* and /api/v1/* prefixes are proxied by Astro/Vite to the
  // backend in dev. In production, the same prefixes are forwarded by
  // the reverse proxy in front of both services.
  const backendOrigin = process.env.BACKEND_URL || 'http://localhost:8085';
  const res = await fetch(new URL(path, backendOrigin), { ...init, headers });
  mirrorWebAuthnBrowserCookie(res, cookies);
  return res;
}

/**
 * Read the currently signed-in user from the JWT. Returns the email we
 * stored at login (cheap, no round-trip) plus the bearer token.
 *
 * For a verified user identity, callers can call /users/me with the
 * bearer; this is left as a future optimisation.
 */
export interface Session {
  email: string;
  token: string;
}

export function readSession(cookies: AstroCookies): Session | null {
  const token = readToken(cookies);
  const email = cookies.get(USERNAME_COOKIE)?.value;
  if (!token || !email) return null;
  return { email, token };
}
