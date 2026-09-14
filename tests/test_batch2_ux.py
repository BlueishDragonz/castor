"""Tests for Batch 2 of the 09-12 merge: UX overhaul (login flow, navbar,
components, CSS).

Batch 2 overwrites 5 files from upstream main + merges layout.py with today's
bottom-nav additions preserved.

TDD red phase: every test in this file must FAIL before Batch 2 code is
applied. TDD green phase: every test passes after the code lands.

Architectural notes:
- routes/routes.py exposes `init_gui_routes(app: FastAPI)` which registers
  the @ui.page-decorated functions. We call it on a fresh FastAPI() to inspect
  the page list.
- NiceGUI @ui.page decorators register pages on the app's internal state, not
  as FastAPI routes. To find registered pages we use `app.user_middleware` or
  the underlying storage. Easier: check function source for @ui.page decorators
  with the expected paths.
- frontend/layout.py is a merge — these tests assert that the merged version
  contains BOTH the 09-12 additions AND today's bottom-nav additions.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path("/opt/castor")
SRC_ROOT = REPO_ROOT / "beaverhabits"
sys.path.insert(0, str(SRC_ROOT))


# ---------------------------------------------------------------------------
# Batch 2.1: configs.py — WebAuthn settings + FIRST_DAY_OF_WEEK=0
# ---------------------------------------------------------------------------

class TestConfigsWEBAUTHNSettings:
    def test_webauthn_rp_id_setting_exists(self):
        from beaverhabits.configs import settings

        assert hasattr(settings, "WEBAUTHN_RP_ID"), (
            "settings.WEBAUTHN_RP_ID missing — Batch 2 not applied"
        )

    def test_webauthn_rp_name_setting_exists(self):
        from beaverhabits.configs import settings

        assert hasattr(settings, "WEBAUTHN_RP_NAME")

    def test_webauthn_origin_setting_exists(self):
        from beaverhabits.configs import settings

        assert hasattr(settings, "WEBAUTHN_ORIGIN")

    def test_webauthn_timeout_setting_exists(self):
        from beaverhabits.configs import settings

        assert hasattr(settings, "WEBAUTHN_TIMEOUT")

    def test_first_day_of_week_is_literal_zero(self):
        """09-12 replaces `calendar.MONDAY` with literal `0` so the config
        doesn't need the calendar import."""
        from beaverhabits.configs import settings

        assert getattr(settings, "FIRST_DAY_OF_WEEK", None) == 0


# ---------------------------------------------------------------------------
# Batch 2.2: frontend/css.py — FONT_CSS for Inter
# ---------------------------------------------------------------------------

CSS_PATH = SRC_ROOT / "frontend" / "css.py"


class TestFontCss:
    def test_font_css_constant_exists(self):
        import beaverhabits.frontend.css as css

        assert hasattr(css, "FONT_CSS"), (
            "css.FONT_CSS missing — Batch 2 not applied"
        )

    def test_font_css_contains_font_face(self):
        import beaverhabits.frontend.css as css

        assert "@font-face" in css.FONT_CSS, (
            "css.FONT_CSS does not declare @font-face"
        )

    def test_font_css_uses_inter_family(self):
        import beaverhabits.frontend.css as css

        assert "'Inter'" in css.FONT_CSS or '"Inter"' in css.FONT_CSS, (
            "css.FONT_CSS does not reference 'Inter' font family"
        )


# ---------------------------------------------------------------------------
# Batch 2.3: frontend/components.py — menu_icon_item icon arg, auth_card,
#            add_webauthn_javascript, compat_card
# NOTE: components.py has a circular import with views.py (views imports
# `redirect` at top-level, but components imports views at top-level). Tests
# that `import beaverhabits.frontend.components` directly will always fail with
# the circular import — so we test these symbols at source-file level instead.
# ---------------------------------------------------------------------------

COMPONENTS_PATH = SRC_ROOT / "frontend" / "components.py"


