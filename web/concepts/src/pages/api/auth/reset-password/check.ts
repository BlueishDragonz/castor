/**
 * /api/auth/reset-password/check — BFF proxy to the backend's
 * POST /auth/reset-password/check.
 *
 * The backend endpoint accepts { email, code } and returns
 * { valid: true } if the code matches an unconsumed, unexpired
 * row that points at an active user. Any failure returns a
 * generic 400 to prevent enumeration.
 *
 * We do NOT forward the browser's Origin header — the backend
 * uses BrowserOriginMiddleware and forbids it from non-allowed
 * hosts. The BFF reads identity from the session cookie set
 * during `/login`, but for password reset the user is logged
 * out, so the call is purely "by email + code". The backend
 * endpoint is intentionally public for this reason.
 *
 * Returns the upstream JSON body verbatim with the same status
 * code so the UI can branch on the JSON shape.
 */
export const prerender = false;

import type { APIRoute } from 'astro';
import { backendFetch } from '../../../../lib/auth';

export const POST: APIRoute = async ({ request, cookies }) => {
  let payload: { email?: string; code?: string };
  try {
    payload = (await request.json()) as { email?: string; code?: string };
  } catch {
    return new Response(JSON.stringify({ detail: 'Invalid request body' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  if (!payload.email || !payload.code) {
    return new Response(JSON.stringify({ detail: 'Email and code are required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const res = await backendFetch(cookies, '/auth/reset-password/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: payload.email, code: payload.code }),
  });

  const text = await res.text();
  return new Response(text, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
