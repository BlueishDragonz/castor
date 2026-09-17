"""Autofill configuration and actual dialog element regression tests."""
import unittest
from beaverhabits.frontend.security_page import EMPTY_PASSWORD_PROPS, EMPTY_NICKNAME_PROPS
from tests.test_security_dialogs import SecurityDialogTests

class AutofillSuppressionTests(unittest.TestCase):
    def test_password_hints(self):
        self.assertIn('autocomplete=new-password', EMPTY_PASSWORD_PROPS)
        self.assertIn('data-1p-ignore', EMPTY_PASSWORD_PROPS)
    def test_nickname_hints(self):
        self.assertIn('autocomplete=off', EMPTY_NICKNAME_PROPS)
        self.assertIn('name=passkey-nickname', EMPTY_NICKNAME_PROPS)

class DialogAutofillTests(SecurityDialogTests):
    async def test_actual_dialog_inputs_have_native_hints(self):
        from nicegui import ui
        await self.render()
        await self.click('Change password')
        fields=self.elements(ui.input)
        self.assertEqual(len(fields),3)
        for field in fields:
            self.assertEqual(field._props.get('autocomplete'),'new-password')
            self.assertIn('data-1p-ignore',field._props)
        await self.click('Cancel')
        await self.click('Add passkey')
        self.assertEqual(self.elements(ui.input)[0]._props.get('autocomplete'),'off')

if __name__=='__main__': unittest.main()
