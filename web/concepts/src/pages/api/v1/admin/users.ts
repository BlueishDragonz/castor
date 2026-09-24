/**
 * /api/v1/admin/users — admin-only JSON list of users.
 *
 * Backend (castor/app/admin_routes.py:30): GET /api/v1/admin/users
 * returns `[{id, email, created_at, last_login, is_superuser}]` for the
 * current_admin_user. Slice 8 pinned this contract (9 tests in
 * tests/test_slice8_admin.py).
 *
 * The /admin page renders this list. Slice 8 didn't ship the Astro BFF
 * proxy, so the page would 404 if anything called /api/v1/admin/users
 * from the browser. (Today the /admin page reads user info from
 * Astro.locals + a direct backendFetch, not the /api path — but the
 * proxy is still needed for any client-side revalidation or admin
 * tooling that hits the canonical path.)
 */
import type { APIRoute } from 'astro';
import { backendFetch } from '../../../../lib/auth';

export const GET: APIRoute = async ({ cookies }) => {
  const res = await backendFetch(cookies, '/api/v1/admin/users');
  const body = await res.text();
  return new Response(body, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
