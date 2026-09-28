// Import habits — POST /api/v1/habits/import
//
// Body: { habits: Habit[], order?: string[] }
//
// The implementation lives in `src/lib/import-habits.ts`, shared with
// the /import page. That page used to call this route with a relative
// fetch, which fails on the server ("Failed to parse URL"), so the
// import never ran; sharing the module removes the HTTP hop entirely
// rather than patching a URL around it.
//
// This route remains for API clients (native apps, scripts) that POST
// JSON directly.
import type { APIRoute } from 'astro';
import { importHabits } from '../../../../lib/import-habits';

export const POST: APIRoute = async ({ request, cookies }) => {
  const token = cookies.get('castor_token')?.value;
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  let body: { habits?: unknown; order?: unknown };
  try {
    body = await request.json();
  } catch {
    return new Response(
      JSON.stringify({ detail: 'Body was not valid JSON.' }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    );
  }

  const outcome = await importHabits(cookies, {
    habits: body?.habits,
    order: body?.order,
  });

  // A malformed *request* is a client error. A failed *import* is
  // still reported as 200, because the import ran and the per-habit
  // `results` explain exactly what happened to each one.
  const malformed = outcome.detail?.startsWith('Expected') ?? false;
  return new Response(JSON.stringify(outcome), {
    status: malformed ? 400 : 200,
    headers: { 'Content-Type': 'application/json' },
  });
};
