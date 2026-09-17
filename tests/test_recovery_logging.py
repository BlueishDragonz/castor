"""Recovery secrets must never reach logs; disposable DB and captured mail."""
import unittest
from unittest.mock import patch
from tests import test_legacy_recovery_safety as fixture
from beaverhabits import views


class RecoveryLoggingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LegacyRecoverySafetyTests.asyncSetUp
    asyncTearDown = fixture.LegacyRecoverySafetyTests.asyncTearDown

    async def test_legacy_recovery_does_not_log_token(self):
        sentinel = 'synthetic-reset-secret-never-log'
        with patch.object(views, 'user_create_reset_token', return_value=sentinel):
            await views.forgot_password(self.user.email)
        self.assertEqual(len(self.mail), 1, 'Test must exercise mail delivery')
        self.assertIn(sentinel, self.mail[0][0][1])
        self.assertNotIn(sentinel, str(self.logs), 'Recovery secret was logged')
        self.assertNotIn(sentinel, str(self.notices))

    async def test_smtp_exception_never_logs_token_or_recipient(self):
        sentinel = 'synthetic-reset-secret-never-log'
        with patch.object(views, 'user_create_reset_token', return_value=sentinel), \
             patch.object(views, 'send_email', side_effect=RuntimeError(sentinel + self.user.email)):
            await views.forgot_password(self.user.email)
        self.assertEqual(self.logs, [(('Recovery request could not be completed',), {})])
        self.assertNotIn(sentinel, str(self.notices))
        self.assertNotIn(self.user.email, str(self.notices))


if __name__ == '__main__':
    unittest.main()
