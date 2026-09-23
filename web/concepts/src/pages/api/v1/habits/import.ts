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
      // Create habit
      const createRes = await fetch(new URL('/api/v1/habits', origin), {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          name: habit.name,
          period: habit.period || null,
          tags: habit.tags || [],
          status: habit.status || 'active',
        }),
      });

      if (!createRes.ok) {
        const err = await createRes.json();
        results.push({ name: habit.name, success: false, error: err.detail });
        continue;
      }

      const created = await createRes.json();
      
      // Import records
      if (habit.records && habit.records.length > 0) {
        for (const record of habit.records) {
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