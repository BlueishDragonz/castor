/**
 * /api/v1/user-configs — proxy to backend /api/v1/user-configs with auth.
 *
 * Backend endpoints (castor/routes/api.py):
 *   GET  /api/v1/user-configs → dict (may be empty {})
 *   PUT  /api/v1/user-configs {custom_css?, show_streak?, show_total?,
 *                              date_reverse?} → updated dict
 *
 * Slice 11 shipped the backend contract (UserConfigsUpdate model, CSS
 * sanitiser); slice 12 added 3 boolean display prefs. The Astro UI
 * (SettingsClient.tsx) PUTs these keys on toggle change. This BFF route
 * is what slice 15 added to close the gap caught by manual inspection
 * — slice tests passed because they hit the FastAPI endpoint directly
 * via httpx, never via the Astro BFF path.
 *
 * Slice 11 backend tests: tests/test_slice11_user_configs.py (13 tests).
 * Slice 12 backend tests: tests/test_slice12_display_prefs.py (10 tests).
 */
import type { APIRoute } from 'astro';
import { backendFetch } from '../../../lib/auth';

export const GET: APIRoute = async ({ cookies }) => {
  const res = await backendFetch(cookies, '/api/v1/user-configs');
  const body = await res.text();
  return new Response(body, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};

export const PUT: APIRoute = async ({ cookies, request }) => {
  const body = await request.text();
  const res = await backendFetch(cookies, '/api/v1/user-configs', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body,
  });
  const responseBody = await res.text();
  return new Response(responseBody, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
