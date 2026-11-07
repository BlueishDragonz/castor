// @ts-check
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import node from '@astrojs/node';
import tailwindcss from '@tailwindcss/vite';
import sentry from '@sentry/astro';

// F13: the audit found no monitoring at all — "no detection of any of the
// above". Without this, an OOM kill or a boot failure after a deploy is found
// out by a user, not by an operator.
//
// `authToken` is the SOURCE-MAP UPLOAD token, and is the only secret here.
// Runtime error reporting needs only the public DSN and works without it.
//
// The integration is therefore ALWAYS registered. Gating it on the auth token
// — the obvious reading of the setup guide — silently disables monitoring in
// exactly the local and CI builds where you would want to catch a regression,
// which is worse than not integrating at all, because the package appears
// installed and nothing reports.
const sentryAuthToken = process.env.SENTRY_AUTH_TOKEN;

const integrations = [
  react(),
  sentry({
    project: 'castor-astro',
    org: 'jr-w2',
    // Without a token the integration still injects the client/server SDK and
    // the SSR middleware; it just cannot upload source maps.
    authToken: sentryAuthToken,
    // Never leave sourcemaps in the shipped output: that would hand any
    // visitor the full source of the application. The alternative to
    // deleting them is to ship the app's source to the public.
    sourcemaps: { filesToDeleteAfterUpload: ['./dist/**/*.map'] },
    telemetry: false,
  }),
];

// https://astro.build/config
// - React integration: enables shadcn/ui (React) islands
// - @astrojs/node: SSR adapter so castor can serve the pilot deploy from
//   a single Node process. The reverse proxy in front of both this app
//   and the FastAPI/NiceGUI backend is the production story; in dev,
//   Vite proxies /api/* (and /auth/*) to the backend on BACKEND_URL.
// - Tailwind v4 via Vite plugin: shadcn/ui's CSS-variable system rides
//   on Tailwind v4 tokens; no PostCSS config needed.
//
// Backend route prefixes discovered from `beaverhabits/routes/api.py`
// (init_api_routes mounts `api_router` under `/api/v1`) and
// `beaverhabits/app/app.py` (init_auth_routes mounts fastapi-users
// routers under `/auth`, `/users`). Both are proxied below.
//
// BACKEND_URL defaults to localhost:8085 because port 8080 was occupied
// on this machine by an SSH tunnel. Set BACKEND_URL=http://host:port
// to point at another deployment.
const backend = process.env.BACKEND_URL || 'http://localhost:8085';

export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
  // ─── Reverse-proxy boundary ───────────────────────────────────────
  //
  // Cloudflare terminates TLS in front of this app, so the browser's
  // Origin is "https://castor.blueishdragon.co.uk" while the request that
  // reaches Astro is plain HTTP from the tunnel connector.
  //
  // Without an allow-list, Astro computes the request origin from the raw
  // Host header plus the socket scheme (http), so its built-in CSRF check
  // compares "https://castor..." against "http://castor..." and rejects
  // EVERY browser form POST with "Cross-site POST form submissions are
  // forbidden" -- which breaks sign-in, not just form posts.
  //
  // Listing the public host lets Astro trust X-Forwarded-Proto and rebuild
  // the request origin as https. The protocol is deliberately left
  // unspecified: the origin is also probed directly over http by the
  // local health check, and omitting it keeps the real client IP
  // (X-Forwarded-For) trusted for the direct path as well.
  //
  // This does NOT weaken the CSRF check: a cross-site POST still carries
  // Origin: https://evil.example and is still rejected. Only the
  // scheme/host reconstruction is affected.
  security: {
    allowedDomains: [{ hostname: 'castor.blueishdragon.co.uk' }],
  },
  server: { port: 4321, host: '0.0.0.0' },
  integrations,
  vite: {
    plugins: [tailwindcss()],
    server: {
      proxy: {
        '/auth': { target: backend, changeOrigin: true, ws: true },
        '/users': { target: backend, changeOrigin: true, ws: true },
        '/webauthn': { target: backend, changeOrigin: true, ws: true },
        // /forgot-password and /reset-password are now Astro pages
        // (src/pages/forgot-password.astro, src/pages/reset-password.astro)
        // that POST server-side to BACKEND_URL via backendFetch(). Removing
        // these from the Vite proxy lets Astro serve the pages themselves.
        // /health is mounted at the FastAPI root (see beaverhabits/main.py:64),
        // not under /api/v1, so we proxy the bare path too.
        '/health': { target: backend, changeOrigin: true, ws: false },
      },
    },
  },
});
