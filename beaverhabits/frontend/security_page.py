"""Security summaries and focused dialogs, with page-local state.

Fresh password verification gates both password changes and credential deletion.
Registration is successful only after the server accepts it; password changes
revoke sessions through UserManager and leave a signed-out outcome on screen.
"""
import json

from nicegui import ui

from beaverhabits import views
from beaverhabits.app.auth import (
    get_async_session_context,
    get_user_db_context,
    get_user_manager_context,
    user_authenticate,
    user_logout,
)
from beaverhabits.app.db import User
from beaverhabits.frontend.components import compat_card
from beaverhabits.frontend.layout import custom_headers, layout
from beaverhabits.frontend.security_passkeys import (
    add_security_passkeys_javascript as add_security_passkey_javascript,
)

# Mount-time hints; password managers may still override these at user discretion.
EMPTY_PASSWORD_PROPS = 'autocomplete=new-password data-1p-ignore data-bwignore data-lpignore=true data-dashlane-ignore=true'
EMPTY_NICKNAME_PROPS = 'autocomplete=off name=passkey-nickname data-1p-ignore data-bwignore data-lpignore=true data-dashlane-ignore=true'

SECURITY_CSS = """
.bh-security { width:100%; max-width:350px; gap:24px; }
.bh-security .bh-security-card { border-radius:18px; padding:22px; gap:18px; }
.bh-security .bh-security-copy, .bh-security-dialog .bh-security-copy {
    font-size:13px; line-height:1.65; opacity:.72;
}
.bh-security .bh-security-wrap, .bh-security-dialog .bh-security-wrap {
    min-width:0; overflow-wrap:anywhere; white-space:normal;
}
.bh-security .bh-security-emblem, .bh-security-dialog .bh-security-emblem {
    width:52px; height:52px; border-radius:16px; display:flex;
    align-items:center; justify-content:center; background:rgba(25,118,210,.09);
    color:#1976d2; font-size:28px;
}
.bh-security-dialog .bh-security-panel {
    width:400px; max-width:calc(100vw - 32px); padding:24px;
    border-radius:20px; gap:18px;
}
.bh-security-dialog .bh-security-stage {
    width:100%; gap:16px; animation:bh-security-enter 200ms ease-out;
}
.bh-security-dialog .bh-security-success { color:#16814b; background:rgba(22,129,75,.1); }
.bh-security-dialog .bh-security-error { color:var(--q-negative); font-size:13px; line-height:1.6; }
.bh-security .q-btn, .bh-security-dialog .q-btn { border-radius:10px; }
@keyframes bh-security-enter { from {opacity:0; transform:translateY(5px)} to {opacity:1; transform:translateY(0)} }
@media (prefers-reduced-motion: reduce) {
    .bh-security-dialog, .bh-security-dialog *, .bh-security-dialog *::before,
    .bh-security-dialog *::after, .bh-security * {
        animation:none !important; transition:none !important;
        --q-transition-duration:0ms !important;
    }
}
"""


async def _load_credentials(user: User):
    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                return await user_manager.get_webauthn_credentials(user)


