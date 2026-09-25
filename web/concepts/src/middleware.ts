/**
 * castor middleware — runs on every Astro request.
 *
 * Responsibilities:
 *   1. Surface the current session (token + email) to pages via
 *      Astro.locals.session, so pages don't re-read the cookie.
 *   2. Tag public routes (/, /login, /register) so pages can decide
 *      whether to redirect signed-in users to /habits.
 *   3. PRODUCTION proxy: forward /auth/*, /users/*, /webauthn/*, and
 *      /health to the in-container backend (gunicorn on :8081) when
 *      Astro is running standalone. In dev, the Vite proxy in
 *      astro.config.mjs handles the same paths so the browser hits
 *      Astro, which proxies to the dev backend on :8085. After this
 *      slice, both modes resolve to "browser → Astro → backend".
 *
 * Per-page auth enforcement (redirect to /login if no session) is the
 * page's responsibility — middleware just provides the data.
 *
 * Why a middleware proxy and not more pages/api/ BFF routes? Some
 * client-side callers (SecurityContent.tsx, login.astro passkey flow,
 * register.astro passkey flow) construct URLs like
 *   fetch(`${import.meta.env.BACKEND_URL}/auth/webauthn/credentials`)
 * using import.meta.env.BACKEND_URL as a public-origin placeholder
 * (legacy from when the dev Vite proxy was the only mechanism). The
 * middleware proxy is the smallest change that keeps these working in
 * production without rewriting each call site to use relative URLs
 * that go through pages/api/auth/ BFF routes. Long-term, callers
 * should be migrated to relative URLs + pages/api/auth/* BFF routes
 * for symmetry with the slice-15 /api/v1 BFF work.
 */
import { defineMiddleware } from 'astro:middleware';
import { readSession } from './lib/auth';

// Production-only proxy prefixes. In dev, the Vite proxy in
// astro.config.mjs handles these (and middleware must NOT — that
// would create a double-hop Astro → Astro via middleware → backend).
const PROXY_PREFIXES = ['/auth/', '/users/', '/webauthn/', '/health'];

// Paths that have Astro BFF routes — middleware must skip them so
// the page handler runs. (BFF routes are at /api/auth/webauthn/* —
// not in PROXY_PREFIXES, so they pass through naturally. Listed
// here for completeness in case future routes are added at the bare
// /auth/ prefix.)
const BFF_PATHS = new Set<string>([
  // /api/auth/* routes are handled by pages/api/auth/* — middleware
  // doesn't see them (different URL prefix). Listed here in case the
  // proxy is ever extended to /api/auth/.
]);

function shouldProxy(pathname: string): boolean {
  if (BFF_PATHS.has(pathname)) return false;
  return PROXY_PREFIXES.some((p) => pathname.startsWith(p));
}

function isDevMode(): boolean {
  // Vite dev server sets import.meta.env.DEV. In production builds,
  // DEV is false and we run the proxy.
  return import.meta.env.DEV === true;
}

export const onRequest = defineMiddleware(async (context, next) => {
  // Production proxy: forward backend-prefixed paths to BACKEND_URL.
  // Dev skips this and lets the Vite proxy in astro.config.mjs handle
  // it.
  if (!isDevMode() && shouldProxy(context.url.pathname)) {
    const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:8081';
    const target = new URL(context.url.pathname + context.url.search, backendUrl);

    // Forward the original request, including body for non-GET.
    const init: RequestInit = {
      method: context.request.method,
      headers: context.request.headers,
      redirect: 'manual',
    };
    if (context.request.method !== 'GET' && context.request.method !== 'HEAD') {
      init.body = await context.request.arrayBuffer();
    }
    const upstream = await fetch(target, init);

    // Strip hop-by-hop headers we shouldn't forward.
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.delete('connection');
    responseHeaders.delete('keep-alive');
    responseHeaders.delete('transfer-encoding');

    // Mirror WebAuthn cookie: beaver_webauthn → castor_webauthn_browser
    // so middleware on subsequent requests can see it.
    const setCookie = upstream.headers.get('set-cookie');
    if (setCookie && setCookie.startsWith('beaver_webauthn=')) {
      const value = setCookie.split(';', 1)[0].split('=')[1];
      if (value) {
        context.cookies.set('castor_webauthn_browser', value, {
          sameSite: 'lax',
          path: '/',
          httpOnly: false,
          secure: false,
        });
      }
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  }

  const session = readSession(context.cookies);
  context.locals.session = session;
  return next();
});
