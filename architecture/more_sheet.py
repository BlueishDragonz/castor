"""Sectioned bottom sheet for overflow items.

Single instance lives at module level so opening ⋮ from any page reuses the
same dialog (no stacking). All actions close the sheet first, then navigate.

Mobile sheet ordering:
  Account:  Security (badge)
            Help
  Data:     Import
            Export
  Session:  Log out  (red)

Reorder lives in the top-right of the mobile app bar (Task 2), not here.
"""
import asyncio

from nicegui import ui

from beaverhabits.frontend.layout import redirect, show_help_dialog
from beaverhabits.app.auth import user_logout


def _security_badge() -> str | None:
    """Return a small badge string ('1 passkey') or None. Task 5 adds the
    helper this depends on; until then we return None gracefully."""
    try:
        from beaverhabits.frontend.security_page import _count_credentials

        n = _count_credentials()  # may be sync or async - see Task 5
        if asyncio.iscoroutine(n):
            # Not awaited here; UI is sync. Return None to avoid blocking.
            return None
        return f"{n} passkey{'s' if n != 1 else ''}" if n else None
    except Exception:
        return None


def _logout():
    user_logout()
    ui.navigate.to("/login")


def _section_heading(text: str, *, danger: bool = False):
    klass = "text-red-700 dark:text-red-400" if danger else "text-gray-500"
    ui.label(text.upper()).classes(
        f"text-[11px] font-semibold tracking-wider px-4 pt-4 pb-1 {klass}"
    )


def _row(
    label: str,
    icon: str,
    on_click,
    badge: str | None = None,
    *,
    danger: bool = False,
):
    label_klass = "text-red-700 dark:text-red-400" if danger else ""
    with ui.button(on_click=lambda: (_close(), on_click())).props(
        "flat align=left no-caps dense"
    ).classes(f"w-full justify-start text-left rounded-none {label_klass}"):
        with ui.row().classes("w-full items-center gap-3 py-3 px-4"):
            with ui.element("div").classes(
                "w-7 h-7 grid place-items-center text-gray-500 dark:text-gray-400"
            ):
                ui.icon(icon).classes("text-[22px]")
            ui.label(label).classes("text-[15px]")
            if badge:
                ui.space()
                ui.label(badge).classes(
                    "text-[11px] text-gray-500 dark:text-gray-400"
                )
            else:
                ui.space()
                ui.icon("chevron_right").classes(
                    "text-[18px] text-gray-400 dark:text-gray-500"
                )


# Single sheet instance; reset on close.
_sheet: ui.dialog | None = None


def _close():
    global _sheet
    if _sheet is not None:
        _sheet.close()


def open_more_sheet():
    """Entry point called by the ⋮ bottom-nav button."""
    global _sheet
    with ui.dialog().props('backdrop-filter="blur(2px)"') as dlg:
        with (
            ui.card()
            .classes(
                "w-full max-w-[480px] mx-auto rounded-t-2xl rounded-b-none p-0"
            )
            .style("position: fixed; bottom: 0; left: 0; right: 0;")
        ):
            ui.element("div").classes(
                "w-9 h-1 bg-gray-300 dark:bg-gray-700 rounded mx-auto my-1.5"
            )
            ui.label("More").classes("text-[13px] font-semibold px-4 pb-2")

            _section_heading("Account")
            _row("Security", "shield", lambda: redirect("security"), _security_badge())
            _row("Help", "help", show_help_dialog)

            ui.element("div").classes("h-px bg-gray-200 dark:bg-gray-800 my-1.5")

            _section_heading("Data")
            _row("Import", "upload", lambda: redirect("import"))
            _row("Export", "download", lambda: redirect("export"))

            ui.element("div").classes("h-px bg-gray-200 dark:bg-gray-800 my-1.5")

            _section_heading("Session", danger=True)
            _row("Log out", "logout", _logout, danger=True)
    dlg.open()
    _sheet = dlg
