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

/**
 * Detect production from env. Two flags accepted so both Astro-native
 * (PUBLIC_BACKEND_URL) and Docker-compose-style (TLS_TERMINATED) deploys
 * work without coordination.
 */
function isProd(): boolean {
  const backendUrl = process.env.PUBLIC_BACKEND_URL ?? process.env.BACKEND_URL ?? '';
  if (backendUrl.startsWith('https://')) return true;
  if (process.env.TLS_TERMINATED === 'true') return true;
  return false;
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
    maxAge: 60 * 60 * 24 * 7,
  });
  cookies.set(USERNAME_COOKIE, email, {
    httpOnly: false, // visible to client for UI greeting
    sameSite: 'lax',
    secure,
    path: '/',
    maxAge: 60 * 60 * 24 * 7,
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
 * Side effect: if the response carries a `beaver_webauthn` Set-Cookie
 * header, mirror it onto `castor_webauthn_browser` so subsequent
 * requests see it.
 */
export async function backendFetch(
  cookies: AstroCookies,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = readToken(cookies);
  const headers = new Headers(init.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  // The /auth/* and /api/v1/* prefixes are proxied by Astro/Vite to the
  // backend in dev. In production, the same prefixes are forwarded by
  // the reverse proxy in front of both services.
  const origin = process.env.BACKEND_URL || 'http://localhost:8085';
  const res = await fetch(new URL(path, origin), { ...init, headers });
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
