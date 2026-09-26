/**
 * Authenticated webauthn BFF — verify a recovery email code.
 *
 * F7: the bearer token is attached server-side from the httpOnly cookie, so it
 * is never exposed to client JavaScript.
 *
 * POST /api/v1/webauthn/recovery-email/verify
 */
import type { APIRoute } from 'astro';
import { backendFetch, jsonProxy, requireSession } from '../../../../../lib/auth';

export const POST: APIRoute = async ({ cookies, request }) => {
  if (!requireSession(cookies)) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  const res = await backendFetch(
    cookies,
    '/auth/webauthn/recovery-email/verify',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: await request.text(),
    },
    request.headers.get('origin'),
  );
  return jsonProxy(res);
};
