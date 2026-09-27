/**
 * period.ts — one place that knows the habit-period data contract.
 *
 * The backend (`castor/routes/api.py`, `UpdateHabitPeriod`) serialises a
 * habit's period as:
 *
 *   { period_type: 'D' | 'W' | 'M' | 'Y', period_count: number, target_count: number }
 *
 * Several pages previously read `period.frequency` and `period.interval`
 * instead, which are simply not present in that payload. The result was
 * an interface that rendered as a bare "× per " with no number and no
 * unit (verified in the rendered DOM on /stats) and a streak column that
 * never showed a value.
 *
 * Everything in the UI that needs to describe or parse a period goes
 * through here so the mapping exists once.
 */

export type PeriodInterval = 'day' | 'week' | 'month' | 'year';

/**
 * The period object exactly as the API serialises it.
 *
 * Declared once here and imported by every page that types a habit,
 * because the stale `frequency`/`interval` shape had been copy-pasted
 * into five separate local interfaces — which is how /habits/[id]
 * came to render "Every undefined undefined" long after the
 * frequency/interval bug had been fixed on /stats.
 */
export interface HabitPeriod {
  period_type: 'D' | 'W' | 'M' | 'Y';
  period_count: number;
  target_count: number;
}

/** The backend's single-letter codes. */
export const PERIOD_TYPE_CODE: Record<PeriodInterval, 'D' | 'W' | 'M' | 'Y'> = {
  day: 'D',
  week: 'W',
  month: 'M',
  year: 'Y',
};

const CODE_INTERVAL: Record<string, PeriodInterval> = {
  D: 'day',
  W: 'week',
  M: 'month',
  Y: 'year',
};

/**
 * The raw period shape as the backend returns it. `target_count` and
 * `period_count` are optional at the type level because a habit created
 * without a period has `period: null` and some legacy records carry a
 * partial object.
 */
export interface RawPeriod {
  period_type?: string | null;
  period_count?: number | null;
  target_count?: number | null;
}

export interface NormalisedPeriod {
  interval: PeriodInterval;
  /** "Every N <interval>" — how long the period is. */
  periodCount: number;
  /** "Times per period" — how many completions are expected. */
  targetCount: number;
}

/**
 * Normalise whatever the backend sent into the shape the UI renders.
 * Returns null when there is no usable period, so callers can show
 * "No period set" rather than a half-populated string.
 */
export function normalisePeriod(raw: unknown): NormalisedPeriod | null {
  if (!raw || typeof raw !== 'object') return null;
  const p = raw as RawPeriod;

  const interval = p.period_type ? CODE_INTERVAL[String(p.period_type)] : undefined;
  if (!interval) return null;

  const periodCount =
    typeof p.period_count === 'number' && p.period_count > 0 ? p.period_count : 1;
  const targetCount =
    typeof p.target_count === 'number' && p.target_count > 0 ? p.target_count : 1;

  return { interval, periodCount, targetCount };
}

/**
 * A short, human sentence for the period — "Daily", "3× per week",
 * "Once every 2 months". Used wherever a habit's target is summarised.
 * Returns null when the habit has no period.
 *
 * The plural is a real trap here: the naive form
 * `${target}× per ${interval}s` produced "14× per days" for a habit
 * whose target_count is 14 and whose period_type is "D". The unit has to
 * be pluralised on the *interval*, not on the count.
 */
export function describePeriod(raw: unknown): string | null {
  const p = normalisePeriod(raw);
  if (!p) return null;

  const { interval, periodCount, targetCount } = p;

  // 1× every 1 day is just "Daily" — by far the most common case, and
  // "1× per day" is noise rather than information.
  if (targetCount === 1 && periodCount === 1 && interval === 'day') {
    return 'Daily';
  }

  // 1× every N days/weeks reads better as "Once every 2 weeks" than as
  // "1× per 2 weeks".
  if (targetCount === 1) {
    if (periodCount === 1) return `Once per ${interval}`;
    return `Once every ${periodCount} ${plural(interval, periodCount)}`;
  }

  // The unit is pluralised on periodCount, NOT on targetCount. These are
  // different numbers: target_count is "times per period" and
  // period_count is the length of the period. Pluralising on the target
  // produced "5× per days" (target 5, period 7 days) and
  // "2× per months" (target 2, period 3 months) — both wrong, because
  // the noun belongs to the period.
  return `${targetCount}× per ${plural(interval, periodCount)}`;
}

/** English pluralisation for the four interval words. */
function plural(interval: PeriodInterval, n: number): string {
  if (n === 1) return interval;
  if (interval === 'day') return 'days';
  if (interval === 'week') return 'weeks';
  if (interval === 'month') return 'months';
  return 'years';
}

/**
 * A compact form for tight spaces (table cells, badges): "3/wk",
 * "Daily", "2/mo". Returns an em dash when there is no period so the
 * caller never has to handle undefined.
 */
export function formatPeriodCompact(raw: unknown): string {
  const p = normalisePeriod(raw);
  if (!p) return '—';

  const abbrev: Record<PeriodInterval, string> = {
    day: 'd',
    week: 'wk',
    month: 'mo',
    year: 'yr',
  };

  if (p.targetCount === 1 && p.periodCount === 1 && p.interval === 'day') {
    return 'Daily';
  }
  return `${p.targetCount}×/${abbrev[p.interval]}`;
}
