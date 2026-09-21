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
 *   - Logout: client clears the cookie. Server cannot invalidate a JWT
 *     without a backend-side revoke endpoint, so the bearer remains
 *     technically valid until lifetime expires (the upstream default is
 *     0 = "until the process is restarted"); a future task is to add
 *     a /auth/logout that bumps the user's token_version.
 */

import type { AstroCookies } from 'astro';

const TOKEN_COOKIE = 'castor_token';
const USERNAME_COOKIE = 'castor_user';

/** Read the bearer token from the request cookie, or null if absent. */
export function readToken(cookies: AstroCookies): string | null {
  return cookies.get(TOKEN_COOKIE)?.value ?? null;
}

/** Persist the bearer token + email after a successful login. */
export function writeSession(
  cookies: AstroCookies,
  token: string,
  email: string,
): void {
  cookies.set(TOKEN_COOKIE, token, {
    httpOnly: true,
    sameSite: 'lax',
    secure: false, // dev; flip to true behind HTTPS in production
    path: '/',
    // Token lifetime is enforced by the upstream JWT strategy.
    maxAge: 60 * 60 * 24 * 7,
  });
  cookies.set(USERNAME_COOKIE, email, {
    httpOnly: false, // visible to client for UI greeting
    sameSite: 'lax',
    secure: false,
    path: '/',
    maxAge: 60 * 60 * 24 * 7,
  });
}

/** Clear the session (logout). */
export function clearSession(cookies: AstroCookies): void {
  cookies.delete(TOKEN_COOKIE, { path: '/' });
  cookies.delete(USERNAME_COOKIE, { path: '/' });
}

/**
 * Forward an authenticated fetch to the backend. If the request has
 * a bearer cookie, attach it as `Authorization: Bearer ...`. Otherwise
 * the call is anonymous.
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
  return fetch(new URL(path, origin), { ...init, headers });
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
