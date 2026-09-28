// Export habits — GET /api/v1/habits/export
//
// This previously assembled the export itself from three calls:
//
//   GET /api/v1/habits                    -> returned only [{id, name}]
//   GET /api/v1/habits/{id}/records       -> does not exist; 404
//
// Both were wrong, and the failure was silent: the 404 was swallowed
// into `records = []`, so the download succeeded with HTTP 200 and
// contained five habits stripped to `{id, name, records: []}` — no
// period, no tags, no status, and zero tick history. A user who
// exported and later restored would have silently lost all of it.
//
// The backend already owns a correct, ordered, status-preserving
// export at GET /api/v1/habits/export (`_habit_list_export_data` in
// castor/routes/api.py). Its docstring explicitly notes that it exists
// so "a round-trip (export -> import on another device) preserves
// status". This route now just proxies it, so there is one definition
// of the export format rather than two that have already drifted.
import type { APIRoute } from 'astro';
import { backendFetch } from '../../../../lib/auth';

export const GET: APIRoute = async ({ cookies }) => {
  const token = cookies.get('castor_token')?.value;
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const res = await backendFetch(cookies, '/api/v1/habits/export', {
    method: 'GET',
  });

  if (!res.ok) {
    let detail = `Export failed (HTTP ${res.status}).`;
    try {
      const body = (await res.json()) as { detail?: string } | null;
      if (body?.detail) detail = body.detail;
    } catch {
      // body was not JSON
    }
    return new Response(JSON.stringify({ detail }), {
      status: res.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // The backend returns `{habits: [...], order: [...]}`. That is the
  // canonical shape and the one the import validator accepts, so it is
  // passed through unchanged rather than reshaped here.
  const json = await res.text();
  return new Response(json, {
    status: 200,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Disposition': `attachment; filename="castor-export-${new Date()
        .toISOString()
        .slice(0, 10)}.json"`,
      'Cache-Control': 'no-store',
    },
  });
};
