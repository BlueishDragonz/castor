"""Security settings: passkey management + password change.

Rules enforced (see plan 2026-09-12_040000):
- R1 step-up auth (current password) before credential delete / password change.
- R2 anti-stranding: cannot delete the last sign-in method.
- R4 superseded 2026-09-17: password change revokes existing JWTs (token_version);
  the UI tells the user they are signed out everywhere.
"""

from nicegui import ui

from beaverhabits import views
from beaverhabits.app.auth import (
    get_async_session_context,
    get_user_db_context,
    get_user_manager_context,
    user_authenticate,
)
from beaverhabits.app.db import User
from beaverhabits.frontend.components import add_webauthn_javascript, compat_card
from beaverhabits.frontend.layout import custom_headers, layout

# Explicit attributes apply at mount, including lazily created dialogs.
# Password managers may override these hints at the user's discretion.
EMPTY_PASSWORD_PROPS = 'autocomplete=new-password data-1p-ignore data-bwignore data-lpignore=true data-dashlane-ignore=true'
EMPTY_NICKNAME_PROPS = 'autocomplete=off name=passkey-nickname data-1p-ignore data-bwignore data-lpignore=true data-dashlane-ignore=true'


def _transport_labels(transports) -> str:
    labels = []
    for t in transports or []:
        labels.append(getattr(t, "value", t))
    return ", ".join(labels) or "—"


async def _load_credentials(user: User):
    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                return await user_manager.get_webauthn_credentials(user)


async def security_page(user: User):
    custom_headers()
    add_webauthn_javascript()

    async def do_delete(cred_id: bytes, password: str, dialog) -> None:
        authed = await user_authenticate(email=user.email, password=password)
        if authed is None:
            ui.notify("Current password is incorrect", color="negative")
            return
        async with get_async_session_context() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    creds = await user_manager.get_webauthn_credentials(user)
                    remaining = [c for c in creds if bytes(c.id) != cred_id]
                    if not remaining and not getattr(user, "hashed_password", None):
                        ui.notify(
                            "Cannot remove your last sign-in method: "
                            "set a password first",
                            color="negative",
                        )
                        return
                    ok = await user_manager.delete_webauthn_credential(
                        user, cred_id
                    )
        dialog.close()
        if ok:
            ui.notify("Passkey removed", color="positive")
            render_credentials.refresh()
        else:
            ui.notify("Passkey not found", color="negative")

    def confirm_delete(cred) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(f'Remove "{cred.name or "Passkey"}"?').classes("font-bold")
            ui.label(
                "Confirm with your current password. This cannot be undone."
            ).classes("text-sm text-gray-500")
            pw = ui.input("Current password", password=True).classes(
                "w-full"
            ).props(EMPTY_PASSWORD_PROPS)
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Remove",
                    on_click=lambda: do_delete(bytes(cred.id), pw.value or "", dialog),
                ).props("color=negative")
        dialog.open()

    @ui.refreshable
    async def render_credentials() -> None:
        creds = await _load_credentials(user)
        if not creds:
            ui.label("No passkeys registered yet.").classes("text-gray-500")
            return
        for cred in creds:
            with ui.row().classes("w-full items-center"):
                ui.icon("fingerprint").classes("text-2xl text-gray-500")
                with ui.column().classes("gap-0"):
                    ui.label(cred.name or "Passkey").classes("font-bold")
                    added = cred.created_at.strftime("%Y-%m-%d") if getattr(
                        cred, "created_at", None
                    ) else "unknown date"
                    ui.label(
                        f"Added {added} · {_transport_labels(cred.transports)}"
                    ).classes("text-xs text-gray-500")
                ui.space()
                ui.button(
                    icon="delete", on_click=lambda c=cred: confirm_delete(c)
                ).props("flat round dense").classes("text-gray-500")

    async def do_password_change(current: str, new: str, confirm: str) -> None:
        if not current:
            ui.notify("Enter your current password", color="negative")
            return
        if not new or len(new) < 12:
            ui.notify("New password must be at least 12 characters", color="negative")
            return
        if new != confirm:
            ui.notify("New passwords do not match", color="negative")
            return
        authed = await user_authenticate(email=user.email, password=current)
        if authed is None:
            ui.notify("Current password is incorrect", color="negative")
            return
        from beaverhabits.app.schemas import UserUpdate

        async with get_async_session_context() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    await user_manager.update(
                        UserUpdate(password=new), user, safe=True
                    )
        ui.notify("Password changed", color="positive")

    def start_add_key() -> None:
        nickname = (nickname_input.value or "").replace("\\", "").replace(
            '"', ""
        ).strip()[:64]
        ui.run_javascript(
            f'registerWebAuthnCredential("{user.email}", "{nickname}", true)'
        )

    with layout(title="Security"):
        # Match the app's content standard (~350px: home table, habit cards).
        with ui.column().classes("w-full gap-6").style("max-width:350px"):
            ui.label(
                "Nothing to steal, nothing to forget. But also nothing we can "
                "restore — keep a second passkey registered."
            ).classes("text-sm text-gray-500")

            with compat_card().classes("w-full"):
                with ui.column().classes("w-full gap-3"):
                    ui.label("Passkeys").classes("text-lg font-bold")
                    await render_credentials()
                    ui.separator()
                    ui.label("Add another passkey").classes("font-bold")
                    nickname_input = ui.input(
                        "Passkey nickname (e.g. Laptop, Phone)"
                    ).classes("w-full").props(EMPTY_NICKNAME_PROPS)
                    ui.button(
                        "Add passkey", icon="fingerprint", on_click=start_add_key
                    ).classes("w-full")
                    # Hidden bridge: JS clicks this after a successful ceremony.
                    ui.button(
                        on_click=render_credentials.refresh
                    ).props("id=bh-pk-refresh hidden")

            with compat_card().classes("w-full"):
                with ui.column().classes("w-full gap-3"):
                    ui.label("Password").classes("text-lg font-bold")
                    current_pw = ui.input("Current password", password=True).classes(
                        "w-full"
                    ).props(EMPTY_PASSWORD_PROPS)
                    new_pw = ui.input("New password", password=True).classes("w-full").props(EMPTY_PASSWORD_PROPS)
                    confirm_pw = ui.input(
                        "Confirm new password", password=True
                    ).classes("w-full").props(EMPTY_PASSWORD_PROPS)
                    ui.button(
                        "Change password",
                        on_click=lambda: do_password_change(
                            current_pw.value or "",
                            new_pw.value or "",
                            confirm_pw.value or "",
                        ),
                    ).classes("w-full")
                    ui.label(
                        "Note: changing your password signs you out everywhere, "
                        "including this device — sign in again afterwards."
                    ).classes("text-xs text-gray-500")
