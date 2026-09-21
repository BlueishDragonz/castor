// @ts-check
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import node from '@astrojs/node';
import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
// - React integration: enables shadcn/ui (React) islands
// - @astrojs/node: SSR adapter for the pilot deploy (single Node process
//   fronts both this Astro app and the existing FastAPI/NiceGUI backend).
//   Astro server proxies /api/* requests to the backend on BACKEND_URL.
// - Tailwind v4 via Vite plugin: shadcn/ui's CSS-variable system rides on
//   Tailwind v4 tokens; no PostCSS config needed.
export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
  server: { port: 4321, host: '0.0.0.0' },
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
    server: {
      proxy: {
        // Forward API traffic to the existing FastAPI/NiceGUI backend on
        // 8080. Same-domain via reverse proxy in production.
        '/api': {
          target: process.env.BACKEND_URL || 'http://localhost:8080',
          changeOrigin: true,
          ws: true,
        },
      },
    },
  },
});
