/**
 * /api/auth/webauthn/login/complete — server route for passkey login.
 *
 * The browser's WebAuthn flow does navigator.credentials.get() then
 * POSTs the assertion here. We forward to the backend's
 * /auth/webauthn/login/complete which validates the ceremony and
 * returns {access_token, token_type}. We then mirror the backend's
 * beaver_webauthn Set-Cookie (if present) and write access_token to
 * `castor_token` httpOnly so the session is XSS-resistant.
 *
 * Why a server route instead of client-side fetch + document.cookie:
 *   1. httpOnly can't be set from document.cookie. Client-side scripts
 *      that try end up with a JS-readable bearer — a regression vs.
 *      password login which goes through writeSession().
 *   2. The browser cookie can't carry `Secure` reliably on dev
 *      localhost, so dev WebAuthn login was silently broken.
 *   3. Centralising the cookie write means a future server-side
 *      token_version bump (proper logout) is one place to change.
 */
import type { APIRoute } from 'astro';
import { writeSession, mirrorWebAuthnBrowserCookie } from '../../../../../lib/auth';
import { backendOrigin } from '../../../../../lib/auth';

export const POST: APIRoute = async ({ request, cookies }) => {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, 'Invalid JSON body');
  }

  const origin = backendOrigin();
  const backendRes = await fetch(new URL('/auth/webauthn/login/complete', origin), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  // Capture the backend's beaver_webauthn Set-Cookie (if any) before we
  // consume the body. The backend sets one on the first begin/begin per
  // browser and reuses it for the complete.
  mirrorWebAuthnBrowserCookie(backendRes, cookies);

  if (!backendRes.ok) {
    const text = await backendRes.text();
    return new Response(text, {
      status: backendRes.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const data = (await backendRes.json()) as { access_token?: string; token_type?: string };
  if (!data.access_token) {
    return jsonError(500, 'Backend did not return access_token');
  }

  // F18: derive the display email from the VERIFIED token, never from the
  // request body. The body is attacker-controlled on this unauthenticated
  // route — the browser posts whatever it likes alongside the assertion — so
  // trusting it let a caller choose the identity written into the session
  // cookie. /users/me is bearer-authenticated, so whatever it returns is the
  // principal the backend just proved the assertion belonged to.
  // If the lookup fails we still write the session (the token is valid) and
  // leave the email empty rather than storing an unverified value.
  let username = '';
  try {
    const meRes = await fetch(new URL('/users/me', origin), {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });
    if (meRes.ok) {
      const me = (await meRes.json()) as { email?: unknown };
      if (typeof me.email === 'string' && me.email) {
        username = me.email;
      }
    }
  } catch {
    // Non-fatal: the session is valid regardless of the display name.
  }

  writeSession(cookies, data.access_token, username);

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
};

function jsonError(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
