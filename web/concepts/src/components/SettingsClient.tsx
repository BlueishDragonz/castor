'use client';

/**
 * SettingsClient — minimal client-side handler for the /settings page.
 *
 * Current responsibilities:
 *   - Custom CSS: hydrate the textarea from any locally cached CSS on
 *     mount, and apply it via a <style> tag so it takes effect on
 *     the current page.
 *
 * What this client deliberately does NOT do:
 *   - Habit-display toggles (streak badge, total badge, date columns,
 *     tag filters): the backend has no user_configs write endpoint
 *     that accepts them. Those toggles were removed from /settings
 *     until the backend lands a user-configs endpoint — see
 *     docs/plan/migration-plan.md Phase 3 P2 "polish".
 *   - Theme: handled server-side via the form POST in /settings.astro
 *     so Layout.astro re-renders with the new data-theme on redirect.
 *
 * Once the backend exposes POST /api/v1/user-configs (planned for
 * Phase 3 P2), wire a Save button + PUT here. Until then the custom
 * CSS textarea is read-only-ish — the user can write in it and see
 * the result locally, but persistence requires the backend endpoint.
 */

import { useEffect } from 'react';

export function SettingsClient() {
  useEffect(() => {
    const cached = localStorage.getItem('castor_custom_css');
    const el = document.getElementById('custom-css') as HTMLTextAreaElement | null;
    if (!el) return;

    if (cached) {
      el.value = cached;
      applyCss(cached);
    }

    // Local-only "save": store in localStorage and apply. Honest about
    // the backend gap by surfacing a status attribute the page can read.
    el.addEventListener('blur', () => {
      const css = el.value ?? '';
      localStorage.setItem('castor_custom_css', css);
      applyCss(css);
      el.setAttribute('data-saved', 'local');
    });
  }, []);

  return null;
}

function applyCss(css: string): void {
  let styleEl = document.getElementById('castor-custom-css');
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = 'castor-custom-css';
    document.head.appendChild(styleEl);
  }
  styleEl.textContent = css;
}

export default SettingsClient;
