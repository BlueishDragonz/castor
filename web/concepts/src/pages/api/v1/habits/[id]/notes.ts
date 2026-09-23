/**
 * /api/v1/habits/[id]/notes — server-side proxy for autosaving notes
 * on a habit tick. Mirrors the form-POST handler at
 * /habits/[id]/complete (which serves the no-JS click-to-toggle path)
 * but accepts JSON from the long-press notes dialog's auto-save
 * fetch loop.
 *
 * Why a server-side proxy: the castor frontend never exposes the
 * bearer JWT to client JS. The dialog is a React island; it POSTs
 * JSON to this endpoint, and we attach `Authorization: Bearer ***`
 * from the httpOnly cookie before forwarding to the backend's
 * POST /api/v1/habits/{id}/completions.
 *
 * The backend's POST /habits/{id}/completions is idempotent per
 * (habit, date). Re-posting with the same date+done is safe; the
 * text is overwritten each call. That makes the autosave loop
 * "POST on every keystroke after a 24-char delta debounce"
 * idempotent without explicit conflict handling.
 */
import type { APIRoute } from 'astro';
import { readToken } from '@/lib/auth';

export const POST: APIRoute = async ({ params, request, cookies }) => {
  const habitId = params.id;
  if (!habitId) {
    return new Response(JSON.stringify({ detail: 'habit id required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const token = readToken(cookies);
  if (!token) {
    return new Response(JSON.stringify({ detail: 'not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  let body: { date?: string; done?: boolean; text?: string };
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ detail: 'invalid JSON' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const { date, done = true, text = '' } = body;
  if (!date || !/^\d{2}-\d{2}-\d{4}$/.test(date)) {
    return new Response(JSON.stringify({ detail: 'date must be DD-MM-YYYY' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // Mirror the cap used by complete.astro (line 48): 4000 chars max.
  const safeText = String(text).slice(0, 4000);

  const origin = import.meta.env.BACKEND_URL || 'http://localhost:8085';
  const res = await fetch(new URL(`/api/v1/habits/${habitId}/completions`, origin), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ date, done, text: safeText }),
  });

  const responseText = await res.text();
  return new Response(responseText, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
