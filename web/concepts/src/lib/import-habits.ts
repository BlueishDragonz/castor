/**
 * importHabits — restore a JSON export into the signed-in account.
 *
 * Shared by two callers, which is why it is a module and not inline in
 * the page:
 *   - `src/pages/import.astro` (the form, server-rendered)
 *   - `src/pages/api/v1/habits/import.ts` (the BFF, for API clients)
 *
 * The page previously called the BFF with a *relative* fetch, which has
 * no base URL on the server and threw "Failed to parse URL" — so the
 * import silently never ran. Sharing the implementation means there is
 * no HTTP hop to get wrong.
 *
 * Semantics: this REPLACES the account's habits. The backend caps
 * active habits at MAX_HABIT_COUNT (5), so appending could only ever
 * produce a half-imported account. Deleting first is what makes
 * "replace" true, and it is also what frees the cap.
 *
 * Ordering matters, and the first step is the one that protects the
 * user: everything is validated BEFORE anything is deleted. A payload
 * whose 4th habit is malformed must not have already destroyed the 1st
 * three.
 */
import type { AstroCookies } from 'astro';
import { backendFetch } from './auth';

type Json = Record<string, unknown>;

export type HabitResult = {
  name: string;
  success: boolean;
  id?: string;
  records?: number;
  error?: string;
};

export type ImportOutcome = {
  ok: boolean;
  detail?: string;
  imported: number;
  failed: number;
  records: number;
  removed: number;
  orderRestored: boolean;
  results: HabitResult[];
};

/**
 * Records replayed concurrently. Records for one habit go out in
 * batches: a year of daily history is 365 requests, which sequentially
 * would take minutes, and unbounded-parallel trips the rate limiter
 * and fails the import halfway.
 */
const RECORD_BATCH = 8;

