/**
 * Form-action BFF for token lifecycle (Create + Rotate + Revoke).
 *
 * The /tokens page submits HTML forms. Each form action calls one of:
 *   POST   /api/v1/tokens          → create
 *   POST   /api/v1/tokens/rotate   → rotate
 *   POST   /api/v1/tokens          with ?_method=DELETE → revoke
 *
 * The previous implementation submitted directly to BACKEND_URL:8085,
 * which (a) leaked the backend origin to the browser, (b) failed
 * silently because cookies set by Astro on :4321 are NOT sent to
 * :8085 (different origin → 401), and (c) breaks in production
 * where there's only one ingress.
 *
 * This file handles all three actions with a single POST handler
 * because Astro routes dispatch on method+path, and the form actions
 * collapse to POST /api/v1/tokens with optional ?_method=DELETE or
 * /api/v1/tokens/rotate. We split into separate exported handlers per
 * file rather than one switch, because Astro's file-based routing
 * matches one path per file under the directory.
 *
 * Backend contract (slice 7): tests/test_slice7_tokens.py.
 */
import type { APIRoute } from 'astro';
import { backendFetch } from '../../../../lib/auth';

function redirectBack(url: URL, fallback: string, action?: string): Response {
  const target = url.searchParams.get('redirect') || fallback;
  const sep = target.includes('?') ? '&' : '?';
  const finalAction = action ?? url.searchParams.get('action') ?? 'updated';
  return new Response(null, {
    status: 303,
    headers: { Location: `${target}${sep}action=${encodeURIComponent(finalAction)}` },
  });
}

/** POST /api/v1/tokens — Create a new token. */
export const POST: APIRoute = async ({ cookies, url }) => {
  // If the form posts to /api/v1/tokens?_method=DELETE, we forward DELETE.
  // (Astro forms can't natively send DELETE.)
  if (url.searchParams.get('_method')?.toUpperCase() === 'DELETE') {
    const res = await backendFetch(cookies, '/api/v1/tokens', { method: 'DELETE' });
    return redirectBack(url, '/tokens', res.ok ? 'revoked' : 'fail');
  }
  const res = await backendFetch(cookies, '/api/v1/tokens', { method: 'POST' });
  return redirectBack(url, '/tokens', res.ok ? 'created' : 'fail');
};

/** GET /api/v1/tokens — JSON proxy used by the /tokens page on load. */
export const GET: APIRoute = async ({ cookies }) => {
  const res = await backendFetch(cookies, '/api/v1/tokens');
  const body = await res.text();
  return new Response(body, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
