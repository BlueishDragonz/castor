/**
 * /api/auth/webauthn/recovery-email — server route that proxies the
 * backend's recovery-email request endpoint.
 *
 * The backend's POST /auth/webauthn/recovery-email requires an active
 * session (the user must already be signed in). We forward the
 * castor_token cookie in the Authorization header; the response is a
 * 200 + a `{ pending, email, expires_at }` envelope, OR a 4xx with
 * { detail } on rate-limit / validation failure.
 *
 * Rate limits are enforced server-side (5 requests / 5 minutes per IP
 * AND per user); a 429 here propagates as-is to the client.
 */
import type { APIRoute } from 'astro';
import { readToken } from '../../../../../lib/auth';

export const POST: APIRoute = async ({ request, cookies }) => {
  const token = readToken(cookies);
  if (!token) {
    return new Response(
      JSON.stringify({ detail: 'Sign in before managing a recovery email.' }),
      { status: 401, headers: { 'Content-Type': 'application/json' } },
    );
  }

  let body: { email?: string };
  try {
    body = (await request.json()) as { email?: string };
  } catch {
    return new Response(
      JSON.stringify({ detail: 'Invalid JSON body' }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    );
  }

  const email = (body.email ?? '').trim().toLowerCase();
  if (!email) {
    return new Response(
      JSON.stringify({ detail: 'Email is required.' }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    );
  }

  const origin = process.env.BACKEND_URL || 'http://localhost:8085';
  const res = await fetch(new URL('/auth/webauthn/recovery-email', origin), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ email }),
  });

  const text = await res.text();
  return new Response(text, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
