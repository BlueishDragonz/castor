import type { APIRoute } from 'astro';
import { backendOrigin } from '../../../../lib/auth';

// GET /api/v1/habits/:id - Get habit details
export const GET: APIRoute = async ({ params, cookies }) => {
  const habitId = params.id;
  const token = cookies.get('castor_token')?.value;
  
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const origin = backendOrigin();;
  const res = await fetch(new URL(`/api/v1/habits/${habitId}`, origin), {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Habit not found' }));
    return new Response(JSON.stringify(error), {
      status: res.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  
  const data = await res.json();
  return new Response(JSON.stringify(data), {
    headers: { 'Content-Type': 'application/json' },
  });
};

// PUT /api/v1/habits/:id - Update habit (name, period, tags, chips, status)
//
// NOTE: the backend exposes PUT, not PATCH, for this route
// (`@api_router.put("/habits/{habit_id}")` in castor/routes/api.py).
// Forwarding PATCH returned 405 Method Not Allowed, which meant the
// Edit habit form silently failed for every user — the page reported
// "Failed to update habit (HTTP 405)" and nothing was ever saved.
export const PUT: APIRoute = async ({ params, request, cookies }) => {
  const habitId = params.id;
  const token = cookies.get('castor_token')?.value;

  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const body = await request.json();
  const origin = backendOrigin();;

  const res = await fetch(new URL(`/api/v1/habits/${habitId}`, origin), {
    method: 'PUT',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to update habit' }));
    return new Response(JSON.stringify(error), {
      status: res.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  
  const data = await res.json();
  return new Response(JSON.stringify(data), {
    headers: { 'Content-Type': 'application/json' },
  });
};

// DELETE /api/v1/habits/:id - Archive habit
export const DELETE: APIRoute = async ({ params, cookies }) => {
  const habitId = params.id;
  const token = cookies.get('castor_token')?.value;
  
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const origin = backendOrigin();;
  const res = await fetch(new URL(`/api/v1/habits/${habitId}`, origin), {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` },
  });
  
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to archive habit' }));
    return new Response(JSON.stringify(error), {
      status: res.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  
  return new Response(null, { status: 204 });
};