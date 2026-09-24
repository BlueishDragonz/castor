"""Slice 12 — MoreSheet navigation smoke test.

Locks down parity row 8.2 (MoreSheet on mobile) by pinging each
navigation target against a live Astro dev server and asserting the
expected response code:

  /help       → 200 (static, no session required)
  /security   → 302 /login (auth-gated)
  /tokens     → 302 /login
  /circles    → 302 /login
  /import     → 302 /login
  /export     → 302 /login

Skipped when running under pytest in CI without a dev server bound.
Run locally with:

    cd web/concepts && pnpm exec astro dev --port 4399 --host 127.0.0.1 &
    pytest tests/test_slice12_moresheet_nav.py -q
"""
import os
import socket
import unittest
from urllib.error import URLError
from urllib.request import Request, urlopen

DEV_HOST = os.environ.get('CASTOR_ASTRO_DEV_HOST', '127.0.0.1')
DEV_PORT = int(os.environ.get('CASTOR_ASTRO_DEV_PORT', '4399'))

# Targets extracted from web/concepts/src/components/MoreSheet.tsx.
TARGETS = [
    ('/help', 200),
    ('/security', 302),
    ('/tokens', 302),
    ('/circles', 302),
    ('/import', 302),
    ('/export', 302),
]


def _probe(host: str, port: int, path: str, timeout: float = 2.0):
    """Return (status_code, redirect_location). Status 0 = connection failure."""
    # Use a raw HTTP socket so we observe the actual response code
    # without urllib auto-following 302s to /login.
    import http.client
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request('GET', path)
        resp = conn.getresponse()
        loc = resp.getheader('Location')
        # Drain body so the connection can close cleanly.
        resp.read()
        conn.close()
        return resp.status, loc
    except (OSError, http.client.HTTPException):
        return 0, None


def _server_up(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@unittest.skipUnless(
    _server_up(DEV_HOST, DEV_PORT),
    f'Astro dev server not reachable at {DEV_HOST}:{DEV_PORT}. '
    'Run `pnpm exec astro dev --port 4399 --host 127.0.0.1` first.',
)
class Slice12MoreSheetNavTests(unittest.TestCase):
    """Probe every nav target emitted by MoreSheet.onClick."""

    def test_help_does_not_404(self):
        status, loc = _probe(DEV_HOST, DEV_PORT, '/help')
        self.assertEqual(status, 200, f'/help returned {status}; expected 200')
        self.assertFalse(loc, '/help should not redirect')

    def test_security_redirects_to_login(self):
        status, loc = _probe(DEV_HOST, DEV_PORT, '/security')
        self.assertEqual(status, 302, f'/security returned {status}; expected 302')
        self.assertTrue(
            loc and loc.startswith('/login'),
            f'/security should redirect to /login; got Location={loc!r}',
        )

    def test_tokens_redirects_to_login(self):
        status, loc = _probe(DEV_HOST, DEV_PORT, '/tokens')
        self.assertEqual(status, 302)
        self.assertTrue(loc and loc.startswith('/login'),
                        f'/tokens Location={loc!r}')

    def test_circles_redirects_to_login(self):
        status, loc = _probe(DEV_HOST, DEV_PORT, '/circles')
        self.assertEqual(status, 302)
        self.assertTrue(loc and loc.startswith('/login'),
                        f'/circles Location={loc!r}')

    def test_import_redirects_to_login(self):
        status, loc = _probe(DEV_HOST, DEV_PORT, '/import')
        self.assertEqual(status, 302)
        self.assertTrue(loc and loc.startswith('/login'),
                        f'/import Location={loc!r}')

    def test_export_redirects_to_login(self):
        status, loc = _probe(DEV_HOST, DEV_PORT, '/export')
        self.assertEqual(status, 302)
        self.assertTrue(loc and loc.startswith('/login'),
                        f'/export Location={loc!r}')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
