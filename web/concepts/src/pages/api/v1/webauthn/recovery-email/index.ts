/**
 * Authenticated webauthn BFF — recovery email.
 *
 * F7: the bearer token is attached server-side from the httpOnly cookie, so it
 * is never exposed to client JavaScript.
 *
 *   GET  /api/v1/webauthn/recovery-email  → current recovery address
 *   POST /api/v1/webauthn/recovery-email  → set a new one
 */
import type { APIRoute } from 'astro';
import { backendFetch, jsonProxy, requireSession } from '../../../../../lib/auth';

const UNAUTHENTICATED = () =>
  new Response(JSON.stringify({ detail: 'Not authenticated' }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  });

export const GET: APIRoute = async ({ cookies, request }) => {
  if (!requireSession(cookies)) return UNAUTHENTICATED();
  const res = await backendFetch(
    cookies,
    '/auth/webauthn/recovery-email',
    { method: 'GET' },
    request.headers.get('origin'),
  );
  return jsonProxy(res);
};

export const POST: APIRoute = async ({ cookies, request }) => {
  if (!requireSession(cookies)) return UNAUTHENTICATED();
  const res = await backendFetch(
    cookies,
    '/auth/webauthn/recovery-email',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: await request.text(),
    },
    request.headers.get('origin'),
  );
  return jsonProxy(res);
};
