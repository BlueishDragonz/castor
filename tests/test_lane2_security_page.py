"""Fast configuration tests; browser checks in check_security_dom.py."""
import unittest
from pathlib import Path
from beaverhabits.frontend.security_page import EMPTY_PASSWORD_PROPS, EMPTY_NICKNAME_PROPS

class AutofillSuppressionTests(unittest.TestCase):
    def test_password_hints(self):
        self.assertIn('autocomplete=new-password', EMPTY_PASSWORD_PROPS)
        self.assertIn('data-1p-ignore', EMPTY_PASSWORD_PROPS)

    def test_nickname_hints(self):
        self.assertIn('autocomplete=off', EMPTY_NICKNAME_PROPS)
        self.assertIn('name=passkey-nickname', EMPTY_NICKNAME_PROPS)

    def test_all_fields_use_hints(self):
        source=Path('beaverhabits/frontend/security_page.py').read_text()
        self.assertEqual(source.count('.props(EMPTY_PASSWORD_PROPS)'),4)
        self.assertEqual(source.count('.props(EMPTY_NICKNAME_PROPS)'),1)

    def test_password_guidance(self):
        source=Path('beaverhabits/frontend/security_page.py').read_text()
        self.assertIn('len(new) < 12',source)
        self.assertNotIn('does not sign out other',source)

if __name__=='__main__': unittest.main()
