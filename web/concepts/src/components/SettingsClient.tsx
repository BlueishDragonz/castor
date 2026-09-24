'use client';

/**
 * SettingsClient — client-side handler for the /settings page.
 *
 * Responsibilities (slice 11 + 12):
 *   - Custom CSS: on mount, GET /api/v1/user-configs and hydrate the
 *     textarea. On Save, PUT /api/v1/user-configs with the textarea
 *     value.
 *   - Display preferences (parity row 12.6): streak badge, total badge,
 *     date-reverse. Each toggle PUTs immediately on change. The
 *     underlying `/habits` page reads these server-side and lays out
 *     the grid accordingly.
 *
 * Persistence is server-authoritative. localStorage is kept as a
 * transient hint so the textarea doesn't appear empty during the
 * initial GET round-trip; the server value always wins on a successful
 * load.
 *
 * What this client deliberately does NOT do:
 *   - Habit-display grid preferences not yet wired (date_columns,
 *     first_day_of_week, tag filters, etc.): the backend's user_configs
 *     write endpoint accepts the three booleans + custom_css for now.
 *     Numeric / enum keys would need new schema fields and additional
 *     Pydantic Strict* types — a future slice.
 *   - Theme: handled server-side via the form POST in /settings.astro
 *     so Layout.astro re-renders with the new data-theme on redirect.
 */
import { useEffect, useState } from 'react';

const TEXTAREA_ID = 'custom-css';
const STATUS_ID = 'custom-css-status';
const STYLE_ID = 'castor-custom-css';
const SAVE_BTN_ID = 'custom-css-save';
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

type Status =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved'; at: string }
  | { kind: 'error'; message: string };

