// @ts-check
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import node from '@astrojs/node';
import tailwindcss from '@tailwindcss/vite';

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
  server: { port: 4321, host: '0.0.0.0' },
  integrations: [react()],
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
