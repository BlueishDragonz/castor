/**
 * /api/account/delete — server-side delete with password step-up.
 *
 * The browser POSTs {password} here. We forward it to the backend's
 * DELETE /api/v1/account together with the existing castor_token.
 *
 * F18 fix — why this file no longer verifies anything itself:
 *
 * The previous implementation re-verified the password by attempting a real
 * login as the address in the `castor_user` cookie, then deleted using
 * `castor_token`. Those are two independent cookies that were never
 * cross-checked, and `castor_user` is attacker-writable (it is set with
 * `httpOnly: false`, and the WebAuthn login route writes it straight from an
 * unauthenticated request-body field). An attacker holding a victim's session
 * token could therefore point `castor_user` at an account they controlled,
 * supply their own password, pass the check, and destroy the victim's
 * account. The password prompt the victim saw was never consulted against
 * their account.
 *
 * The durable fix is to make the step-up bind to the AUTHENTICATED principal,
 * server-side, in the same transaction that erases the data. The backend
 * resolves the principal from the bearer token, verifies the password against
 * that principal's own hash, and erases only that principal's rows. There is no
 * second identity channel here for an attacker to point elsewhere, so this
 * route is now a pure authenticated pass-through.
 *
 * Why still a server route at all: it keeps the bearer in an httpOnly cookie
 * and never exposes it to page JavaScript, and it gives one place to clear the
 * session on success.
 */
import type { APIRoute } from 'astro';
import { readToken } from '../../../lib/auth';

export const POST: APIRoute = async ({ request, cookies }) => {
  const token = readToken(cookies);
  if (!token) {
    return jsonError(401, 'Sign in before deleting your account.');
  }

  let body: { password?: string };
  try {
    body = (await request.json()) as { password?: string };
  } catch {
    return jsonError(400, 'Invalid JSON body');
  }
  const password = body.password ?? '';
  if (!password) {
    return jsonError(400, 'Password is required');
  }

  const origin = process.env.BACKEND_URL || 'http://localhost:8080';

  // The backend binds the password to the account this token resolves to and
  // erases the data in a single transaction (F18 + F24).
  const deleteRes = await fetch(new URL('/api/v1/account', origin), {
    method: 'DELETE',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ password }),
  });

  if (deleteRes.status === 204 || deleteRes.ok) {
    cookies.delete('castor_token', { path: '/' });
    cookies.delete('castor_user', { path: '/' });
    cookies.delete('castor_webauthn_browser', { path: '/' });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // Surface backend detail if present.
  const text = await deleteRes.text();
  return new Response(text || JSON.stringify({ detail: 'Delete failed' }), {
    status: deleteRes.status,
    headers: { 'Content-Type': 'application/json' },
  });
};

function jsonError(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