class TestComponentsSymbols:
    def test_menu_icon_item_accepts_icon_kwarg(self):
        """The def signature must include `icon: ... = ...` parameter."""
        assert COMPONENTS_PATH.exists()
        src = COMPONENTS_PATH.read_text()
        # Find the def menu_icon_item line and inspect its signature
        m = re.search(r"^def menu_icon_item\(([^)]+)\)", src, re.MULTILINE)
        assert m, "def menu_icon_item(...) not found"
        sig = m.group(1)
        assert "icon:" in sig, (
            f"menu_icon_item signature {sig!r} has no `icon:` parameter"
        )
        # icon must have a default (optional)
        assert "icon: " in sig and "= None" in sig, (
            f"menu_icon_item's icon must default to None (got {sig!r})"
        )

    def test_auth_card_function_exists(self):
        assert COMPONENTS_PATH.exists()
        src = COMPONENTS_PATH.read_text()
        assert re.search(r"^def auth_card\(", src, re.MULTILINE), (
            "def auth_card(...) not found in components.py"
        )

    def test_add_webauthn_javascript_exists(self):
        assert COMPONENTS_PATH.exists()
        src = COMPONENTS_PATH.read_text()
        assert re.search(r"^def add_webauthn_javascript\(", src, re.MULTILINE)

    def test_compat_card_exists(self):
        assert COMPONENTS_PATH.exists()
        src = COMPONENTS_PATH.read_text()
        assert re.search(r"^def compat_card\(", src, re.MULTILINE)

    def test_security_page_now_imports_cleanly_via_main(self):
        """After Batch 2 lands, security_page.py should be importable via the
        same path the runtime uses: import main (which sets up import order),
        then attribute-access. This is the path the skill documents as the
        reliable way to handle the circular import.

        NOTE: The fork's `main.py` currently triggers a SyntaxError in
        routes/api.py (line 380: `except ValueError, KeyError:` — Python-2
        style that Python 3 rejects). This test is therefore gated: it skips
        if the SyntaxError surfaces. Batch 3 must fix api.py; this test will
        then become active and pass."""
        # Clear any cached import state from earlier tests
        for mod in list(sys.modules):
            if mod.startswith("beaverhabits"):
                del sys.modules[mod]

        try:
            import beaverhabits.main  # noqa: F401
        except SyntaxError as e:
            pytest.skip(
                f"beaverhabits.main import blocked by SyntaxError in api.py "
                f"(Batch 3 must fix): {e}"
            )

        # After main has loaded, security_page is reachable
        from beaverhabits.frontend import security_page

        assert hasattr(security_page, "security_page")


# ---------------------------------------------------------------------------
# Batch 2.4: frontend/menu.py — add_menu uses icon kwarg
# ---------------------------------------------------------------------------

MENU_PATH = SRC_ROOT / "frontend" / "menu.py"


class TestMenuUsesIconArg:
    def test_add_menu_uses_icon_kwarg(self):
        """09-12's menu.py: add() is now menu_icon_item('Add', ..., icon='sym_o_add').
        Check the source file directly to avoid triggering the components↔menu
        circular import."""
        assert MENU_PATH.exists()
        src = MENU_PATH.read_text()
        assert "icon=" in src, (
            "frontend/menu.py has no icon= kwarg — icon hints missing"
        )


# ---------------------------------------------------------------------------
# Batch 2.5: frontend/layout.py — MERGE (today's bottom-nav + 09-12's additions)
# ---------------------------------------------------------------------------

LAYOUT_PATH = SRC_ROOT / "frontend" / "layout.py"


class TestLayoutMerge:
    def test_font_css_is_injected_in_custom_headers(self):
        """09-12 adds `ui.add_css(css.FONT_CSS)` in custom_headers() — must
        appear in the merged layout.py."""
        assert LAYOUT_PATH.exists()
        src = LAYOUT_PATH.read_text()
        assert "css.FONT_CSS" in src, (
            "layout.py does not inject css.FONT_CSS — Inter font won't load"
        )

    def test_menu_icon_items_have_sym_o_icons(self):
        """09-12's menu_component() uses sym_o_construction, sym_o_swap_vert,
        sym_o_download, sym_o_upload, sym_o_bar_chart, sym_o_shield,
        sym_o_help, sym_o_logout on the menu_icon_item calls. The merged
        layout must include at least these icon names."""
        assert LAYOUT_PATH.exists()
        src = LAYOUT_PATH.read_text()
        for icon in ("sym_o_construction", "sym_o_shield", "sym_o_help", "sym_o_logout"):
            assert icon in src, f"layout.py missing menu icon {icon!r}"

    def test_security_menu_entry_present(self):
        """09-12 adds a Security menu entry between separator and Help."""
        assert LAYOUT_PATH.exists()
        src = LAYOUT_PATH.read_text()
        assert '"Security"' in src or "'Security'" in src, (
            "layout.py does not contain a Security menu entry"
        )

    # ---- today's bottom-nav additions MUST be preserved through the merge ----

    def test_mobile_detection_block_preserved(self):
        """The pre-merge (today's) layout has a mobile-detection block via
        is_mobile(_ua). It must survive the merge."""
        assert LAYOUT_PATH.exists()
        src = LAYOUT_PATH.read_text()
        assert "is_mobile" in src, (
            "layout.py has no is_mobile() call — today's mobile detection was lost"
        )

    def test_pb20_padding_for_mobile_preserved(self):
        assert LAYOUT_PATH.exists()
        src = LAYOUT_PATH.read_text()
        assert 'pb-20' in src, (
            "layout.py has no 'pb-20' class — bottom-nav padding was lost"
        )

    def test_bottom_nav_call_preserved(self):
        assert LAYOUT_PATH.exists()
        src = LAYOUT_PATH.read_text()
        assert "bottom_nav()" in src, (
            "layout.py does not call bottom_nav() — mobile chrome was lost"
        )

    def test_conditional_reorder_icon_preserved(self):
        """Mobile shows a Reorder icon, desktop shows the hamburger."""
        assert LAYOUT_PATH.exists()
        src = LAYOUT_PATH.read_text()
        # Today's branch: if mobile: menu_icon_button("sym_o_swap_vert"...); else: menu_component()
        assert 'sym_o_swap_vert' in src, (
            "layout.py missing the mobile-only Reorder icon (sym_o_swap_vert)"
        )

    def test_imports_bottom_nav_and_device_modules(self):
        """The merge must keep importing the new modules added today.
        NOTE: imports are LAZY (inside the layout() function) to avoid a
        layout↔bottom_nav circular import — bottom_nav.py imports `redirect`
        from layout at module level, so layout cannot import bottom_nav at
        module level either."""
        assert LAYOUT_PATH.exists()
        src = LAYOUT_PATH.read_text()
        # Lazy imports — must appear inside the layout() function body, not at module top
        assert "from beaverhabits.frontend.bottom_nav import bottom_nav" in src
        assert "from beaverhabits.frontend.device import is_mobile" in src
        # And they must NOT be at the top of the file
        head_section = "\n".join(src.split("\n")[:30])
        assert "from beaverhabits.frontend.bottom_nav import bottom_nav" not in head_section, (
            "bottom_nav import should be lazy (inside layout()), not at module top"
        )


