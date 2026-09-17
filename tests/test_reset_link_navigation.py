"""Invalid reset navigation is safe and never a server error."""
import unittest
from unittest.mock import AsyncMock, patch
import beaverhabits.main
from beaverhabits.app import dependencies
from fastapi import HTTPException
from fastapi_users import exceptions
from starlette.requests import Request

class ResetNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_links_return_safe_login_redirect(self):
        for failure in (exceptions.InvalidResetPasswordToken, exceptions.UserInactive, exceptions.UserNotExists):
            with self.subTest(failure=failure.__name__), patch.object(dependencies, 'user_from_reset_token', AsyncMock(side_effect=failure)):
                request = Request({'type':'http','headers':[], 'query_string':b'token=fixture'})
                with self.assertRaises(HTTPException) as caught:
                    await dependencies.get_reset_user(request)
                self.assertEqual(caught.exception.status_code, 303)
                self.assertEqual(caught.exception.headers['Location'], '/login?recovery=expired')

if __name__ == '__main__':
    unittest.main()
