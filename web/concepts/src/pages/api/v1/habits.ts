/**
 * /api/v1/habits — proxy to backend /api/v1/habits with auth.
 * Uses backendFetch to attach the Authorization header from the cookie.
 * Also accepts Authorization header for server-to-server calls.
 */
import type { APIRoute } from 'astro';
import { readToken } from '../../../lib/auth';

function getToken(cookies: any, headers: Headers): string | null {
  // Check Authorization header first (for server-to-server calls)
  const authHeader = headers.get('Authorization');
  if (authHeader?.startsWith('Bearer ')) {
    return authHeader.slice(7);
  }
  // Fallback to cookie
  return readToken(cookies);
}

export const GET: APIRoute = async ({ cookies, request }) => {
  const token = getToken(cookies, request.headers);
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  
  const origin = import.meta.env.BACKEND_URL || 'http://localhost:8085';
  const res = await fetch(new URL('/api/v1/habits', origin), {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  
  if (!res.ok) {
    return new Response(await res.text(), {
      status: res.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  
  const habits = await res.json();
  
  // Fetch records for each habit (backend list doesn't include records)
  const habitsWithRecords = await Promise.all(habits.map(async (habit: any) => {
    const recordRes = await fetch(new URL(`/api/v1/habits/${habit.id}`, origin), {
      headers: { 'Authorization': `Bearer ${token}` },
    });
    if (recordRes.ok) {
      const detail = await recordRes.json();
      return { ...habit, ...detail, records: detail.records || [] };
    }
    return { ...habit, records: [] };
  }));
  
  return new Response(JSON.stringify(habitsWithRecords), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
};

export const POST: APIRoute = async ({ cookies, request }) => {
  const token = getToken(cookies, request.headers);
  if (!token) {
    return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  
  const body = await request.json();
  const origin = import.meta.env.BACKEND_URL || 'http://localhost:8085';
  const res = await fetch(new URL('/api/v1/habits', origin), {
    method: 'POST',
    headers: { 
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}` 
    },
    body: JSON.stringify(body),
  });
  
  const responseBody = await res.text();
  
  return new Response(responseBody, {
    status: res.status,
    headers: { 'Content-Type': 'application/json' },
  });
};