/**
 * /api/auth/webauthn/recovery-email/verify — server route that
 * proxies the backend's verify endpoint.
 *
 * Two forms:
 *  - Standard verify: `{ email, code }` — proves ownership of the
 *    address, persists it to user.recovery_email.
 *  - Removal: `{ email, code, remove: true }` — clears an existing
 *    recovery email without a code. The current session is the proof
 *    of intent.
 *
 * The backend enforces one-shot consumption: replaying a verify call
 * with the same code returns 400 "Invalid or expired code".
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

  let body: { email?: string; code?: string; remove?: boolean };
  try {
    body = (await request.json()) as { email?: string; code?: string; remove?: boolean };
  } catch {
    return new Response(
      JSON.stringify({ detail: 'Invalid JSON body' }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    );
  }

  // Validate shape: either (email + code) or (remove=true).
  const isRemove = body.remove === true;
  const email = (body.email ?? '').trim().toLowerCase();
  const code = (body.code ?? '').trim();
  if (isRemove) {
    // Email is ignored on the remove path, but the backend requires the
    // field to be syntactically valid. Pass an empty string and the
    // backend discards it.
  } else {
    if (!email) {
      return new Response(
        JSON.stringify({ detail: 'Email is required.' }),
        { status: 400, headers: { 'Content-Type': 'application/json' } },
      );
    }
    if (!/^\d{6}$/.test(code)) {
      return new Response(
        JSON.stringify({ detail: 'Code must be exactly 6 digits.' }),
        { status: 400, headers: { 'Content-Type': 'application/json' } },
      );
    }
  }

  const origin = process.env.BACKEND_URL || 'http://localhost:8085';
  const payload: Record<string, unknown> = { email };
  if (isRemove) {
    payload['remove'] = true;
  } else {
    payload['code'] = code;
  }
  const res = await fetch(new URL('/auth/webauthn/recovery-email/verify', origin), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });

  const text = await res.text();
  return new Response(text, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
