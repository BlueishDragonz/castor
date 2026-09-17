"""Rendered real Security page: recovery address and summary-only inputs."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import importlib
import beaverhabits.main
from nicegui import ui

page = importlib.import_module('beaverhabits.frontend.security_page')

class RecoveryCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_card_shows_recovery_recipient_without_inputs(self):
        fixture = SimpleNamespace(email='recovery-fixture@example.invalid', token_version=0)
        with ui.column() as root:
            with patch.object(page, '_load_credentials', AsyncMock(return_value=[])), patch.object(page, 'layout', lambda **kwargs: ui.column()), patch.object(page, 'custom_headers', lambda: None):
                await page.security_page(fixture)
        def walk(element):
            yield element
            for slot in element.slots.values():
                for child in slot.children:
                    yield from walk(child)
        elements = list(walk(root))
        labels = [getattr(e, 'text', '') for e in elements]
        self.assertIn('Recovery email', labels)
        self.assertIn(fixture.email, labels)
        # Inputs in closed dialogs are allowed; the summary cards contain none.
        def visible_tree(element):
            if isinstance(element, ui.dialog):
                return
            yield element
            for slot in element.slots.values():
                for child in slot.children:
                    yield from visible_tree(child)
        self.assertFalse(any(isinstance(e, ui.input) for e in visible_tree(root)))
        root.delete()

if __name__ == '__main__': unittest.main()
