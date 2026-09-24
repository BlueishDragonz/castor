'use client';

/**
 * DisplayCard — client-side React component that renders the Display
 * preferences section of /settings. Replaces the raw `<input
 * type="checkbox">` markup that Astro emitted before slice 16, so the
 * three toggles use the same shadcn Checkbox primitive as the rest of
 * the app and visually match the radios + buttons on the same page.
 *
 * Behaviour is identical to the pre-slice-16 dual-mount pattern:
 *   - GET /api/v1/user-configs on mount seeds the toggles (with a
 *     cookie fallback so they reflect the last local session even if
 *     the GET round-trip is slow or fails).
 *   - Each toggle PUTs immediately to /api/v1/user-configs.
 *   - The new value is mirrored to a `habit_${key}` cookie so /habits
 *     (which reads cookies server-side) sees the change on the next
 *     request. Server-stored user_configs remains source-of-truth.
 *   - If the PUT fails, the toggle rolls back to its prior value.
 *
 * The cookie mirror was inherited from the pre-slice-16 code; the
 * brief doesn't ask for it to be removed, and rolling it back is out
 * of scope for the cosmetic fix.
 */
import { useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';

const DISPLAY_STATUS_ID = 'display-prefs-status';

type DisplayKey = 'show_streak' | 'show_total' | 'date_reverse';
const DISPLAY_KEYS: readonly DisplayKey[] = [
  'show_streak',
  'show_total',
  'date_reverse',
] as const;

type DisplayPrefs = Record<DisplayKey, boolean>;

type DisplayStatus =
  | { kind: 'idle' }
  | { kind: 'saving'; key: DisplayKey }
  | { kind: 'saved'; key: DisplayKey; at: string }
  | { kind: 'error'; key: DisplayKey; message: string };

const LABELS: Record<DisplayKey, string> = {
  show_streak: 'Show streak badge',
  show_total: 'Show total-completion badge',
  date_reverse: 'Show date columns in reverse (oldest first)',
};

function readBool(v: unknown): boolean {
  return typeof v === 'boolean' ? v : false;
}

function readBoolCookie(name: string): boolean {
  if (typeof document === 'undefined') return false;
  const match = document.cookie
    .split('; ')
    .find((c) => c.startsWith(`${name}=`));
  if (!match) return false;
  return match.split('=')[1] === 'true';
}

export function DisplayCard() {
  const [displays, setDisplays] = useState<DisplayPrefs>({
    show_streak: false,
    show_total: false,
    date_reverse: false,
  });
  const [displayStatus, setDisplayStatus] = useState<DisplayStatus>({
    kind: 'idle',
  });
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const seedFromCookies: DisplayPrefs = {
      show_streak: readBoolCookie('habit_show_streak'),
      show_total: readBoolCookie('habit_show_total'),
      date_reverse: readBoolCookie('habit_date_reverse'),
    };
    setDisplays(seedFromCookies);
    (async () => {
      try {
        const r = await fetch('/api/v1/user-configs', {
          credentials: 'same-origin',
        });
        if (!r.ok) return;
        const data = (await r.json()) as Record<string, unknown>;
        if (cancelled) return;
        const next: DisplayPrefs = {
          show_streak: readBool(data?.show_streak),
          show_total: readBool(data?.show_total),
          date_reverse: readBool(data?.date_reverse),
        };
        setDisplays(next);
        for (const key of DISPLAY_KEYS) {
          // Mirror server values to cookies so /habits (server-side
          // cookie reads) reflects them even before the user toggles.
          document.cookie = `habit_${key}=${next[key] ? 'true' : 'false'}; path=/; max-age=31536000; samesite=lax`;
        }
      } catch {
        // Network failure: silently leave toggles in cookie-seed state.
      } finally {
        if (!cancelled) setHydrated(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onToggle(key: DisplayKey, value: boolean) {
    const previous = displays[key];
    setDisplays((d) => ({ ...d, [key]: value }));
    document.cookie = `habit_${key}=${value ? 'true' : 'false'}; path=/; max-age=31536000; samesite=lax`;
    setDisplayStatus({ kind: 'saving', key });
    try {
      const r = await fetch('/api/v1/user-configs', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      });
      if (!r.ok) {
        const body = await r.text();
        setDisplayStatus({
          kind: 'error',
          key,
          message: `Save rejected (HTTP ${r.status}): ${body.slice(0, 200)}`,
        });
        setDisplays((d) => ({ ...d, [key]: previous }));
        document.cookie = `habit_${key}=${previous ? 'true' : 'false'}; path=/; max-age=31536000; samesite=lax`;
        return;
      }
      setDisplayStatus({ kind: 'saved', key, at: new Date().toISOString() });
    } catch {
      setDisplayStatus({
        kind: 'error',
        key,
        message: 'Save failed (network error).',
      });
      setDisplays((d) => ({ ...d, [key]: previous }));
      document.cookie = `habit_${key}=${previous ? 'true' : 'false'}; path=/; max-age=31536000; samesite=lax`;
    }
  }

  const statusText =
    displayStatus.kind === 'saving'
      ? 'Saving…'
      : displayStatus.kind === 'saved'
        ? `Saved at ${new Date(displayStatus.at).toLocaleTimeString()}.`
        : displayStatus.kind === 'error'
          ? displayStatus.message
          : '';

  return (
    <Card>
      <CardHeader>
        <CardTitle>Display</CardTitle>
        <CardDescription>
          Per-device habit grid options. Saved to your account immediately,
          applies on next visit.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {DISPLAY_KEYS.map((key) => (
          <label
            key={key}
            className="flex items-center gap-3 cursor-pointer text-sm select-none"
          >
            <Checkbox
              id={`display-${key}`}
              checked={hydrated ? displays[key] : false}
              onCheckedChange={(v) => onToggle(key, v === true)}
            />
            <span>{LABELS[key]}</span>
          </label>
        ))}
        <p
          id={DISPLAY_STATUS_ID}
          role={displayStatus.kind === 'error' ? 'alert' : 'status'}
          aria-live="polite"
          className={`text-xs mt-2 ${
            displayStatus.kind === 'error' ? 'text-destructive' : 'text-muted-foreground'
          }`}
          data-saved={displayStatus.kind === 'saved' ? 'server' : ''}
        >
          {statusText}
        </p>
        <p className="text-xs text-muted-foreground">
          Each toggle saves to <code>/api/v1/user-configs</code> on change. The
          home grid on /habits reads these server-side, so switching browser or
          device preserves your selection.
        </p>
      </CardContent>
    </Card>
  );
}

export default DisplayCard;
