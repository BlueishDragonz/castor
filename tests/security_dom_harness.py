"""Local-only rendering harness: no auth, DB or real credentials."""
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from nicegui import ui
import beaverhabits.main
import importlib
page = importlib.import_module('beaverhabits.frontend.security_page')

@ui.page('/')
async def render():
    with patch.object(page, '_load_credentials', AsyncMock(return_value=[])), patch.object(page, 'layout', lambda **kw: ui.column()), patch.object(page, 'custom_headers', lambda: None):
        await page.security_page(SimpleNamespace(email='fixture@example.com'))
ui.run(host='127.0.0.1', port=18765, reload=False, show=False)
