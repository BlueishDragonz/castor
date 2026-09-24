// Import habits endpoint
// POST /api/v1/habits/import
// Body: { habits: Habit[] }
import type { APIRoute } from 'astro';

export const POST: APIRoute = async ({ request, cookies }) => {
  const token = cookies.get('castor_token')?.value;
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), { 
      status: 401,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  try {
    const body = await request.json();
    const { habits } = body;
    
    if (!Array.isArray(habits)) {
      return new Response(JSON.stringify({ detail: 'Expected habits array' }), { 
        status: 400,
        headers: { 'Content-Type': 'application/json' }
      });
    }

    const origin = import.meta.env.BACKEND_URL || 'http://localhost:8085';
    
    // Import each habit
    const results = [];
    for (const habit of habits) {
      // Create habit (POST only accepts {name}; tags/status/period/records
      // must be set via PUT /habits/{id} after creation. The backend
      // silently drops everything except name on POST.)
      const createRes = await fetch(new URL('/api/v1/habits', origin), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ name: habit.name }),
      });

      if (!createRes.ok) {
        const err = await createRes.json();
        results.push({ name: habit.name, success: false, error: err.detail });
        continue;
      }

      const created = await createRes.json();

      // Apply tags / period / status via PUT. Skip the round-trip if all
      // three are at their defaults (no PUT is cheaper than an empty PUT).
      const hasMetadata =
        (habit.tags && habit.tags.length > 0) ||
        (habit.period) ||
        (habit.status && habit.status !== 'active');
      if (hasMetadata) {
        const update: Record<string, unknown> = {};
        if (habit.tags && habit.tags.length > 0) update.tags = habit.tags;
        if (habit.period) update.period = habit.period;
        if (habit.status) update.status = habit.status;
        await fetch(new URL(`/api/v1/habits/${created.id}`, origin), {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(update),
        });
      }

      // Import records. Records in the export are nested: {data: {day, done, ...}}
      // (matches /habits/{id} canonical shape). Flatten them here.
      const records = (habit.records || []).map((r: any) => r.data || r);
      if (records.length > 0) {
        for (const record of records) {
          await fetch(new URL(`/api/v1/habits/${created.id}/completions`, origin), {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded',
              'Authorization': `Bearer ${token}`
            },
            body: new URLSearchParams({
              date: record.day,
              date_fmt: '%Y-%m-%d',
              done: record.done ? 'true' : 'false',
              text: record.text || '',
            }),
          });
        }
      }
      
      results.push({ name: habit.name, success: true, id: created.id });
    }

    return new Response(JSON.stringify({ results }), { 
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  } catch (e: any) {
    return new Response(JSON.stringify({ detail: e.message }), { 
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
};