"""Local-only interactive prototype. Fixture data, no auth or live database."""
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime
from nicegui import ui
import beaverhabits.main
import importlib
page = importlib.import_module('beaverhabits.frontend.security_page')
from beaverhabits.frontend.css import FONT_CSS
from beaverhabits.frontend.layout import custom_headers

async def load(user):
    return ([SimpleNamespace(id=b'fixture',name='Personal laptop',created_at=datetime(2026,9,17))]
            if user.email.startswith('populated') else [])
page._load_credentials = load

@ui.page('/')
async def render(populated: bool=False, dark: bool=False):
    ui.add_css(FONT_CSS)
    ui.dark_mode(dark)
    with patch.object(page, 'layout', lambda **kw: ui.column().classes('w-full items-center')), patch.object(page, 'custom_headers', lambda: None):
        await page.security_page(SimpleNamespace(email='populated@example.com' if populated else 'fixture@example.com', token_version=0))

@ui.page('/auth-card')
async def auth_card_probe():
    """Mirror of the login page's auth_card construct (P3-9 geometry probe)."""
    from beaverhabits.frontend.components import auth_card
    custom_headers()  # same BH_DESIGN_CSS chain the login page loads
    async def noop():
        pass
    with auth_card(title='Sign in', func=noop, logo=True, on_back=None, show_continue=True) as slot:
        ui.label('fixture body')

ui.run(host='127.0.0.1', port=18765, reload=False, show=False)