async def security_page(user: User):
    custom_headers()
    add_security_passkey_javascript()
    ui.add_css(SECURITY_CSS)
    state = {
        'active': None, 'pending': False, 'signed_out': False,
        'success': False, 'credential': None, 'credentials': [],
        'list_error': '', 'recovery_pending': False,
    }
    refs = {'dialog': None, 'fields': [], 'opener': None, 'focus': None}

    def heading(text):
        return ui.label(text).classes('text-lg font-semibold bh-security-wrap').props('role=heading aria-level=2')

    def message(text='', *, error=False):
        return ui.label(text).classes(
            'w-full bh-security-wrap bh-security-error' if error else 'w-full bh-security-wrap bh-security-copy'
        ).props('role=alert aria-live=assertive' if error else 'role=status aria-live=polite')

    def erase_fields():
        for field in refs['fields']:
            if not field.is_deleted:
                field.value = ''

    def focus_element(element):
        if isinstance(element, ui.button):
            # QBtn has no exposed focus() method; focus its native button.
            element.client.run_javascript(f'document.getElementById("c{element.id}")?.focus()')
        else:
            element.run_method('focus')

    def focus_dialog():
        if refs['focus'] is not None and not refs['focus'].is_deleted:
            focus_element(refs['focus'])

    def restore_focus():
        opener = refs['opener']
        if not state['signed_out'] and opener is not None and not opener.is_deleted:
            focus_element(opener)

    def closed():
        erase_fields()
        restore_focus()

    def set_pending(pending):
        state['pending'] = pending
        refs['submit'].set_enabled(not pending)
        refs['submit'].props(f'loading={str(pending).lower()}')
        refs['cancel'].set_enabled(not pending or state['active'] == 'passkey')
        for field in refs['fields']:
            field.set_enabled(not pending)
        refs['dialog'].props('persistent' if pending or state['signed_out'] else '',
                             remove='' if pending or state['signed_out'] else 'persistent')

    async def close_dialog():
        if state['signed_out']:
            return
        if state['pending']:
            if state['active'] == 'passkey':
                refs['cancel'].disable()
                refs['status'].text = 'Cancelling… waiting for the registration result.'
                try:
                    await ui.run_javascript('return window.cancelSecurityPasskey()', timeout=10)
                except Exception:
                    refs['status'].text = 'Still waiting. Keep this dialog open until registration finishes.'
            return  # do not hide an in-flight ceremony or lose a late server success
        erase_fields()
        refs['dialog'].close()

    def start_dialog(kind, opener, credential=None):
        if state['signed_out'] or state['pending']:
            return False
        erase_fields()
        state.update(active=kind, success=False, credential=credential)
        refs.update(opener=opener, fields=[], focus=None)
        if refs['dialog'] is None:
            refs['dialog'] = ui.dialog().classes('bh-security-dialog').props(
                'transition-show=fade transition-hide=fade transition-duration=200'
            )
            refs['dialog'].on('show', focus_dialog)
            refs['dialog'].on('hide', closed)
        refs['dialog'].props(remove='persistent')
        refs['dialog'].clear()
        return True

    def dialog_shell(title, description, icon):
        """Create the one dynamic region inside the visible dialog card."""
        with refs['dialog']:
            with compat_card().classes('bh-security-panel'):
                refs['body'] = ui.column().classes('bh-security-stage')
        with refs['body']:
            ui.icon(icon).classes('bh-security-emblem').props('aria-hidden=true')
            title_label = heading(title)
            refs['dialog'].props(f'aria-labelledby=c{title_label.id}')
            ui.label(description).classes('bh-security-copy bh-security-wrap')

    def actions(label, handler):
        refs['error'] = message(error=True)
        refs['status'] = message()
        for field in refs['fields']:
            field.props(f'aria-describedby="c{refs["error"].id} c{refs["status"].id}"')
        with ui.row().classes('w-full justify-end gap-2'):
            refs['cancel'] = ui.button('Cancel', on_click=close_dialog).props('flat no-caps')
            refs['submit'] = ui.button(label, on_click=handler).props('unelevated no-caps')

    def show_success(title, description, *, signed_out=False):
        erase_fields()
        refs['fields'] = []
        refs['body'].clear()
        state['success'] = True
        with refs['body']:
            ui.icon('check').classes('bh-security-emblem bh-security-success').props('aria-hidden=true')
            title_label = heading(title)
            refs['dialog'].props(f'aria-labelledby=c{title_label.id}')
            message(description)
            if state['list_error'] and not signed_out:
                message('Could not refresh your passkey list. Your passkey was saved; close this dialog and reload the list.', error=True)
            refs['focus'] = ui.button(
                'Sign in' if signed_out else 'Done',
                on_click=(lambda: ui.navigate.to('/login')) if signed_out else close_dialog,
            ).classes('w-full').props('unelevated no-caps autofocus')
        focus_dialog()

    def render_credentials():
        refs['credentials'].clear()
        with refs['credentials']:
            if state['list_error']:
                message(state['list_error'], error=True)
                ui.button('Reload list', on_click=reload_credentials).props('flat no-caps')
            elif not state['credentials']:
                ui.icon('fingerprint').classes('bh-security-emblem').props('aria-hidden=true')
                ui.label('No passkeys yet').classes('font-medium')
                ui.label('Sign in with a passkey instead of typing a password. Keep a second passkey as a backup.').classes('bh-security-copy')
            else:
                for cred in state['credentials']:
                    with ui.row().classes('w-full items-center no-wrap gap-3'):
                        ui.icon('fingerprint').classes('text-2xl opacity-60').props('aria-hidden=true')
                        with ui.column().classes('grow gap-1 bh-security-wrap'):
                            ui.label(cred.name or 'Passkey').classes('font-medium bh-security-wrap')
                            added = cred.created_at.strftime('%Y-%m-%d') if getattr(cred, 'created_at', None) else 'unknown date'
                            ui.label(f'Added {added}').classes('text-xs opacity-60')
                        with ui.button(icon='more_horiz').props('flat round dense aria-label="Passkey options"') as opener:
                            with ui.menu():
                                ui.button('Remove passkey', icon='delete_outline',
                                          on_click=lambda c=cred, o=opener: open_delete(c, o)).props('flat no-caps color=negative')

    async def reload_credentials():
        if state['signed_out']:
            return False
        try:
            state['credentials'] = await _load_credentials(user)
            state['list_error'] = ''
        except Exception:
            state['list_error'] = 'Could not load passkeys. Please try again.'
        render_credentials()
        return not state['list_error']

    async def register_passkey():
        if state['pending'] or state['signed_out'] or state['success']:
            return
        nickname = (refs['nickname'].value or '').strip()
        if not nickname:
            refs['error'].text = 'Enter a nickname for this passkey.'
            refs['nickname'].run_method('focus')
            return
        if len(nickname) > 64:
            refs['error'].text = 'Use a nickname of 64 characters or fewer.'
            refs['nickname'].run_method('focus')
            return
        refs['error'].text = ''
        refs['status'].text = 'Follow the instructions from your browser or device…'
        set_pending(True)
        try:
            result = await ui.run_javascript(
                'return await window.registerSecurityPasskey(' + json.dumps(user.email) + ',' + json.dumps(nickname) + ')',
                timeout=180,
            )
        except Exception:
            result = {'status': 'uncertain'}
        status = result.get('status') if isinstance(result, dict) else 'uncertain'
        if status == 'success':
            # Keep the single-flight guard through refresh, too. A slow list
            # fetch must not permit a second registration or dialog replacement.
            state['success'] = True
            refs['cancel'].disable()
            refs['status'].text = 'Passkey saved. Updating your list…'
            await reload_credentials()
            set_pending(False)
            show_success('Passkey added', 'Your passkey is ready to use next time you sign in.')
            return
        set_pending(False)
        refs['status'].text = ''
        if status == 'cancelled':
            refs['error'].text = 'Registration cancelled. You can try again when you are ready.'
        elif status == 'error':
            refs['error'].text = result.get('message') or 'Could not add your passkey. Please try again.'
        else:
            refs['error'].text = 'We could not confirm whether your passkey was saved. Close this dialog and reload the list before trying again.'

    def open_passkey():
        if not start_dialog('passkey', refs['add']):
            return
        dialog_shell('Add a passkey', 'Choose a name you will recognise. Your browser will help you save the passkey.', 'fingerprint')
        with refs['body']:
            refs['nickname'] = ui.input('Passkey nickname').classes('w-full').props(EMPTY_NICKNAME_PROPS)
            refs['nickname'].props('outlined autofocus').on('keydown.enter', register_passkey)
            refs['fields'] = [refs['nickname']]
            refs['focus'] = refs['nickname']
            actions('Register passkey', register_passkey)
        refs['dialog'].open()

    async def change_password():
        if state['pending'] or state['signed_out'] or state['success']:
            return
        current, new, confirm = [(field.value or '') for field in refs['fields']]
        error = ('Enter your current password' if not current else
                 'New password must be at least 12 characters' if len(new) < 12 else
                 'New passwords do not match' if new != confirm else '')
        refs['error'].text = error
        if error:
            return
        set_pending(True)
        refs['status'].text = 'Updating your password…'
        try:
            authed = await user_authenticate(email=user.email, password=current)
            if authed is None or authed.id != user.id:
                refs['error'].text = 'Current password is incorrect'
                return
            from beaverhabits.app.schemas import UserUpdate
            async with get_async_session_context() as session:
                async with get_user_db_context(session) as user_db:
                    async with get_user_manager_context(user_db) as user_manager:
                        await user_manager.update(UserUpdate(password=new), authed, safe=True)
        except Exception:
            refs['error'].text = 'Could not change your password. Please try again, or use password recovery if your session has expired.'
            return
        finally:
            # Do not retain passwords in per-page state, event closures, or UI.
            current = new = confirm = ''
            set_pending(False)
            refs['status'].text = ''
        state['signed_out'] = True
        erase_fields()
        refs['add'].disable()
        refs['change'].disable()
        refs['recovery'].disable()
        # user_logout clears NiceGUI user storage and flags the HttpOnly cookie
        # for removal on the next HTTP response; it does NOT navigate. Existing
        # tokens are already revoked by UserManager. Keep this success visible.
        user_logout()
        refs['dialog'].props('persistent')
        # Legacy WebAuthn callers used localStorage, unlike GUI auth storage.
        ui.context.client.run_javascript("try { localStorage.removeItem('auth_token'); } catch (_) {}")
        show_success('Password changed', 'You are signed out everywhere, including this device. Sign in again with your new password.', signed_out=True)

    def password_input(label, handler):
        field = ui.input(label, password=True, password_toggle_button=True).classes('w-full').props(EMPTY_PASSWORD_PROPS)
        field.props('outlined').on('keydown.enter', handler)
        refs['fields'].append(field)
        return field

    def open_password():
        if not start_dialog('password', refs['change']):
            return
        dialog_shell('Change password', 'Changing your password signs you out everywhere, including this device.', 'lock_outline')
        with refs['body']:
            refs['focus'] = password_input('Current password', change_password).props('autofocus')
            password_input('New password', change_password)
            password_input('Confirm new password', change_password)
            ui.label('Use at least 12 characters.').classes('bh-security-copy')
            actions('Save password', change_password)
        refs['dialog'].open()

    async def remove_passkey():
        if state['pending'] or state['signed_out']:
            return
        password = refs['fields'][0].value or ''
        if not password:
            refs['error'].text = 'Enter your current password'
            return
        set_pending(True)
        refs['error'].text = ''
        try:
            authed = await user_authenticate(email=user.email, password=password)
            if authed is None or authed.id != user.id:
                refs['error'].text = 'Current password is incorrect'
                return
            # Fresh successful authentication PROVES another usable sign-in
            # method remains. Never infer this from a random OAuth password hash.
            cred_id = bytes(state['credential'].id)
            async with get_async_session_context() as session:
                async with get_user_db_context(session) as user_db:
                    async with get_user_manager_context(user_db) as user_manager:
                        creds = await user_manager.get_webauthn_credentials(authed)
                        if not any(bytes(c.id) == cred_id for c in creds):
                            refs['error'].text = 'Passkey not found. Close this dialog and reload the list.'
                            return
                        ok = await user_manager.delete_webauthn_credential(authed, cred_id)
            if not ok:
                refs['error'].text = 'Passkey not found. Close this dialog and reload the list.'
                return
        except Exception:
            refs['error'].text = 'Could not remove your passkey. Please try again.'
            return
        finally:
            password = ''
            set_pending(False)
        erase_fields()
        refs['dialog'].close()
        await reload_credentials()
        ui.notify('Passkey removed', color='positive')

    def open_delete(credential, opener):
        if not start_dialog('delete', opener, credential):
            return
        dialog_shell('Remove passkey', f'Remove “{credential.name or "Passkey"}”? Confirm with your current password. This cannot be undone.', 'delete_outline')
        with refs['body']:
            refs['focus'] = password_input('Current password', remove_passkey).props('autofocus')
            actions('Remove', remove_passkey)
            refs['submit'].props('color=negative')
        refs['dialog'].open()

    def recovery_sign_out():
        erase_fields()
        user_logout()
        ui.context.client.run_javascript("try { localStorage.removeItem('auth_token'); } catch (_) {}")
        ui.navigate.to('/login')

    async def recover_password():
        if not start_dialog('recovery', refs['recovery']):
            return
        dialog_shell('Recover your password',
                     'Continue to sign out on this device and open sign in. Enter your recovery email there and choose Forgot password. No email is sent until you request it.',
                     'mail_outline')
        with refs['body']:
            ui.label(user.email).classes('bh-security-wrap select-text')
            actions('Continue to sign in', recovery_sign_out)
            refs['focus'] = refs['cancel']
        refs['dialog'].open()

    with layout(title='Security'):
        with ui.column().classes('bh-security'):
            with compat_card().classes('w-full bh-security-card'):
                heading('Passkeys')
                refs['credentials'] = ui.column().classes('w-full gap-4')
                refs['add'] = ui.button('Add passkey', icon='add', on_click=open_passkey).classes('w-full').props('unelevated no-caps')
                await reload_credentials()
            with compat_card().classes('w-full bh-security-card'):
                heading('Password')
                ui.label('Manage your password using your current password, or recover access by email.').classes('bh-security-copy')
                with ui.column().classes('w-full gap-1'):
                    ui.label('Recovery email').classes('text-xs font-medium opacity-60')
                    ui.label(user.email).classes('w-full text-sm select-text bh-security-wrap')
                refs['change'] = ui.button('Change password', icon='lock_outline', on_click=open_password).classes('w-full').props('outline no-caps')
                refs['recovery'] = ui.button('Forgot password?', on_click=recover_password).props('flat no-caps dense').classes('self-start')
                refs['recovery_status'] = message(error=True)
