/**
 * Form action for the Astro admin "Trigger backup" button.
 *
 * Astro page: /admin (web/concepts/src/pages/admin.astro)
 * Backend endpoint: POST /api/v1/admin/backup (beaverhabits/app/admin_routes.py)
 *
 * Why a separate Astro route: the form action attribute cannot call
 * a backend URL with a bearer header (the cookie is what authenticates
 * the user, not the form submit). Astro acts as a tiny BFF here:
 * reads the session cookie, forwards as Authorization: Bearer, and
 * returns the result via 303 redirect to the admin page.
 */
import type { APIRoute } from 'astro';
import { backendFetch } from '../../../lib/auth';

export const POST: APIRoute = async ({ cookies, redirect }) => {
  const res = await backendFetch(cookies, '/api/v1/admin/backup', {
    method: 'POST',
  });

  if (res.ok || res.status === 202) {
    return redirect('/admin?backup=ok', 303);
  }
  return redirect('/admin?backup=fail', 303);
};
