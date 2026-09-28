// Reorder habits endpoint
// POST /api/v1/habits/reorder
// Body: { habit_ids: string[] }
import type { APIRoute } from 'astro';
import { backendOrigin } from '../../../../lib/auth';

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
    const { habit_ids } = body;
    
    if (!Array.isArray(habit_ids)) {
      return new Response(JSON.stringify({ detail: 'Expected habit_ids array' }), { 
        status: 400,
        headers: { 'Content-Type': 'application/json' }
      });
    }

    const origin = backendOrigin();;
    
    // Update each habit's position
    for (let i = 0; i < habit_ids.length; i++) {
      await fetch(new URL(`/api/v1/habits/${habit_ids[i]}`, origin), {
        method: 'PATCH',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ position: i }),
      });
    }

    return new Response(JSON.stringify({ success: true }), { 
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