import type { APIRoute } from 'astro';

export const prerender = false;

/**
 * POST /api/auth/webauthn/offer/dismiss
 *
 * Body: `{ username: string }`
 *
 * Sets `users.passkey_offer_dismissed = true` for the named account.
 * After this fires the login flow never re-prompts the user to enrol a
 * passkey; enrolment remains available on the /security page.
 *
 * Idempotent. Always 200 — the response shape does not leak whether the
 * user exists (matches /api/auth/webauthn/check).
 */
export const POST: APIRoute = async ({ request }) => {
  let body: { username?: string } | null = null;
  try {
    body = (await request.json()) as { username?: string };
  } catch {
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  const username = (body?.username ?? '').trim().toLowerCase();
  if (!username) {
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const origin =
    import.meta.env.BACKEND_URL ||
    import.meta.env.PUBLIC_BACKEND_URL ||
    'http://localhost:8085';
  try {
    const res = await fetch(new URL('/auth/webauthn/offer/dismiss', origin), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    });
    const data = await res.json().catch(() => ({ ok: true }));
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
      },
    });
  } catch {
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
