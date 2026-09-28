/**
 * Authenticated webauthn BFF — delete a passkey.
 *
 * F7: the bearer token is attached server-side from the httpOnly cookie, so it
 * is never exposed to client JavaScript. See credentials/index.ts.
 *
 * DELETE /api/v1/webauthn/credentials/[credentialId]
 *
 * The request body is streamed through untouched, exactly as
 * change-password.ts does. That body carries the user's fresh account
 * password — the step-up proof the backend requires before it will delete
 * a credential. Omitting it here made every deletion fail with a 422
 * "Field required", so the UI's Remove button could never succeed.
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
    {
      method: 'DELETE',
      // `request.text()` on an empty body returns '', which fetch() sends
      // as a zero-length body — the backend then reports the missing field
      // in its own 422 instead of this route inventing a 400.
      headers: { 'Content-Type': 'application/json' },
      body: await request.text(),
    },
    request.headers.get('origin'),
  );
  return jsonProxy(res);
};
