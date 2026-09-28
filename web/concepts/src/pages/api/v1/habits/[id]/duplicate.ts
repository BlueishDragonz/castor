import type { APIRoute } from 'astro';
import { backendOrigin } from '../../../../../lib/auth';

export const POST: APIRoute = async ({ params, cookies }) => {
  const habitId = params.id;
  const token = cookies.get('castor_token')?.value;
  
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const origin = backendOrigin();;
  
  // Fetch the habit to duplicate
  const habitRes = await fetch(new URL(`/api/v1/habits/${habitId}`, origin), {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  
  if (!habitRes.ok) {
    return new Response(JSON.stringify({ detail: 'Habit not found' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  
  const habit = await habitRes.json();
  
  // Create a copy with a new ID
  const newHabit = {
    name: habit.name,
    period: habit.period,
    tags: habit.tags,
    chips: habit.chips,
    status: 'active',
  };
  
  // Create the new habit
  const createRes = await fetch(new URL('/api/v1/habits', origin), {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(newHabit),
  });
  
  if (!createRes.ok) {
    const error = await createRes.json().catch(() => ({ detail: 'Failed to duplicate habit' }));
    return new Response(JSON.stringify(error), {
      status: createRes.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  
  const created = await createRes.json();
  return new Response(JSON.stringify(created), {
    status: 201,
    headers: { 'Content-Type': 'application/json' },
  });
};