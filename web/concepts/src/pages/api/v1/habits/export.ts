// Export habits endpoint
// GET /api/v1/habits/export
import type { APIRoute } from 'astro';

export const GET: APIRoute = async ({ cookies }) => {
  const token = cookies.get('castor_token')?.value;
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), { 
      status: 401,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  try {
    const origin = import.meta.env.BACKEND_URL || 'http://localhost:8085';
    
    // Fetch all habits with records
    const habitsRes = await fetch(new URL('/api/v1/habits', origin), {
      headers: { 'Authorization': `Bearer ${token}` },
    });

    if (!habitsRes.ok) {
      return new Response(JSON.stringify({ detail: 'Failed to fetch habits' }), { 
        status: habitsRes.status,
        headers: { 'Content-Type': 'application/json' }
      });
    }

    const habits = await habitsRes.json();
    
    // Fetch records for each habit
    const habitsWithRecords = await Promise.all(
      habits.map(async (habit: any) => {
        const recordsRes = await fetch(new URL(`/api/v1/habits/${habit.id}/records`, origin), {
          headers: { 'Authorization': `Bearer ${token}` },
        });
        
        let records: any[] = [];
        if (recordsRes.ok) {
          records = await recordsRes.json();
        }
        
        return {
          id: habit.id,
          name: habit.name,
          star: habit.star,
          status: habit.status,
          period: habit.period,
          tags: habit.tags,
          records: records.map((r: any) => ({
            day: r.day,
            done: r.done,
            text: r.text,
            timestamp: r.timestamp,
          })),
        };
      })
    );

    // Return as JSON file
    const json = JSON.stringify(habitsWithRecords, null, 2);
    
    return new Response(json, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Content-Disposition': `attachment; filename="beaver-habits-export-${new Date().toISOString().split('T')[0]}.json"`,
      },
    });
  } catch (e: any) {
    return new Response(JSON.stringify({ detail: e.message }), { 
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
};