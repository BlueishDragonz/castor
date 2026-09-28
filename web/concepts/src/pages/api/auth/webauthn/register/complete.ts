/**
 * /api/auth/webauthn/register/complete — server route for passkey
 * registration. The browser's WebAuthn flow does
 * navigator.credentials.create() then POSTs the attestation here.
 *
 * The backend's /auth/webauthn/register/complete requires an
 * authenticated session (the user must be logged in to enroll a
 * passkey). The browser passes the existing castor_token in the
 * Authorization header. On success the credential is persisted and
 * the response carries a fresh Set-Cookie for `beaver_webauthn` which
 * we mirror.
 *
 * Registration does NOT change the auth token — the user already has
 * one from /auth/login. So this route does not call writeSession().
 */
import type { APIRoute } from 'astro';
import { readToken, mirrorWebAuthnBrowserCookie, writeSession } from '../../../../../lib/auth';
import { backendOrigin } from '../../../../../lib/auth';

export const POST: APIRoute = async ({ request, cookies }) => {
  const token = readToken(cookies);
  if (!token) {
    return jsonError(401, 'Sign in before enrolling a passkey.');
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, 'Invalid JSON body');
  }

  const origin = backendOrigin();
  const backendRes = await fetch(new URL('/auth/webauthn/register/complete', origin), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  mirrorWebAuthnBrowserCookie(backendRes, cookies);

  if (!backendRes.ok) {
    const text = await backendRes.text();
    return new Response(text, {
      status: backendRes.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // Backend returns { access_token, token_type } on success — the
  // register/complete flow is allowed to rotate the session token.
  // Apply the rotation if present so the user keeps a valid cookie.
  const data = (await backendRes.json().catch(() => ({}))) as {
    access_token?: string;
  };
  if (data.access_token) {
    // F26: this used to hand-roll a second, weaker copy of the session-cookie
    // logic — httpOnly:false on castor_user, secure:false hardcoded, a
    // different 7-day maxAge than writeSession's 30 days, and its own
    // isProd() reimplementation. Two divergent implementations of one
    // security-relevant cookie is exactly the shape of bug that let F6/F18
    // hide: whichever ran last won. Refresh the token through the single
    // shared helper instead, and keep the existing verified email.
    const email = cookies.get('castor_user')?.value ?? '';
    writeSession(cookies, data.access_token, email);
  }

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
};

function jsonError(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
