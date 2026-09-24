/**
 * Form-action BFF for token rotate: POST /api/v1/tokens/rotate.
 *
 * Backend: POST /api/v1/tokens/rotate (castor/routes/api.py:574) returns
 * the new raw token; old token is invalidated immediately.
 * Slice 7 tests: tests/test_slice7_tokens.py::TestRotateTokenInvalidatesOld.
 */
import type { APIRoute } from 'astro';
import { backendFetch } from '../../../../lib/auth';

export const POST: APIRoute = async ({ cookies, url }) => {
  const res = await backendFetch(cookies, '/api/v1/tokens/rotate', {
    method: 'POST',
  });
  const target = url.searchParams.get('redirect') || '/tokens';
  const sep = target.includes('?') ? '&' : '?';
  return new Response(null, {
    status: 303,
    headers: {
      Location: `${target}${sep}action=${encodeURIComponent(res.ok ? 'rotated' : 'fail')}`,
    },
  });
};