function isJsonObject(v: unknown): v is Json {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function fail(detail: string): ImportOutcome {
  return {
    ok: false,
    detail,
    imported: 0,
    failed: 0,
    records: 0,
    removed: 0,
    orderRestored: false,
    results: [],
  };
}

export async function importHabits(
  cookies: AstroCookies,
  payload: { habits: unknown; order?: unknown },
): Promise<ImportOutcome> {
  const { habits, order } = payload ?? {};

  // ── 1. Validate the whole payload before touching anything ───────
  if (!Array.isArray(habits)) {
    return fail('Expected a "habits" array in the payload.');
  }
  if (habits.length === 0) {
    return fail('The payload contains no habits.');
  }

  const invalid: string[] = [];
  habits.forEach((h, i) => {
    if (!isJsonObject(h) || typeof h.name !== 'string' || h.name.trim() === '') {
      invalid.push(`habit ${i + 1} has no "name"`);
    }
  });
  if (invalid.length > 0) {
    return fail(`Nothing was imported. ${invalid.join('; ')}.`);
  }

  const call = (path: string, init: RequestInit = {}) =>
    backendFetch(cookies, path, init);

  // ── 2. Clear existing habits (the "replace", and it frees the cap) ─
  //
  // `GET /api/v1/habits` defaults to `status=active`, which was the
  // second silent bug here: archived habits were never listed and so
  // never deleted. The account kept its archived habits *and* imported
  // fresh ones with the same names, so a restore produced visible
  // duplicates. The cap check is also active-only, so the surplus
  // imported and then failed on create with "Maximum habit count (5)
  // reached" — leaving a mixture the user could not tell apart.
  //
  // So: walk every status the API knows about, not just the default.
  const STATUSES = ['active', 'archive', 'soft_delete'] as const;
  const seen = new Set<string>();
  for (const status of STATUSES) {
    const res = await call(`/api/v1/habits?status=${status}`, { method: 'GET' });
    if (!res.ok) continue;
    const parsed = await res.json();
    const list = Array.isArray(parsed) ? parsed : [];
    for (const h of list) {
      const id = isJsonObject(h) ? h.id : null;
      if (typeof id === 'string' && !seen.has(id)) seen.add(id);
    }
  }
  const existingIds = [...seen];

  for (const id of existingIds) {
    await call(`/api/v1/habits/${id}`, { method: 'DELETE' });
  }

  // ── 3. Recreate each habit and replay its history ────────────────
  const results: HabitResult[] = [];
  const createdIds: string[] = [];

  for (const raw of habits as Json[]) {
    const name = String(raw.name);
    const createRes = await call('/api/v1/habits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });

    if (!createRes.ok) {
      let detail = `HTTP ${createRes.status}`;
      try {
        const err = (await createRes.json()) as { detail?: string };
        if (err?.detail) detail = err.detail;
      } catch {
        // not JSON
      }
      results.push({ name, success: false, error: detail });
      continue;
    }

    const created = (await createRes.json()) as { id?: string };
    const id = typeof created.id === 'string' ? created.id : null;
    if (!id) {
      results.push({ name, success: false, error: 'Backend returned no id' });
      continue;
    }
    createdIds.push(id);

    // POST /habits only stores the name, so period / tags / status need
    // the follow-up PUT. Skip the round-trip when all three are default.
    const tags = Array.isArray(raw.tags) ? (raw.tags as string[]) : [];
    const period = isJsonObject(raw.period) ? raw.period : null;
    const status = typeof raw.status === 'string' ? raw.status : null;
    const update: Json = {};
    if (tags.length > 0) update.tags = tags;
    if (period) update.period = period;
    if (status && status !== 'active') update.status = status;

    if (Object.keys(update).length > 0) {
      await call(`/api/v1/habits/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(update),
      });
    }

    // Records arrive nested as {data: {day, done, text, timestamp}};
    // flatten, and drop anything without a usable day.
    const flat = (Array.isArray(raw.records) ? raw.records : [])
      .map((r) => (isJsonObject(r) && isJsonObject(r.data) ? r.data : r))
      .filter(isJsonObject)
      .filter((r) => typeof r.day === 'string' && r.day.length > 0);

    /*
     * Replay one record.
     *
     * Two conversions, both of which 422'd every single record before
     * (the backend rejected all 138 of them and the import carried on
     * regardless, reporting a successful import that restored nothing):
     *
     *  - Content-Type. `Tick` is a Pydantic *body* model, so the
     *    payload must be JSON. This was
     *    `application/x-www-form-urlencoded`.
     *  - Date format. The backend parses with `date_fmt`, which
     *    defaults to `%d-%m-%Y`. An export's record `day` is ISO
     *    `%Y-%m-%d` (that is the format the record itself stores), so
     *    the ISO string is rejected and the DMY string is wanted.
     */
    const replay = (r: Json): Promise<Response> | undefined => {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(r.day));
      if (!m) return undefined; // unusable date: skip, don't 422 the batch
      return call(`/api/v1/habits/${id}/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          date: `${m[3]}-${m[2]}-${m[1]}`,
          date_fmt: '%d-%m-%Y',
          done: r.done === true,
          ...(typeof r.text === 'string' && r.text ? { text: r.text } : {}),
        }),
      });
    };

    let replayed = 0;
    for (let i = 0; i < flat.length; i += RECORD_BATCH) {
      const slice = flat.slice(i, i + RECORD_BATCH);
      const responses = await Promise.all(slice.map(replay));
      // Count what actually landed, not what we attempted. This number
      // is what the success banner shows, so it has to be true.
      for (const res of responses) {
        if (res && res.ok) replayed += 1;
      }
    }

    results.push({ name, success: true, id, records: replayed });
  }

  // ── 4. Restore the home page order the export captured ───────────
  // The payload's habits are already in exported order and createdIds
  // was appended in that same order, so the i-th created id is the
  // replacement for the i-th exported habit.
  let orderRestored = false;
  if (Array.isArray(order) && order.length > 0 && createdIds.length > 0) {
    const orderRes = await call('/api/v1/habits/reorder', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ habit_ids: createdIds }),
    });
    orderRestored = orderRes.ok;
  }

  const succeeded = results.filter((r) => r.success).length;
  const failed = results.length - succeeded;
  const totalRecords = results.reduce(
    (sum, r) => sum + (typeof r.records === 'number' ? r.records : 0),
    0,
  );

  return {
    ok: succeeded > 0,
    detail:
      succeeded === 0
        ? 'None of the habits could be imported. Your existing habits were ' +
          'already removed, so re-export from a working account if you have one.'
        : undefined,
    imported: succeeded,
    failed,
    records: totalRecords,
    removed: existingIds.length,
    orderRestored,
    results,
  };
}
