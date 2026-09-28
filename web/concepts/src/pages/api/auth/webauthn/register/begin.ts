/**
 * /api/auth/webauthn/register/begin — server route for the first half
 * of a passkey registration ceremony. The browser's WebAuthn flow
 * does `navigator.credentials.create()` with the public-key options
 * returned here, then POSTs the attestation to
 * /api/auth/webauthn/register/complete.
 *
 * The backend's /auth/webauthn/register/begin requires an
 * authenticated session (the user must be already signed in to
 * enroll a passkey). We forward whatever Authorization the browser
 * sent, or fall back to the castor_token cookie.
 *
 * On success the server gives us a challenge that the attestation
 * response must echo in its clientDataJSON. The browser holds the
 * challenge in memory between begin and complete, so this route
 * never persists anything itself.
 */
import type { APIRoute } from 'astro';
import { readToken } from '../../../../../lib/auth';
import { backendOrigin } from '../../../../../lib/auth';

export const POST: APIRoute = async ({ request, cookies }) => {
  const token = readToken(cookies);
  if (!token) {
    return new Response(
      JSON.stringify({ detail: 'Sign in before enrolling a passkey.' }),
      { status: 401, headers: { 'Content-Type': 'application/json' } },
    );
  }

  let body: { username?: string } = {};
  try {
    body = (await request.json()) as { username?: string };
  } catch {
    /* empty body is fine */
  }
  const username = body.username ?? cookies.get('castor_user')?.value ?? '';

  const origin = backendOrigin();
  const backendRes = await fetch(new URL('/auth/webauthn/register/begin', origin), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ username }),
  });

  const text = await backendRes.text();
  return new Response(text, {
    status: backendRes.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
