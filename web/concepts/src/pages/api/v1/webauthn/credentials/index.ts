/**
 * Authenticated webauthn BFF — list passkeys.
 *
 * F7: the /security page used to read the session JWT out of
 * `window.__SECURITY_TOKEN__` and attach it as an `Authorization` header from
 * client JavaScript. That directly defeated the httpOnly cookie design, so any
 * XSS anywhere in the app would yield a 30-day bearer token.
 *
 * This route is the replacement: the browser calls it same-origin with
 * credentials, and the httpOnly cookie is attached to the *backend* call
 * server-side by `backendFetch`. The JWT never crosses the wire to the client.
 *
 * GET /api/v1/webauthn/credentials
 */
import type { APIRoute } from 'astro';
import { backendFetch, jsonProxy, requireSession } from '../../../../../lib/auth';

export const GET: APIRoute = async ({ cookies, request }) => {
  const session = requireSession(cookies);
  if (!session) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  // F21: forward the browser's real Origin so the backend's origin allowlist
  // is enforced on this state-changing surface too.
  const res = await backendFetch(
    cookies,
    '/auth/webauthn/credentials',
    { method: 'GET' },
    request.headers.get('origin'),
  );
  return jsonProxy(res);
};
