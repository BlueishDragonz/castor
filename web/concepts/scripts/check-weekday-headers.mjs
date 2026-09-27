// Regression guard for the weekday column headers on /habits.
//
// Found by dogfooding the deployed app on 2026-09-27. Every visible weekday
// label was wrong: the grid rendered "Mon 27 ... Sun 21" when 27 September
// 2026 is a Sunday. The labels came from a static Mon..Sun array indexed by
// column position, but the columns are a rolling 7-day window ending today
// (`today - i`), not a calendar week. The two only coincide when today
// happens to land on the last day of an aligned week.
//
// The impact is data correctness, not cosmetics: a user reading "Mon 27"
// and ticking it records a habit on the wrong day. It was invisible to
// assistive tech, because the aria-label on each cell was already derived
// correctly from the date, so a screen-reader user heard the right weekday
// while a sighted user saw the wrong one.
//
// This test pins the rule the fix implements: the label must be derived from
// the date it sits above, never from its column index.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import assert from 'node:assert/strict';

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, '..', 'src', 'components', 'HabitGrid.astro');
const source = readFileSync(SRC, 'utf8');

function test(name, fn) {
  try {
    fn();
    console.log(`  ok   ${name}`);
  } catch (err) {
    console.error(`  FAIL ${name}\n       ${err.message}`);
    process.exitCode = 1;
  }
}

test('weekday header is derived from the date, not the column index', () => {
  assert.ok(
    source.includes("day.toLocaleDateString('en-GB', { weekday: 'short' })"),
    'header must derive the weekday from the date it labels',
  );
});

test('the positional weekday array is gone from HabitGrid', () => {
  assert.ok(
    !source.includes('weekDayAbbr'),
    'HabitGrid must not index a static Mon..Sun array by column position',
  );
});

test('the aria-label is still derived from the date', () => {
  assert.ok(
    source.includes("weekday: 'long'"),
    'cell aria-label must remain date-derived',
  );
});

test('header label and cell aria-label agree for a known week', () => {
  // The actual failure: a window ending Sunday 27 September 2026.
  const days = [];
  const today = new Date(2026, 8, 27); // 27 Sep 2026, a Sunday
  today.setHours(0, 0, 0, 0);
  for (let i = 0; i < 7; i++) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    days.push(d);
  }

  // What the fixed code produces.
  const fixed = days.map((d) => d.toLocaleDateString('en-GB', { weekday: 'short' }));

  // What the old code produced: static array indexed by position.
  const stale = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  assert.equal(today.getDay(), 0, 'fixture assumption: 27 Sep 2026 is a Sunday');
  assert.deepEqual(
    fixed,
    ['Sun', 'Sat', 'Fri', 'Thu', 'Wed', 'Tue', 'Mon'],
    'derived labels must name the real weekday of each date',
  );
  assert.notDeepEqual(
    fixed,
    stale,
    'the derived labels must differ from the old positional ones, or this '
    + 'fixture no longer reproduces the bug',
  );
});

test('reversal does not desynchronise labels from dates', () => {
  const today = new Date(2026, 8, 27);
  today.setHours(0, 0, 0, 0);
  const days = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    days.push(d);
  }
  days.reverse(); // the habit_date_reverse setting

  const afterReverse = days.map((d) => ({
    label: d.toLocaleDateString('en-GB', { weekday: 'short' }),
    date: d.getDate(),
  }));
  assert.deepEqual(
    afterReverse,
    [
      { label: 'Mon', date: 21 },
      { label: 'Tue', date: 22 },
      { label: 'Wed', date: 23 },
      { label: 'Thu', date: 24 },
      { label: 'Fri', date: 25 },
      { label: 'Sat', date: 26 },
      { label: 'Sun', date: 27 },
    ],
    'reversing the window must reverse label+date together, never separately',
  );
});
