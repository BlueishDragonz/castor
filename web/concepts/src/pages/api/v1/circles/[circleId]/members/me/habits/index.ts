/**
 * /api/v1/circles/{circleId}/members/me/habits — BFF proxy for the
 * share-your-habit wizard (slice 24d).
 *
 * Browser-side POST from circles/[id]/welcome.astro's inline script
 * (`fetch('/api/v1/circles/${circleId}/members/me/habits')`). Without
 * this BFF route the POST 404s in production — in dev the Vite proxy
 * masks the gap (same bug class the slice-15/20 audits caught).
 * Found by audit_bff_coverage.py during the slice-28 runbook-fix pass
 * (it flags this exact path as "no BFF proxy").
 *
 * Backend contract (pinned by tests/test_slice24d_member_shares.py):
 *   POST /api/v1/circles/{id}/members/me/habits
 *   body { habit_id, visibility } → 201 { habit_id, visibility }
 *   404 "Habit not found in your habit list" if the habit isn't the
 *   caller's own; 401 unauth; non-member → 404 (via _require_member).
 *
 * This route is a thin pass-through: cookie → bearer, forward JSON,
 * relay status + body verbatim. GET/DELETE of member shares are
 * server-side only (backendFetch) and intentionally not proxied.
 */
import type { APIRoute } from 'astro';
import { readToken } from '@/lib/auth';

export const POST: APIRoute = async ({ params, request, cookies }) => {
  const circleId = params.circleId;
  if (!circleId || !/^\d+$/.test(circleId)) {
    return new Response(JSON.stringify({ detail: 'Invalid circle id' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const token = readToken(cookies);
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const origin = import.meta.env.BACKEND_URL || 'http://localhost:8085';
  let body: string;
  try {
    body = JSON.stringify(await request.json());
  } catch {
    return new Response(JSON.stringify({ detail: 'Invalid JSON body' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  let res: Response;
  try {
    res = await fetch(
      new URL(`/api/v1/circles/${circleId}/members/me/habits`, origin),
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body,
      },
    );
  } catch {
    return new Response(
      JSON.stringify({ detail: 'Could not reach the backend' }),
      { status: 502, headers: { 'Content-Type': 'application/json' } },
    );
  }

  return new Response(await res.text(), {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};
