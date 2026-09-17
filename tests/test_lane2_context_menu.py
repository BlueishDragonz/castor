"""Behavior regression tests for phone-only context-menu suppression.

Run without NiceGUI's browser plugin:
    python tests/test_lane2_context_menu.py
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from beaverhabits.frontend import javascript

ROOT = Path(__file__).resolve().parents[1]
PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36"
DESKTOP = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
TABLET = "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"


class ContextMenuTests(unittest.TestCase):
    def run_headers(self, ua):
        """Execute the actual custom_headers body, isolating unrelated theme/UI work."""
        tree = ast.parse((ROOT / "beaverhabits/frontend/layout.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "custom_headers")
        module = ast.Module(body=[function], type_ignores=[])
        ui = MagicMock()
        request = None if ua is None else SimpleNamespace(headers={"user-agent": ua})
        context = SimpleNamespace(client=SimpleNamespace(request=request))
        namespace = {
            "ui": ui,
            "page_path": lambda: "/",
            "settings": SimpleNamespace(UMAMI_ANALYTICS_ID=""),
            "css": SimpleNamespace(WHITE_FLASH_PREVENT="", TEXTAREA_CSS="", FONT_CSS="", BH_DESIGN_CSS=""),
            "views": MagicMock(),
            "PREVENT_CONTEXT_MENU": javascript.PREVENT_CONTEXT_MENU,
            "prevent_context_menu": javascript.prevent_context_menu,
        }
        with patch.object(javascript, "ui", ui), patch("nicegui.context", context):
            exec(compile(module, "layout.py", "exec"), namespace)
            namespace["custom_headers"]()
        return ui

    def test_desktop_keeps_native_menu(self):
        ui = self.run_headers(DESKTOP)
        ui.add_body_html.assert_not_called()
        ui.run_javascript.assert_not_called()

    def test_tablet_keeps_desktop_behavior(self):
        self.run_headers(TABLET).add_body_html.assert_not_called()

    def test_missing_ua_keeps_native_menu(self):
        self.run_headers("").add_body_html.assert_not_called()

    def test_missing_request_keeps_native_menu(self):
        self.run_headers(None).add_body_html.assert_not_called()

    def test_iphone_retains_long_press_suppression(self):
        ui = self.run_headers(PHONE)
        ui.add_body_html.assert_called_once_with(f"<script>{javascript.PREVENT_CONTEXT_MENU}</script>")

    def test_android_retains_long_press_suppression(self):
        ui = self.run_headers(ANDROID)
        ui.add_body_html.assert_called_once_with(f"<script>{javascript.PREVENT_CONTEXT_MENU}</script>")


if __name__ == "__main__":
    unittest.main(verbosity=2)
