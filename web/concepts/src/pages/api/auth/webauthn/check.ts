import type { APIRoute } from 'astro';

export const prerender = false;

/**
 * POST /api/auth/webauthn/check
 *
 * Body: `{ username: string }`
 *
 * Probes whether a Castor's account has a registered passkey, and
 * whether the post-login "Set up a passkey?" offer has been dismissed.
 *
 * The endpoint is intentionally IDEMPOTENT and leak-free: a response
 * with `has_passkey: false` does NOT distinguish "user not registered"
 * from "user registered but no passkey". The frontend MUST treat both
 * shapes identically.
 */
export const POST: APIRoute = async ({ request }) => {
  let body: { username?: string } | null = null;
  try {
    body = (await request.json()) as { username?: string };
  } catch {
    return new Response(
      JSON.stringify({ has_passkey: false, passkey_offer_dismissed: false }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }
  const username = (body?.username ?? '').trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(username)) {
    return new Response(
      JSON.stringify({ has_passkey: false, passkey_offer_dismissed: false }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }

  const origin =
    import.meta.env.BACKEND_URL ||
    import.meta.env.PUBLIC_BACKEND_URL ||
    'http://localhost:8085';
  try {
    const res = await fetch(new URL('/auth/webauthn/check', origin), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    });
    const data = await res
      .json()
      .catch(() => ({ has_passkey: false, passkey_offer_dismissed: false }));
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
      },
    });
  } catch {
    return new Response(
      JSON.stringify({ has_passkey: false, passkey_offer_dismissed: false }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }
};
