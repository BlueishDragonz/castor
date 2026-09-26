/**
 * Authenticated webauthn BFF — delete a passkey.
 *
 * F7: the bearer token is attached server-side from the httpOnly cookie, so it
 * is never exposed to client JavaScript. See credentials/index.ts.
 *
 * DELETE /api/v1/webauthn/credentials/[credentialId]
 */
import type { APIRoute } from 'astro';
import { backendFetch, jsonProxy, requireSession } from '../../../../../lib/auth';

export const DELETE: APIRoute = async ({ cookies, params, request }) => {
  if (!requireSession(cookies)) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  const { credentialId } = params;
  if (!credentialId) {
    return new Response(JSON.stringify({ detail: 'Missing credential id' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  const res = await backendFetch(
    cookies,
    `/auth/webauthn/credentials/${encodeURIComponent(credentialId)}`,
    { method: 'DELETE' },
    request.headers.get('origin'),
  );
  return jsonProxy(res);
};
