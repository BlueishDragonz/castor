/**
 * /api/v1/habits/meta — proxy for sort-persistence (PUT) and read (GET).
 *
 * Backend (castor/routes/api.py:71,77):
 *   GET  /api/v1/habits/meta → {order: "manual"|"name"|"category"}
 *   PUT  /api/v1/habits/meta → updates the user's `order` field
 *
 * Slice 3 shipped the PUT contract (and tests confirmed it works at
 * the FastAPI level). Slice 9-alt added `?order_by=` for GET-time
 * override but not for persistence. The Astro /habits sort menu
 * triggers this endpoint on selection, so the BFF route was needed
 * for the choice to persist across browser sessions.
 */
import type { APIRoute } from 'astro';
import { backendFetch } from '../../../../lib/auth';

export const GET: APIRoute = async ({ cookies }) => {
  const res = await backendFetch(cookies, '/api/v1/habits/meta');
  const body = await res.text();
  return new Response(body, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};

export const PUT: APIRoute = async ({ cookies, request }) => {
  const body = await request.text();
  const res = await backendFetch(cookies, '/api/v1/habits/meta', {
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