# ---------------------------------------------------------------------------
# Batch 2.6: routes/routes.py — email-first login + security page wiring
# ---------------------------------------------------------------------------

class TestRoutesLoginFlow:
    def test_login_page_function_exists(self):
        from beaverhabits.routes import routes

        assert hasattr(routes, "login_page"), (
            "routes.login_page missing — email-first login flow not wired"
        )

    def test_gui_security_function_exists(self):
        from beaverhabits.routes import routes

        assert hasattr(routes, "gui_security"), (
            "routes.gui_security missing — Security page not exposed at /gui/security"
        )

    def test_normalize_email_helper_exists(self):
        from beaverhabits.routes import routes

        assert hasattr(routes, "normalize_email")

    def test_is_valid_email_helper_exists(self):
        from beaverhabits.routes import routes

        assert hasattr(routes, "is_valid_email")

    def test_check_user_has_passkeys_helper_exists(self):
        from beaverhabits.routes import routes

        assert hasattr(routes, "_check_user_has_passkeys")

    def test_init_gui_routes_accepts_fastapi_app(self):
        from beaverhabits.routes.routes import init_gui_routes

        sig = inspect.signature(init_gui_routes)
        params = list(sig.parameters.keys())
        assert len(params) >= 1, (
            f"init_gui_routes takes no parameters; expected a FastAPI app"
        )
        # Convention: first arg is named fastapi_app
        assert "app" in params[0].lower() or "fastapi" in params[0].lower()

    def test_init_gui_routes_registers_security_page(self):
        """After calling init_gui_routes(app), the @ui.page('/gui/security')
        decorator should have registered gui_security on the app."""
        from fastapi import FastAPI

        from beaverhabits.routes.routes import init_gui_routes

        app = FastAPI()
        init_gui_routes(app)

        # NiceGUI stores @ui.page-decorated functions on app.storage or via
        # the ui instance. The simplest reliable check: the source of routes
        # module must contain the @ui.page("/gui/security") decorator line.
        from beaverhabits.routes import routes as routes_mod

        src = Path(routes_mod.__file__).read_text()
        assert '@ui.page("/gui/security")' in src, (
            "routes.py does not have @ui.page('/gui/security') decorator"
        )

    def test_login_page_source_has_email_first_logic(self):
        """The 09-12 login_page implements email-first: capture email,
        validate, then surface passkey prompt."""
        from beaverhabits.routes import routes as routes_mod

        src = Path(routes_mod.__file__).read_text()
        # Email-first signatures: calls to is_valid_email, _check_user_has_passkeys
        assert "is_valid_email" in src
        assert "_check_user_has_passkeys" in src

    def test_login_page_decorator_present(self):
        from beaverhabits.routes import routes as routes_mod

        src = Path(routes_mod.__file__).read_text()
        assert '@ui.page("/login")' in src