export function SettingsClient() {
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  useEffect(() => {
    const el = document.getElementById(TEXTAREA_ID) as HTMLTextAreaElement | null;
    if (!el) return;

    let cancelled = false;

    // Local cache hint while we wait for the GET round-trip.
    const cached = localStorage.getItem('castor_custom_css');
    if (cached && !el.value) {
      el.value = cached;
      applyCss(cached);
    }

    (async () => {
      try {
        const r = await fetch('/api/v1/user-configs', {
          credentials: 'same-origin',
        });
        if (!r.ok) {
          if (!cancelled) {
            setStatus({
              kind: 'error',
              message: `Could not load saved CSS (HTTP ${r.status}). Using local copy.`,
            });
          }
          return;
        }
        const data = (await r.json()) as { custom_css?: unknown };
        const css = typeof data?.custom_css === 'string' ? data.custom_css : '';
        if (cancelled) return;
        if (css && css !== el.value) {
          el.value = css;
          localStorage.setItem('castor_custom_css', css);
          applyCss(css);
        }
      } catch {
        if (!cancelled) {
          setStatus({ kind: 'error', message: 'Could not load saved CSS (network error).' });
        }
      }
    })();

    const saveBtn = document.getElementById(SAVE_BTN_ID) as HTMLButtonElement | null;
    if (saveBtn) {
      saveBtn.addEventListener('click', async (ev) => {
        ev.preventDefault();
        const css = el.value ?? '';
        setStatus({ kind: 'saving' });
        try {
          const r = await fetch('/api/v1/user-configs', {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ custom_css: css }),
          });
          if (!r.ok) {
            const body = await r.text();
            setStatus({
              kind: 'error',
              message: `Save rejected (HTTP ${r.status}): ${body.slice(0, 200)}`,
            });
            return;
          }
          localStorage.setItem('castor_custom_css', css);
          applyCss(css);
          setStatus({ kind: 'saved', at: new Date().toISOString() });
        } catch {
          setStatus({ kind: 'error', message: 'Save failed (network error).' });
        }
      });
    }

    return () => {
      cancelled = true;
    };
  }, []);

  // ───────────────────────── Display preferences (12.6) ─────────────────
  // Each toggle PUTs immediately. The toggle's `checked` reflects
  // local state seeded from the GET round-trip via data-initial;
  // toggling sends the new value and updates the data-initial so a
  // re-render re-syncs with the server's authoritative answer.
  const [displays, setDisplays] = useState<DisplayPrefs>({
    show_streak: false,
    show_total: false,
    date_reverse: false,
  });
  const [displayStatus, setDisplayStatus] = useState<DisplayStatus>({
    kind: 'idle',
  });

  useEffect(() => {
    // Hydrate toggles from /api/v1/user-configs, with a cookie fallback
    // so toggles reflect the user's last local session even if the GET
    // round-trip is slow or fails. The server value always wins on a
    // successful load.
    let cancelled = false;
    const seedFromCookies: DisplayPrefs = {
      show_streak: readBoolCookie('habit_show_streak'),
      show_total: readBoolCookie('habit_show_total'),
      date_reverse: readBoolCookie('habit_date_reverse'),
    };
    setDisplays(seedFromCookies);
    for (const key of DISPLAY_KEYS) {
      const el = document.getElementById(`display-${key}`) as
        | HTMLInputElement
        | null;
      if (el) el.checked = seedFromCookies[key];
    }
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
          const el = document.getElementById(`display-${key}`) as
            | HTMLInputElement
            | null;
          if (el) el.checked = next[key];
          // Also mirror server values to cookies so /habits (server-side
          // cookie reads) reflects them even before the user toggles.
          document.cookie = `habit_${key}=${next[key] ? 'true' : 'false'}; path=/; max-age=31536000; samesite=lax`;
        }
      } catch {
        // Network failure: silently leave toggles in cookie-seed state.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onToggle(key: DisplayKey, value: boolean) {
    setDisplays((d) => ({ ...d, [key]: value }));
    // Mirror the value to the cookie too so /habits (which reads from
    // cookies server-side) reflects the change on the next request.
    // Server-stored user_configs remains source-of-truth across devices.
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
        // Roll back: revert the toggle to its prior value.
        setDisplays((d) => ({ ...d, [key]: !value }));
        const el = document.getElementById(`display-${key}`) as
          | HTMLInputElement
          | null;
        if (el) el.checked = !value;
        return;
      }
      setDisplayStatus({ kind: 'saved', key, at: new Date().toISOString() });
    } catch {
      setDisplayStatus({
        kind: 'error',
        key,
        message: 'Save failed (network error).',
      });
      setDisplays((d) => ({ ...d, [key]: !value }));
      const el = document.getElementById(`display-${key}`) as
        | HTMLInputElement
        | null;
      if (el) el.checked = !value;
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
    <>
      <p
        id={STATUS_ID}
        role={status.kind === 'error' ? 'alert' : 'status'}
        aria-live="polite"
        className={`text-xs mt-2 ${status.kind === 'error' ? 'text-destructive' : 'text-muted-foreground'}`}
        data-saved={status.kind === 'saved' ? 'server' : ''}
      >
        {statusText}
      </p>
      <p
        id={DISPLAY_STATUS_ID}
        role={displayStatus.kind === 'error' ? 'alert' : 'status'}
        aria-live="polite"
        className={`text-xs mt-2 ${displayStatus.kind === 'error' ? 'text-destructive' : 'text-muted-foreground'}`}
        data-saved={displayStatus.kind === 'saved' ? 'server' : ''}
      >
        {/* Display status text overlaps with the CSS status in the
            same column; only one will be in 'saving'/'saved' state at
            a time in practice. Keep both for clarity. */}
        {statusText ? '' : displayStatus.kind === 'idle' ? '' : statusText}
      </p>
      {/* Hidden helper — the actual toggle inputs are emitted from
          settings.astro as `<input type="checkbox" id="display-{key}">`.
          We bind their change listener below. */}
      <DisplayPrefBinder
        keyId="show_streak"
        onToggle={(v) => onToggle('show_streak', v)}
      />
      <DisplayPrefBinder
        keyId="show_total"
        onToggle={(v) => onToggle('show_total', v)}
      />
      <DisplayPrefBinder
        keyId="date_reverse"
        onToggle={(v) => onToggle('date_reverse', v)}
      />
    </>
  );
}

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

function DisplayPrefBinder({
  keyId,
  onToggle,
}: {
  keyId: DisplayKey;
  onToggle: (next: boolean) => void;
}) {
  useEffect(() => {
    const el = document.getElementById(`display-${keyId}`) as
      | HTMLInputElement
      | null;
    if (!el) return;
    const handler = () => onToggle(el.checked);
    el.addEventListener('change', handler);
    return () => el.removeEventListener('change', handler);
  }, [keyId, onToggle]);
  return null;
}

function applyCss(css: string): void {
  let styleEl = document.getElementById(STYLE_ID);
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = STYLE_ID;
    document.head.appendChild(styleEl);
  }
  styleEl.textContent = css;
}

export default SettingsClient;
