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

  // Display preferences moved to <DisplayCard /> (slice 16). SettingsClient
  // now only handles the Custom CSS textarea + status. The Display card is
  // a separate React island on the same page so it can render the shadcn
  // Checkbox primitive (raw HTML <input> can't use Radix Checkbox state).
  // Both islands share the same /api/v1/user-configs endpoint.

  const cssStatusText =
    status.kind === 'saving'
      ? 'Saving…'
      : status.kind === 'saved'
        ? `Saved at ${new Date(status.at).toLocaleTimeString()}.`
        : status.kind === 'error'
          ? status.message
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
        {cssStatusText}
      </p>
    </>
  );
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
