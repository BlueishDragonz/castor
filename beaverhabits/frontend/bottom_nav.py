"""Mobile bottom navigation: Home / Stats / More.

Renders below the page content and stays sticky at the viewport bottom.
The More button opens the sectioned More sheet (see more_sheet.py).
"""
from nicegui import ui

from beaverhabits.frontend.layout import redirect, show_help_dialog


def bottom_nav():
    """Build the 3-item sticky bottom nav. Caller gates on is_mobile()."""
    with ui.element("div").classes(
        "fixed bottom-0 left-0 right-0 z-50 "
        "bg-white dark:bg-[#121212] "
        "border-t border-gray-200 dark:border-gray-800"
    ).style("padding-bottom: env(safe-area-inset-bottom, 0)"):
        with ui.row().classes("w-full justify-around items-center py-1"):
            _nav_btn("home", "Home", lambda: redirect("/gui"))
            _nav_btn("bar_chart", "Stats", lambda: redirect("stats"))
            _more_btn()  # stub: ui.notify("More coming soon") until Task 4 lands


def _nav_btn(icon: str, label: str, on_click):
    btn = ui.button(on_click=on_click).props(
        "flat round dense no-caps"
    ).classes("text-gray-600 dark:text-gray-300")
    with btn:
        with ui.element("div").classes("flex flex-col items-center gap-0 py-1 px-3"):
            ui.icon(icon).classes("text-[26px]")
            ui.label(label).classes("text-[11px] font-medium tracking-wide")
    return btn


def _more_btn():
    """The ellipsis: stub until Task 4 (more_sheet.py) lands."""
    def _open():
        try:
            from beaverhabits.frontend.more_sheet import open_more_sheet
            open_more_sheet()
        except ImportError:
            # More sheet is built in Task 4. Until then, surface a quiet notice.
            ui.notify("More sheet coming soon (Task 4)", type="info")
    btn = ui.button(on_click=_open).props("flat round dense no-caps").classes(
        "text-gray-600 dark:text-gray-300"
    )
    with btn:
        with ui.element("div").classes("flex flex-col items-center gap-0 py-1 px-3"):
            ui.icon("more_horiz").classes("text-[26px]")
            ui.label("More").classes("text-[11px] font-medium tracking-wide")
    return btn
