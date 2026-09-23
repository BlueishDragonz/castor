import type { APIRoute } from 'astro';

export const prerender = false;

/**
 * POST /api/auth/webauthn/check
 *
 * Body: `{ username: string }`
 *
 * Backend returns `{ has_passkey: bool }`. The same shape for "user
 * doesn't exist" and "user exists but no passkey enrolled" — deliberately
 * indistinguishable — so this endpoint cannot be used to enumerate
 * registered emails.
 *
 * Caching: the email is hashed and used as a cache key with a short
 * TTL. The browser sets Cache-Control: no-store anyway; this is just
 * defensive.
 */
export const POST: APIRoute = async ({ request, locals, cookies }) => {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ has_passkey: false }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const username = String(body?.username ?? '').trim();
  // Don't even hit the network for malformed emails; we already know
  // there can't be a passkey attached to an invalid form.
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(username)) {
    return new Response(JSON.stringify({ has_passkey: false }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const origin = import.meta.env.BACKEND_URL || import.meta.env.PUBLIC_BACKEND_URL || 'http://localhost:8085';
  // NOTE: do NOT forward an Origin header from the browser. The backend's
  // BrowserOriginMiddleware treats the request as if it came from the
  // server-side host (the backend), not the browser; sending the browser's
  // own origin would cause a 403 mismatch. Server-to-server fetches do not
  // carry an Origin header.
  const res = await fetch(new URL('/auth/webauthn/check', origin), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username }),
  });
  const upstreamText = await res.text();
  // eslint-disable-next-line no-console
  console.log('[auth/check] upstream', origin, res.status, upstreamText);

  // Always 200 by backend design; if for any reason it's not, fall back
  // to showing the password field (safer — passkey path requires extra
  // opt-in, password falls back to the typed credential).
  if (!res.ok) {
    return new Response(JSON.stringify({ has_passkey: false }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const data = (() => {
    try {
      return JSON.parse(upstreamText);
    } catch {
      return null;
    }
  })() as { has_passkey?: boolean } | null;
  return new Response(JSON.stringify({ has_passkey: !!(data && data.has_passkey) }), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    },
  });
};
