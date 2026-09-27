"""CSRF origin allowlist must cover the origin the BFF actually forwards.

Found by dogfooding the deployed app: every browser write returned 403 and no
user could register. The cause was not obvious from either side alone.

  - The Astro BFF forwards the PUBLIC Origin (F21) so the backend, not the
    frontend, decides CSRF.
  - The backend listens on 127.0.0.1:8081, so when CSRF_ALLOWED_ORIGINS was
    empty its fallback derived the allowed origin from the Host header, i.e.
    the loopback address.
  - Public origin and loopback origin can never match, so every POST/PUT/
    PATCH/DELETE was rejected. The whole application was unusable through a
    browser while every backend test passed.

The allowlist now always includes FRONTEND_URL. These tests pin that
behaviour, including the exact mismatch that caused the outage.
"""
import pytest

from castor.configs import Settings
from castor.app.http_security import BrowserOriginMiddleware


def make(**kw):
    base = {"FRONTEND_URL": "http://10.8.0.1:8080", "CSRF_ALLOWED_ORIGINS": []}
    base.update(kw)
    return Settings(**base)


class FakeSend:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)


def call(middleware, *, method="POST", path="/auth/register", host, origin=None,
         cookie=None, sec_fetch_site=None):
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    if sec_fetch_site is not None:
        headers.append((b"sec-fetch-site", sec_fetch_site.encode()))
    scope = {"type": "http", "method": method, "path": path,
             "scheme": "http", "headers": headers}
    sent = FakeSend()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware.app = app
    import asyncio
    # asyncio.run, not get_event_loop: on 3.14 there is no current loop by
    # default and get_event_loop raises.
    asyncio.run(middleware(scope, None, sent))
    status = sent.messages[0].get("status", 200)
    return status


# --- the production failure, reproduced -----------------------------------

def test_bff_forwarded_public_origin_is_accepted():
    """The exact production request: backend on loopback, public Origin sent.

    Before the fix this returned 403 and the app was unusable in a browser.
    """
    s = make()
    m = BrowserOriginMiddleware(app=None, allowed_origins=s.csrf_allowed_origins())
    status = call(m, host="127.0.0.1:8081", origin="http://10.8.0.1:8080")
    assert status == 200, "BFF's forwarded public Origin was rejected"


def test_register_and_login_are_not_403_with_production_config():
    """Both user-facing entry points must survive a browser round trip."""
    s = make()
    m = BrowserOriginMiddleware(app=None, allowed_origins=s.csrf_allowed_origins())
    for path in ("/auth/register", "/auth/login"):
        assert call(m, path=path, host="127.0.0.1:8081",
                    origin="http://10.8.0.1:8080") == 200, path


# --- the defence must still hold -------------------------------------------

def test_genuinely_cross_origin_write_is_still_rejected():
    """A real attacker origin is still refused. This is the point of the check."""
    s = make()
    m = BrowserOriginMiddleware(app=None, allowed_origins=s.csrf_allowed_origins())
    assert call(m, host="127.0.0.1:8081", origin="https://evil.tld") == 403


def test_sec_fetch_site_cross_site_is_rejected_even_on_allowed_origin():
    """A cross-site marker from an allowlisted origin is still refused."""
    s = make()
    m = BrowserOriginMiddleware(app=None, allowed_origins=s.csrf_allowed_origins())
    assert call(m, host="127.0.0.1:8081", origin="http://10.8.0.1:8080",
                sec_fetch_site="cross-site") == 403


def test_cookie_bearing_write_with_no_origin_is_rejected():
    s = make()
    m = BrowserOriginMiddleware(app=None, allowed_origins=s.csrf_allowed_origins())
    assert call(m, host="127.0.0.1:8081", cookie="castor_token=x") == 403


# --- allowlist construction ------------------------------------------------

def test_frontend_url_is_included_when_list_is_empty():
    s = make()
    assert s.csrf_allowed_origins() == ["http://10.8.0.1:8080"]


def test_explicit_list_is_preserved_and_frontend_appended():
    s = make(CSRF_ALLOWED_ORIGINS=["https://castor.example"])
    assert s.csrf_allowed_origins() == [
        "https://castor.example", "http://10.8.0.1:8080"]


def test_frontend_url_is_not_duplicated():
    s = make(CSRF_ALLOWED_ORIGINS=["http://10.8.0.1:8080"])
    assert s.csrf_allowed_origins().count("http://10.8.0.1:8080") == 1


def test_blank_frontend_url_does_not_produce_an_empty_entry():
    s = make(FRONTEND_URL="   ")
    assert "" not in s.csrf_allowed_origins()
    assert " " not in s.csrf_allowed_origins()


def test_dev_default_still_allows_localhost():
    """A fresh checkout keeps working without extra configuration."""
    s = make(FRONTEND_URL="http://localhost:4321")
    assert s.csrf_allowed_origins() == ["http://localhost:4321"]
    m = BrowserOriginMiddleware(app=None, allowed_origins=s.csrf_allowed_origins())
    assert call(m, host="127.0.0.1:8085", origin="http://localhost:4321") == 200
