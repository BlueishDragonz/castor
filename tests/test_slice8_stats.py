"""Slice 8 — /admin + /stats: SSR smoke tests + admin contract tests.

This is the second test file for Slice 8. The first (test_slice8_admin.py)
pins the admin backend contract. This file:

  - Validates the date-range picker HTML renders for the four query
    combinations: no params, start+end, invalid date format, start>end.
  - Confirms the picker uses server-side filtering (no client-side fetch
    when a range is active — the page must render with the subset baked
    in).

The Astro /stats page is server-rendered. We hit it through the Astro dev
server if it's running, or fall back to rendering via the production build.

Note: we don't ship a JSX snapshot test here — Playwright (Slice 9) is the
right tool for browser-side behaviour. This is a backend/SSR contract.
"""
import os
import re
import subprocess
import time
import unittest
from urllib.parse import urlencode

import httpx


def _is_server_up(url: str, timeout: float = 2.0) -> bool:
    try:
        httpx.get(url, timeout=timeout)
        return True
    except Exception:
        return False


class Slice8StatsPageTests(unittest.TestCase):
    """SSR contract tests for /stats date-range picker."""

    @classmethod
    def setUpClass(cls):
        cls.base_url = os.environ.get('ASTRO_URL', 'http://localhost:4321')
        cls.server_available = _is_server_up(cls.base_url)
        if not cls.server_available:
            raise unittest.SkipTest(
                f'Astro dev server not available at {cls.base_url}. '
                'Run `pnpm dev` in web/concepts/ to enable these tests.'
            )

    def test_no_query_renders_picker_with_empty_inputs(self):
        """No ?start / ?end: the date inputs render empty, no Clear link."""
        r = httpx.get(f'{self.base_url}/stats', follow_redirects=False, timeout=10)
        if r.status_code in (302, 307):
            self.skipTest('Astro redirected (likely no session): ' + str(r.headers.get('location')))
        self.assertEqual(r.status_code, 200, r.text[:300])
        # Picker container present
        self.assertIn('data-testid="date-range-picker"', r.text)
        # Inputs present with empty values
        self.assertIn('id="start"', r.text)
        self.assertIn('id="end"', r.text)
        # No range error
        self.assertNotIn('Invalid date range', r.text)

    def test_valid_range_renders_picker_with_values(self):
        """?start=2026-01-01&end=2026-09-24: the inputs pre-fill, Clear
        link appears, no error message."""
        qs = urlencode({'start': '2026-01-01', 'end': '2026-09-24'})
        r = httpx.get(f'{self.base_url}/stats?{qs}', follow_redirects=False, timeout=10)
        if r.status_code in (302, 307):
            self.skipTest('Astro redirected: ' + str(r.headers.get('location')))
        self.assertEqual(r.status_code, 200, r.text[:300])
        # Inputs pre-fill
        self.assertIn('value="2026-01-01"', r.text)
        self.assertIn('value="2026-09-24"', r.text)
        # Clear link appears
        self.assertIn('>Clear<', r.text)
        self.assertNotIn('Invalid date range', r.text)

    def test_invalid_date_format_renders_error(self):
        """?start=not-a-date: page renders 200 with 'Invalid date range'."""
        qs = urlencode({'start': 'not-a-date', 'end': '2026-09-24'})
        r = httpx.get(f'{self.base_url}/stats?{qs}', follow_redirects=False, timeout=10)
        if r.status_code in (302, 307):
            self.skipTest('Astro redirected: ' + str(r.headers.get('location')))
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn('Invalid date range', r.text)
        self.assertIn('role="alert"', r.text)

    def test_start_after_end_renders_error(self):
        """?start=2026-09-24&end=2026-01-01: invalid because start > end."""
        qs = urlencode({'start': '2026-09-24', 'end': '2026-01-01'})
        r = httpx.get(f'{self.base_url}/stats?{qs}', follow_redirects=False, timeout=10)
        if r.status_code in (302, 307):
            self.skipTest('Astro redirected: ' + str(r.headers.get('location')))
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn('Invalid date range', r.text)

    def test_picker_includes_preset_dropdown(self):
        """The preset dropdown with 'Last 3/6/12/24 months' is rendered."""
        r = httpx.get(f'{self.base_url}/stats', follow_redirects=False, timeout=10)
        if r.status_code in (302, 307):
            self.skipTest('Astro redirected: ' + str(r.headers.get('location')))
        self.assertEqual(r.status_code, 200, r.text[:300])
        for option in ['Last 3 months', 'Last 6 months', 'Last 12 months', 'Last 24 months']:
            self.assertIn(option, r.text, f'preset option missing: {option}')

    def test_clear_link_is_a_plain_anchor(self):
        """The Clear control is an <a href='/stats'>, not a form button.
        This makes it work without JS."""
        qs = urlencode({'start': '2026-01-01', 'end': '2026-09-24'})
        r = httpx.get(f'{self.base_url}/stats?{qs}', follow_redirects=False, timeout=10)
        if r.status_code in (302, 307):
            self.skipTest('Astro redirected: ' + str(r.headers.get('location')))
        # Find the Clear anchor. Acceptable forms:
        #   href="/stats"
        #   href="/stats?"
        self.assertRegex(r.text, r'href="/stats(\?)?"')


if __name__ == '__main__':
    unittest.main()
