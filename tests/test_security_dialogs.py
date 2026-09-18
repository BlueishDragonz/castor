"""Real NiceGUI element/handler tests; no database, network, or secrets.

Run alone (legacy suite modules install global stubs):
  .venv/bin/python -m unittest tests.test_security_dialogs -v
Browser focus/geometry and real WebAuthn acceptance belong to the integration suite.
"""
import asyncio
import importlib
import inspect
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nicegui import ui
from nicegui.client import Client
from nicegui.page import page as Page
import beaverhabits.main  # preserve the app's circular-import ordering

security = importlib.import_module("beaverhabits.frontend.security_page")
from beaverhabits.app import security_actions


class SecurityDialogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Client(Page('/security-test'))
        self.user = SimpleNamespace(email='fixture+"quoted"@example.com', id='fixture', token_version=7)
        self.load = AsyncMock(return_value=[])
        self.change = AsyncMock(return_value=self.user)
        self.remove = AsyncMock(return_value=True)

        self.js = AsyncMock(return_value={"status": "success"})
        self.logout = MagicMock()
        patches = [
            patch.object(security, '_load_credentials', self.load),
            patch.object(security, 'custom_headers', lambda: None),
            patch.object(security, 'layout', lambda **kwargs: ui.column()),
            patch.object(security, 'add_security_passkey_javascript', lambda: None, create=True),
            patch.object(security_actions, 'change_password', self.change),
            patch.object(security_actions, 'remove_passkey', self.remove),
            patch.object(security, 'user_logout', self.logout, create=True),
            patch.object(ui, 'run_javascript', self.js),
            patch.object(self.client, 'run_javascript', MagicMock()),
            patch.object(ui.navigate, 'to'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.client.delete)

    async def render(self):
        with self.client:
            await security.security_page(self.user)

    def elements(self, cls=None):
        return [e for e in self.client.elements.values()
                if not e.is_deleted and (cls is None or isinstance(e, cls))]

    def named(self, cls, text):
        matches = [e for e in self.elements(cls)
                   if getattr(e, 'text', getattr(e, 'label', None)) == text]
        self.assertTrue(matches, f'Missing {cls.__name__}: {text}')
        return matches[-1]

    async def click(self, text):
        el = self.named(ui.button, text)
        await self.invoke(el, 'click')

    async def invoke(self, el, event):
        listeners = [e for e in el._event_listeners.values() if e.type == event]
        self.assertTrue(listeners, f'No {event} handler')
        with self.client:
            handler = listeners[0].handler
            # NiceGUI's button wrapper schedules async callbacks; invoke its
            # actual callback so the test can await completion deterministically.
            callback = inspect.getclosurevars(handler).nonlocals.get('callback', handler)
            result = callback()
            if inspect.isawaitable(result):
                await result

    def text(self):
        return '\n'.join(str(getattr(e, 'text', '')) for e in self.elements())

    def set_passwords(self, current='current-secret', new='a-new-password-12', confirm=None):
        self.named(ui.input, 'Current password').value = current
        self.named(ui.input, 'New password').value = new
        self.named(ui.input, 'Confirm new password').value = new if confirm is None else confirm

    async def open_password(self):
        await self.render()
        await self.click('Change password')

    async def open_passkey(self, nickname='Laptop'):
        await self.render()
        await self.click('Add passkey')
        self.named(ui.input, 'Passkey nickname').value = nickname

    def assert_inline(self, phrase):
        labels = [e for e in self.elements(ui.label) if phrase in e.text]
        self.assertTrue(labels, phrase)
        self.assertTrue(any(e._props.get('role') in ('alert', 'status') for e in labels))

    async def test_summary_has_two_cards_no_inputs_and_recovery_email(self):
        await self.render()
        self.assertEqual(len(self.elements(ui.input)), 0)
        self.assertEqual(len(self.elements(ui.card)), 2)
        self.assertIn('No passkeys yet', self.text())
        self.assertIn(self.user.email, self.text())
        self.named(ui.button, 'Add passkey')
        self.named(ui.button, 'Change password')
        self.assertNotIn('Password is set', self.text())

    async def test_populated_summary_displays_name_date_and_add(self):
        self.load.return_value = [SimpleNamespace(id=b'one', name='Office key', created_at=datetime(2026, 9, 1), transports=[])]
        await self.render()
        self.assertIn('Office key', self.text())
        self.assertIn('Added 2026-09-01', self.text())
        self.assertNotIn('No passkeys yet', self.text())
        self.named(ui.button, 'Add passkey')
        self.assertEqual(self.elements(ui.input), [])

    async def test_initial_list_failure_is_not_empty_state(self):
        self.load.side_effect = RuntimeError('private backend detail')
        await self.render()
        self.assertIn('Could not load passkeys', self.text())
        self.assertNotIn('No passkeys yet', self.text())
        self.assertNotIn('private backend detail', self.text())
        self.named(ui.button, 'Add passkey')

    async def test_nickname_validation_is_trimmed_and_not_silently_truncated(self):
        await self.open_passkey('  ')
        await self.click('Register passkey')
        self.assert_inline('Enter a nickname')
        self.js.assert_not_awaited()
        self.named(ui.input, 'Passkey nickname').value = 'x' * 65
        await self.click('Register passkey')
        self.assert_inline('64 characters')
        self.js.assert_not_awaited()

    async def test_registration_success_uses_json_and_confirmed_refresh(self):
        nickname = '  Laptop "work" \\ key  '
        await self.open_passkey(nickname)
        await self.click('Register passkey')
        self.js.assert_awaited_once_with(
            'return await window.registerSecurityPasskey(' + json.dumps(self.user.email) + ',' + json.dumps(nickname.strip()) + ')',
            timeout=180,
        )
        self.assertEqual(self.load.await_count, 2)
        self.assertIn('Passkey added', self.text())
        self.named(ui.button, 'Done')
        self.assertEqual(self.elements(ui.input), [])

    async def test_cancelled_registration_keeps_nickname_and_does_not_refresh(self):
        self.js.return_value = {'status': 'cancelled'}
        await self.open_passkey()
        await self.click('Register passkey')
        self.assert_inline('cancelled')
        self.assertEqual(self.named(ui.input, 'Passkey nickname').value, 'Laptop')
        self.assertEqual(self.load.await_count, 1)

    async def test_error_registration_is_inline_and_retryable(self):
        self.js.return_value = {'status': 'error', 'message': 'Use a secure connection.'}
        await self.open_passkey()
        await self.click('Register passkey')
        self.assert_inline('Use a secure connection.')
        self.assertEqual(self.load.await_count, 1)
        self.assertTrue(self.named(ui.button, 'Register passkey').enabled)

    async def test_uncertain_registration_never_claims_success(self):
        self.js.return_value = {'status': 'uncertain'}
        await self.open_passkey()
        await self.click('Register passkey')
        self.assert_inline('could not confirm')
        self.assertNotIn('Passkey added', self.text())
        self.assertEqual(self.load.await_count, 1)
        self.assertFalse(self.named(ui.button, 'Register passkey').enabled)
        await self.click('Register passkey')
        self.assertEqual(self.js.await_count, 1)

    async def test_js_timeout_is_uncertain_without_backend_details(self):
        self.js.side_effect = TimeoutError('private detail')
        await self.open_passkey()
        await self.click('Register passkey')
        self.assert_inline('could not confirm')
        self.assertNotIn('private detail', self.text())
        self.assertEqual(self.load.await_count, 1)

    async def test_confirmed_registration_with_refresh_failure_stays_success(self):
        self.load.side_effect = [[], RuntimeError('private detail')]
        await self.open_passkey()
        await self.click('Register passkey')
        self.assertIn('Passkey added', self.text())
        self.assertIn('Could not refresh', self.text())
        self.named(ui.button, 'Done')

    async def test_register_is_single_flight_with_pending_state(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def ceremony(*args, **kwargs):
            started.set()
            await finish.wait()
            return {'status': 'cancelled'}
        self.js.side_effect = ceremony
        await self.open_passkey()
        task = asyncio.create_task(self.click('Register passkey'))
        await started.wait()
        try:
            self.assertIn('Follow the instructions', self.text())
            self.assertFalse(self.named(ui.button, 'Register passkey').enabled)
            await self.click('Register passkey')
            self.assertEqual(self.js.await_count, 1)
        finally:
            finish.set()
            await task

    async def test_password_fields_lazily_open_empty_and_toggle(self):
        await self.open_password()
        inputs = self.elements(ui.input)
        self.assertEqual(len(inputs), 3)
        for field in inputs:
            self.assertFalse(field.value)
            self.assertEqual(field._props.get('autocomplete'), 'new-password')
        self.assertIn('signs you out everywhere', self.text())

    async def test_password_validation_happens_before_auth(self):
        await self.open_password()
        await self.click('Save password')
        self.assert_inline('Enter your current password')
        self.set_passwords(new='short')
        await self.click('Save password')
        self.assert_inline('at least 12 characters')
        self.set_passwords(confirm='different')
        await self.click('Save password')
        self.assert_inline('do not match')
        self.change.assert_not_awaited()

    async def test_wrong_current_password_keeps_form(self):
        self.change.side_effect = security_actions.AuthorizationError()
        await self.open_password()
        self.set_passwords()
        await self.click('Save password')
        self.assert_inline(security_actions.AuthorizationError.message)
        self.change.assert_awaited_once()
        # P0-1: secrets are erased even when the form stays open for retry.
        self.assertEqual(self.named(ui.input, 'New password').value, '')
        self.logout.assert_not_called()

    async def test_password_error_paths_erase_field_values(self):
        # P0-1: a rejected sensitive action must not leave typed secrets in
        # the visible inputs; only the inline error survives for retry.
        await self.render()
        for error in (security_actions.AuthorizationError(),
                      security_actions.PasswordPolicyError(),
                      security_actions.RateLimitError()):
            with self.subTest(error=type(error).__name__):
                self.change.side_effect = error
                await self.click('Change password')
                self.set_passwords()
                fields = self.elements(ui.input)
                self.assertEqual(len(fields), 3)
                self.assertTrue(all(field.value for field in fields))
                await self.click('Save password')
                self.assert_inline(error.message)
                self.assertTrue(all(not field.value for field in fields),
                                'secret retained in DOM after SecurityActionError')
                self.logout.assert_not_called()
                await self.click('Cancel')  # retryable: form stays closable

    async def test_delete_error_path_erases_field_value(self):
        # P0-1: same invariant for the remove-passkey password field.
        self.load.return_value = [SimpleNamespace(id=b'one', name='Office key', created_at=None, transports=[])]
        await self.render()
        for error in (security_actions.AuthorizationError(),
                      security_actions.CredentialNotFoundError()):
            with self.subTest(error=type(error).__name__):
                self.remove.side_effect = error
                await self.click('Remove passkey')
                field = self.named(ui.input, 'Current password')
                field.value = 'fixture-password'
                await self.click('Remove')
                self.assert_inline(error.message)
                self.assertEqual(field.value, '')
                self.logout.assert_not_called()
                await self.click('Cancel')

    async def test_password_backend_failure_is_uncertain_and_not_replayable(self):
        await self.assert_interrupted('password', RuntimeError('sensitive backend detail'))

    async def assert_interrupted(self, action, error):
        service = self.change if action == 'password' else self.remove
        service.side_effect = error
        if action == 'password':
            await self.open_password()
            self.set_passwords()
            submit = self.named(ui.button, 'Save password')
        else:
            await self.open_delete()
            self.named(ui.input, 'Current password').value = 'fixture-password'
            submit = self.named(ui.button, 'Remove')
        fields = self.elements(ui.input)
        listener = next(e for e in submit._event_listeners.values() if e.type == 'click')
        queued = inspect.getclosurevars(listener.handler).nonlocals.get('callback', listener.handler)
        await self.invoke(submit, 'click')
        self.assertTrue(all(not f.value for f in fields))
        self.assertEqual(self.elements(ui.input), [])
        self.assertNotIn('sensitive backend detail', self.text())
        self.assertNotIn('Password changed', self.text())
        if isinstance(error, security_actions.StaleAuthorizationError):
            self.assert_inline('sign in again')
        else:
            self.assert_inline('could not confirm')
        self.named(ui.button, 'Sign in')
        self.assertFalse(self.named(ui.button, 'Change password').enabled)
        self.assertFalse(self.named(ui.button, 'Add passkey').enabled)
        with self.client:
            await queued()  # even a queued Enter/click cannot replay
        service.assert_awaited_once()
        await self.click('Forgot password?')
        self.assertIn('Recover your password', self.text())
        self.assertIn('cannot access this email', self.text())
        self.named(ui.button, 'Continue to sign in')
        self.assertFalse(self.named(ui.button, 'Cancel').enabled)

    async def test_password_unavailable_is_uncertain(self):
        await self.assert_interrupted('password', security_actions.SecurityActionUnavailable())

    async def test_password_stale_session_erases_fields_and_keeps_recovery(self):
        await self.assert_interrupted('password', security_actions.StaleAuthorizationError())

    async def test_delete_unavailable_is_uncertain(self):
        await self.assert_interrupted('delete', security_actions.SecurityActionUnavailable())

    async def test_delete_unexpected_error_is_uncertain(self):
        await self.assert_interrupted('delete', RuntimeError('sensitive backend detail'))

    async def test_delete_stale_session_erases_fields_and_keeps_recovery(self):
        await self.assert_interrupted('delete', security_actions.StaleAuthorizationError())

    async def test_password_safe_policy_error_keeps_form_retryable(self):
        self.change.side_effect = security_actions.PasswordPolicyError()
        await self.open_password()
        self.set_passwords()
        await self.click('Save password')
        self.assert_inline(security_actions.PasswordPolicyError.message)
        self.assertTrue(self.named(ui.button, 'Save password').enabled)
        self.assertEqual(len(self.elements(ui.input)), 3)
        self.logout.assert_not_called()

    async def test_password_safe_rate_limit_does_not_claim_uncertain_success(self):
        self.change.side_effect = security_actions.RateLimitError()
        await self.open_password()
        self.set_passwords()
        await self.click('Save password')
        self.assert_inline(security_actions.RateLimitError.message)
        self.assertNotIn('could not confirm', self.text())
        self.logout.assert_not_called()

    async def test_page_captures_version_before_later_user_object_changes(self):
        await self.open_password()
        self.user.token_version = 99
        self.set_passwords()
        await self.click('Save password')
        self.change.assert_awaited_once_with(self.user.id, 7, 'current-secret', 'a-new-password-12')

    async def open_delete(self):
        self.load.return_value = [SimpleNamespace(id=b'one', name='Office key', created_at=None)]
        await self.render()
        await self.click('Remove passkey')

    async def test_delete_recovery_erases_password_and_does_not_send_email(self):
        await self.open_delete()
        field = self.named(ui.input, 'Current password')
        field.value = 'fixture-password'
        with patch.object(security.views, 'forgot_password', AsyncMock()) as mail:
            await self.click('Forgot password?')
            self.assertFalse(field.value)
            self.assertIn('establish a password', self.text())
            self.assertIn('cannot access this email', self.text())
            self.assertIn('cannot bypass', self.text())
            self.assertIn(self.user.email, self.text())
            self.logout.assert_not_called()
            mail.assert_not_awaited()
            self.remove.assert_not_awaited()

    async def test_password_success_erases_secrets_logs_out_without_redirect(self):
        await self.open_password()
        self.set_passwords()
        fields = self.elements(ui.input)
        await self.click('Save password')
        self.change.assert_awaited_once()
        self.change.assert_awaited_once_with(self.user.id, 7, 'current-secret', 'a-new-password-12')
        self.logout.assert_called_once_with()
        self.assertTrue(all(not f.value for f in fields))
        self.assertIn('Password changed', self.text())
        self.assertIn('signed out everywhere', self.text())
        ui.navigate.to.assert_not_called()
        await self.click('Sign in')
        ui.navigate.to.assert_called_once_with('/login')
        self.assertFalse(self.named(ui.button, 'Add passkey').enabled)
        self.assertFalse(self.named(ui.button, 'Change password').enabled)

    async def test_cancel_password_erases_fields_and_reopen_is_fresh(self):
        await self.open_password()
        self.set_passwords()
        fields = self.elements(ui.input)
        await self.click('Cancel')
        self.assertTrue(all(not f.value for f in fields))
        await self.click('Change password')
        self.assertTrue(all(not f.value for f in self.elements(ui.input)))
        self.assertEqual(len(self.elements(ui.dialog)), 1)

    async def test_delete_requires_fresh_password_and_never_deletes_on_failure(self):
        self.load.return_value = [SimpleNamespace(id=b'one', name='Office key', created_at=None, transports=[])]
        self.remove.side_effect = security_actions.AuthorizationError()
        await self.render()
        await self.click('Remove passkey')
        await self.click('Remove')
        self.remove.assert_not_awaited()
        self.named(ui.input, 'Current password').value = 'incorrect'
        await self.click('Remove')
        self.remove.assert_awaited_once()
        self.assert_inline(security_actions.AuthorizationError.message)

    async def test_confirmed_registration_cannot_resubmit_during_list_refresh(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def load(user):
            if self.load.await_count > 1:
                started.set()
                await finish.wait()
            return []
        self.load.side_effect = load
        await self.open_passkey()
        task = asyncio.create_task(self.click('Register passkey'))
        await started.wait()
        duplicate = asyncio.create_task(self.click('Register passkey'))
        await asyncio.sleep(0)
        try:
            self.js.assert_awaited_once()
        finally:
            finish.set()
            await asyncio.gather(task, duplicate)

    async def test_password_is_single_flight(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def update(*args, **kwargs):
            started.set()
            await finish.wait()
        self.change.side_effect = update
        await self.open_password()
        self.set_passwords()
        task = asyncio.create_task(self.click('Save password'))
        await started.wait()
        try:
            self.assertFalse(self.named(ui.button, 'Save password').enabled)
            self.assertFalse(self.named(ui.button, 'Cancel').enabled)
            self.assertTrue(self.elements(ui.dialog)[0]._props.get('persistent'))
            await self.click('Save password')
            self.change.assert_awaited_once()
        finally:
            finish.set()
            await task

    async def test_delete_only_owned_credential_after_verified_password(self):
        cred = SimpleNamespace(id=b'one', name='Office key', created_at=None)
        self.load.return_value = [cred]
        await self.render()
        await self.click('Remove passkey')
        self.named(ui.input, 'Current password').value = 'current-secret'
        await self.click('Remove')
        self.remove.assert_awaited_once_with(self.user.id, 7, 'current-secret', b'one')
        self.assertEqual(self.load.await_count, 2)

    async def test_delete_rechecks_ownership(self):
        self.remove.side_effect = security_actions.CredentialNotFoundError()
        self.load.return_value = [SimpleNamespace(id=b'one', name='Office key', created_at=None)]
        await self.render()
        await self.click('Remove passkey')
        self.named(ui.input, 'Current password').value = 'current-secret'
        await self.click('Remove')
        self.remove.assert_awaited_once()
        self.assert_inline('Passkey not found')

    async def test_recovery_opens_existing_login_flow_without_sending_mail(self):
        await self.render()
        self.assertFalse(any(b.text == 'Forgot password?' for b in self.elements(ui.button)))
        await self.click('Change password')
        self.set_passwords()
        original_fields = list(self.elements(ui.input))
        with patch.object(security.ui.navigate, 'to') as navigate, patch.object(security.views, 'forgot_password', AsyncMock()) as recover, patch.object(security, 'user_logout') as logout:
            await self.click('Forgot password?')
            navigate.assert_not_called()
            logout.assert_not_called()
            self.assertTrue(all(not field.value for field in original_fields))
            await self.click('Continue to sign in')
            logout.assert_called_once()
            navigate.assert_called_once_with('/login')
            recover.assert_not_awaited()
        self.assertEqual(self.elements(ui.input), [])

    async def test_dialog_escape_cleans_secrets_and_restores_focus(self):
        await self.open_password()
        self.set_passwords()
        opener = self.named(ui.button, 'Change password')
        self.client.run_javascript.reset_mock()
        await self.invoke(self.elements(ui.dialog)[0], 'hide')
        self.client.run_javascript.assert_called_once_with(
            f'document.getElementById("c{opener.id}")?.focus()'
        )
        self.assertTrue(all(not f.value for f in self.elements(ui.input)))

    async def test_dialog_fields_describe_inline_errors(self):
        await self.open_password()
        for field in self.elements(ui.input):
            self.assertTrue(field._props.get('aria-describedby'))

    async def test_two_page_clients_do_not_share_dialog_state(self):
        await self.open_password()
        self.set_passwords()
        second = Client(Page('/second-security-test'))
        try:
            with second:
                await security.security_page(SimpleNamespace(id='second', email='second@example.com', token_version=0))
            self.assertFalse(any(isinstance(e, ui.input) for e in second.elements.values()))
            self.assertEqual(self.named(ui.input, 'Current password').value, 'current-secret')
        finally:
            second.delete()

    async def test_cancel_pending_registration_waits_for_result(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def ceremony(code, **kwargs):
            if 'registerSecurityPasskey' in code:
                started.set()
                await finish.wait()
                return {'status': 'cancelled'}
            return None
        self.js.side_effect = ceremony
        await self.open_passkey()
        task = asyncio.create_task(self.click('Register passkey'))
        await started.wait()
        try:
            await self.click('Cancel')
            self.assertTrue(self.elements(ui.dialog)[0].value)
            self.assertIn('Cancelling', self.text())
            self.assertEqual(self.js.await_count, 2)
        finally:
            finish.set()
            await task
        self.assertEqual(self.load.await_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
