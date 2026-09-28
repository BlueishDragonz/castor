/**
 * Authenticated webauthn BFF — change password.
 *
 * F7: the bearer token is attached server-side from the httpOnly cookie, so it
 * is never exposed to client JavaScript.
 *
 * The request body (containing the old and new passwords) is streamed through
 * untouched; only the auth material is added server-side.
 *
 * POST /api/v1/webauthn/change-password
 */
import type { APIRoute } from 'astro';
import { backendFetch, jsonProxy, requireSession } from '../../../../lib/auth';

export const POST: APIRoute = async ({ cookies, request }) => {
  if (!requireSession(cookies)) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  const res = await backendFetch(
    cookies,
    '/auth/webauthn/change-password',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: await request.text(),
    },
    request.headers.get('origin'),
  );
  return jsonProxy(res);
};
