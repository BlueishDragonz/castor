/**
 * /api/account/delete — server-side delete with password re-verification.
 *
 * The browser POSTs {password} here. We:
 *   1. Re-verify the password by attempting a fresh /auth/login. The
 *      backend has no /auth/verify-password endpoint, so a real login
 *      is the only reliable gate. If login fails (401/403), reject
 *      without touching the account.
 *   2. Forward DELETE /api/v1/account to the backend using the
 *      existing castor_token cookie. The backend's current_active_user
 *      dependency will recognise the user.
 *   3. Clear the session cookie on success. The Astro page reads the
 *      response and redirects to /.
 *
 * Why a server route and not a client-side fetch chain:
 *   - The browser already has castor_token; we forward it via the
 *     Cookie header so the backend's current_active_user dependency
 *     passes.
 *   - Centralises the verification logic in one place — when the
 *     backend ships /auth/verify-password (planned), only this file
 *     changes.
 *   - Avoids exposing the access_token to JS even momentarily.
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
  const email = cookies.get('castor_user')?.value;
  if (!email) {
    return jsonError(401, 'Session has no associated email');
  }

  // 1. Re-verify the password by attempting a real login.
  const verifyRes = await fetch(new URL('/auth/login', origin), {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ username: email, password }),
  });
  if (!verifyRes.ok) {
    return jsonError(401, 'Password did not match');
  }
  // Discard the login response; the existing session cookie is still valid.

  // 2. Forward the delete with the existing castor_token.
  const deleteRes = await fetch(new URL('/api/v1/account', origin), {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` },
  });

  if (deleteRes.status === 204 || deleteRes.ok) {
    // 3. Clear the session on success. The Astro page redirects to /.
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
