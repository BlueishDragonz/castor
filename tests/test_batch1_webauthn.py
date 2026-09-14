"""Tests for Batch 1 of the 09-12 merge: WebAuthn routes + UserManager methods + DB model.

These tests exercise ONLY the source under /opt/castor/beaverhabits/. They are
import-level and schema-level — they don't require a running server, a Docker
container, or a real network. They prove Batch 1's promise: the WebAuthn code
is present, correctly structured, and self-consistent.

TDD red phase: every test in this file must FAIL before Batch 1 code is applied.
TDD green phase: every test passes after the code lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path("/opt/castor")
SRC_ROOT = REPO_ROOT / "beaverhabits"
sys.path.insert(0, str(SRC_ROOT))


# ---------------------------------------------------------------------------
# Batch 1.1: webauthn_routes.py exists with the expected router surface
# ---------------------------------------------------------------------------

class TestWebAuthnRoutesModule:
    def test_module_importable(self):
        """The webauthn_routes module must import without raising."""
        from beaverhabits.app import webauthn_routes  # noqa: F401

    def test_router_prefix(self):
        from beaverhabits.app.webauthn_routes import router

        assert router.prefix == "/auth/webauthn", (
            f"router.prefix was {router.prefix!r}, expected '/auth/webauthn'"
        )

    def test_router_has_expected_paths(self):
        from beaverhabits.app.webauthn_routes import router

        # FastAPI stores each route's full path with the prefix prepended;
        # we compare the path-relative-to-prefix instead so the test stays
        # robust against prefix changes.
        from fastapi.routing import APIRoute

        rel_paths = sorted(
            r.path[len(router.prefix):] or "/" for r in router.routes
        )
        expected = sorted([
            "/register/begin",
            "/register/complete",
            "/login/begin",
            "/login/complete",
            "/verify-password",
            "/credentials",
            "/credentials/{credential_id}",
        ])
        assert rel_paths == expected, (
            f"router routes mismatch (relative to prefix {router.prefix!r}).\n"
            f"  got: {rel_paths}\n  expected: {expected}"
        )

    def test_router_prefix_is_auth_webauthn(self):
        from beaverhabits.app.webauthn_routes import router

        assert router.prefix == "/auth/webauthn"


# ---------------------------------------------------------------------------
# Batch 1.2: security_page.py exists (file-level checks — module import
# depends on Batch 2's components.py rework and is exercised in Batch 2 tests)
# ---------------------------------------------------------------------------

SECURITY_PAGE_PATH = SRC_ROOT / "frontend" / "security_page.py"


class TestSecurityPageModule:
    def test_file_exists(self):
        """The security_page.py source file must exist on disk."""
        assert SECURITY_PAGE_PATH.exists(), (
            f"{SECURITY_PAGE_PATH} does not exist — Batch 1 not applied"
        )

    def test_file_size_matches_backup(self):
        """The 09-12 backup's security_page.py is 8,159 bytes. If our file
        size diverges significantly, we probably copied the wrong file."""
        assert SECURITY_PAGE_PATH.exists()
        size = SECURITY_PAGE_PATH.stat().st_size
        # Allow ±10% tolerance for line-ending differences from scp
        assert 7300 < size < 9000, (
            f"security_page.py is {size} bytes; expected ~8,159 (09-12 backup)"
        )

    def test_security_page_symbol_in_source(self):
        """The `security_page` page object must be defined in the source."""
        assert SECURITY_PAGE_PATH.exists()
        src = SECURITY_PAGE_PATH.read_text()
        assert "def security_page" in src, (
            "beaverhabits/frontend/security_page.py does not define security_page()"
        )

    def test_security_page_source_has_credential_management(self):
        """The 09-12 security_page handles passkey list / add / delete —
        sanity-check that the source contains the expected UI elements."""
        assert SECURITY_PAGE_PATH.exists()
        src = SECURITY_PAGE_PATH.read_text()
        assert "credential" in src.lower(), (
            "security_page.py source has no 'credential' references — "
            "is it the right file?"
        )


# ---------------------------------------------------------------------------
# Batch 1.3: app.app wires the webauthn router into the FastAPI app
# ---------------------------------------------------------------------------

class TestAppWiring:
    def test_webauthn_router_is_mounted(self):
        """`init_auth_routes` must mount the webauthn router. The app/app.py
        module does NOT export a FastAPI instance directly — it exposes
        init_auth_routes(app: FastAPI). Call it on a fresh app and assert the
        webauthn routes are present (descending into included routers)."""
        from fastapi import FastAPI

        from beaverhabits.app.app import init_auth_routes

        app = FastAPI()
        init_auth_routes(app)

        def all_paths(routes):
            """Recursively collect every route path, descending into included
            sub-routers via their .original_router.routes attribute."""
            out = []
            for r in routes:
                p = getattr(r, "path", None)
                if p:
                    out.append(p)
                # _IncludedRouter stores its source router under .original_router
                orig = getattr(r, "original_router", None)
                if orig is not None:
                    out.extend(all_paths(orig.routes))
            return out

        paths = all_paths(app.routes)
        webauthn_paths = [p for p in paths if "/auth/webauthn" in p]
        assert webauthn_paths, (
            f"no /auth/webauthn routes found on app; "
            f"all paths: {sorted(paths)[:30]}"
        )

    def test_init_auth_routes_does_not_raise(self):
        """Calling init_auth_routes on a fresh app must not raise ImportError
        or anything else — proves the webauthn_router import in app/app.py
        resolves cleanly."""
        from fastapi import FastAPI

        from beaverhabits.app.app import init_auth_routes

        app = FastAPI()
        init_auth_routes(app)  # would raise if webauthn_router import was broken


# ---------------------------------------------------------------------------
# Batch 1.4: UserManager has WebAuthn helper methods
# ---------------------------------------------------------------------------

class TestUserManagerWebAuthnMethods:
    def test_add_webauthn_credential_method_exists(self):
        from beaverhabits.app.users import UserManager

        assert hasattr(UserManager, "add_webauthn_credential"), (
            "UserManager.add_webauthn_credential missing — Batch 1 not applied"
        )

    def test_get_webauthn_credentials_method_exists(self):
        from beaverhabits.app.users import UserManager

        assert hasattr(UserManager, "get_webauthn_credentials")

    def test_delete_webauthn_credential_method_exists(self):
        from beaverhabits.app.users import UserManager

        assert hasattr(UserManager, "delete_webauthn_credential")

    def test_base64_imported_in_users(self):
        """The 09-12 users.py adds `import base64` for credential-id handling."""
        from beaverhabits.app import users as users_mod

        src = Path(users_mod.__file__).read_text()
        assert "import base64" in src, (
            "beaverhabits/app/users.py does not `import base64` — Batch 1 not applied"
        )


# ---------------------------------------------------------------------------
# Batch 1.5: WebAuthnCredential SQLAlchemy model exists with the right table name
# ---------------------------------------------------------------------------

class TestWebAuthnCredentialModel:
    def test_model_importable(self):
        from beaverhabits.app.db import WebAuthnCredential  # noqa: F401

    def test_tablename_is_webauthn_credential(self):
        from beaverhabits.app.db import WebAuthnCredential

        assert WebAuthnCredential.__tablename__ == "webauthn_credential"

    def test_model_has_expected_columns(self):
        """The model must declare at least these columns so the existing
        habits.db schema (1 row for the USB passkey) keeps mapping cleanly."""
        from beaverhabits.app.db import WebAuthnCredential

        required = {
            "id", "user_id", "public_key", "sign_count",
            "transports", "aaguid", "backup_eligible", "backup_state",
            "name",
        }
        actual = set(WebAuthnCredential.__table__.columns.keys())
        missing = required - actual
        assert not missing, f"WebAuthnCredential missing columns: {missing}"


# ---------------------------------------------------------------------------
# Batch 1.6: webauthn==3.0.0 importable
# ---------------------------------------------------------------------------

class TestWebAuthnPackage:
    def test_webauthn_importable(self):
        import webauthn  # noqa: F401

    def test_webauthn_version_is_3_0_0(self):
        import webauthn

        version = getattr(webauthn, "__version__", None)
        if version is None:
            try:
                from importlib.metadata import version as meta_version

                version = meta_version("webauthn")
            except Exception:
                pytest.fail(
                    "could not determine webauthn version "
                    "(no __version__ and no importlib.metadata entry)"
                )
        major, minor, *_ = (int(p) for p in version.split(".")[:3])
        assert (major, minor) == (3, 0), (
            f"webauthn version {version!r} - expected 3.0.x"
        )
