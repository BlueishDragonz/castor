/**
 * Local-date utilities for castor.
 *
 * Avoid `Date.toISOString().slice(0, 10)` for anything that compares
 * to dates stored in the backend. The backend stores ticks as the
 * user's LOCAL calendar date (e.g. "2026-09-23" for someone who ticked
 * at 23:30 local time on Sep 22 — the date in their timezone, not
 * UTC). `toISOString()` returns UTC, so for any user not in UTC the
 * conversion drifts by the timezone offset, making cells empty and
 * date pickers show yesterday.
 *
 * Use these helpers instead. They format the YYYY-MM-DD components
 * from the Date's local-time getters (getFullYear / getMonth / getDate)
 * which are timezone-naive and produce the date the user actually sees
 * on their clock.
 */

/** Returns YYYY-MM-DD in the local timezone (e.g. "2026-09-23"). */
export function localISO(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/** Returns DD-MM-YYYY in the local timezone (backend's tick.date_fmt). */
export function localDMY(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${d}-${m}-${y}`;
}
