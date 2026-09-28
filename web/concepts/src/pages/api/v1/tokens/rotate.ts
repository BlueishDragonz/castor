/**
 * Form-action BFF for token rotate: POST /api/v1/tokens/rotate.
 *
 * Backend: POST /api/v1/tokens/rotate (castor/routes/api.py:574) returns
 * the new raw token; old token is invalidated immediately.
 * Slice 7 tests: tests/test_slice7_tokens.py::TestRotateTokenInvalidatesOld.
 */
import type { APIRoute } from 'astro';
import { backendFetch, safeRedirectTarget } from '../../../../lib/auth';

export const POST: APIRoute = async ({ cookies, url }) => {
  const res = await backendFetch(cookies, '/api/v1/tokens/rotate', {
    method: 'POST',
  });
  // F22: `redirect` was used verbatim in the Location header, so
  // POST /api/v1/tokens/rotate?redirect=https://evil.tld returned
  // `303 Location: https://evil.tld?action=fail`. No session was required for
  // the redirect itself, which makes it a phishing primitive that borrows the
  // app's trusted origin. safeRedirectTarget() only permits same-site
  // absolute paths.
  const target = safeRedirectTarget(url.searchParams.get('redirect'));
  const sep = target.includes('?') ? '&' : '?';
  return new Response(null, {
    status: 303,
    headers: {
      Location: `${target}${sep}action=${encodeURIComponent(res.ok ? 'rotated' : 'fail')}`,
    },
  });
};
