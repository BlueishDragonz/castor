import type { APIRoute } from 'astro';
import { backendOrigin } from '../../../../../lib/auth';

export const prerender = false;


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

  // Resolved at runtime, not build time — see backendOrigin().
  const origin = backendOrigin();
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
