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

export const POST: APIRoute = async ({ request, cookies }) => {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, 'Invalid JSON body');
  }

  const origin = process.env.BACKEND_URL || 'http://localhost:8085';
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

  // Extract username from the request body if present so we can write
  // the email cookie for the UI greeting. The request body schema is
  // { username, id, rawId, type, response } per the backend contract.
  const username =
    typeof body === 'object' && body !== null && 'username' in body
      ? String((body as { username?: unknown }).username ?? '')
      : '';

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
