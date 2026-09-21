/**
 * castor middleware — runs on every Astro request.
 *
 * Responsibilities:
 *   1. Surface the current session (token + email) to pages via
 *      Astro.locals.session, so pages don't re-read the cookie.
 *   2. Tag public routes (/, /login, /register) so pages can decide
 *      whether to redirect signed-in users to /habits.
 *
 * Per-page auth enforcement (redirect to /login if no session) is the
 * page's responsibility — middleware just provides the data.
 */

import { defineMiddleware } from 'astro:middleware';
import { readSession } from './lib/auth';

export const onRequest = defineMiddleware((context, next) => {
  const session = readSession(context.cookies);
  context.locals.session = session;
  return next();
});
