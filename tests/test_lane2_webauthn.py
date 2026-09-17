"""Defensive WebAuthn tests; isolated SQLite only, verifiers are mocked."""
import asyncio
import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "local-test-only-not-a-real-secret")

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from beaverhabits.app import challenges
from beaverhabits.app import webauthn_routes as routes
from beaverhabits.app.db import Base, User, WebAuthnCredential
from beaverhabits.app.users import UserManager
from fastapi_users.db import SQLAlchemyUserDatabase


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def completion(challenge, username="owner@example.test", ceremony="webauthn.create"):
    # Only clientDataJSON needed to select a challenge. No synthetic attestation.
    return {"username": username, "id": encoded(b"credential"), "rawId": encoded(b"credential"),
            "type": "public-key", "response": {"clientDataJSON": encoded(json.dumps(
                {"type": ceremony, "challenge": challenge, "origin": "https://example.test"}
            ).encode())}}


class WebAuthnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine("sqlite+aiosqlite:///" + self.tmp.name + "/test.db")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.user = User(id=uuid4(), email="owner@example.test", hashed_password="unused", is_active=True,
                         is_verified=True, is_superuser=False, token_version=0)
        async with self.sessions() as session:
            session.add(self.user)
            session.add(WebAuthnCredential(id=b"credential", user_id=self.user.id, public_key=b"mock-key",
                                          sign_count=1, transports=[]))
            await session.commit()
        self.store_patch = patch.object(challenges, "async_session_maker", self.sessions)
        self.store_patch.start()
        from beaverhabits.app import db
        self.audit_patch = patch.object(db, "async_session_maker", self.sessions)
        self.audit_patch.start()
        self.auth_patch = patch.object(routes, "user_from_token", AsyncMock(side_effect=lambda token: self.user if token == "session-token" else None))
        self.auth = self.auth_patch.start()
        self.lookup_patch = patch.object(routes, "user_get_by_email", AsyncMock(return_value=self.user))
        self.lookup_patch.start()
        app = FastAPI()
        app.include_router(routes.router)
        async def manager():
            async with self.sessions() as session:
                yield UserManager(SQLAlchemyUserDatabase(session, User))
        app.dependency_overrides[routes.get_user_manager] = manager
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="https://example.test")
        self.client.cookies.set("beaver_auth", "session-token")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.store_patch.stop()
        self.audit_patch.stop()
        self.auth_patch.stop()
        self.lookup_patch.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def begin(self, ceremony="register", client=None):
        response = await (client or self.client).post("/auth/webauthn/" + ceremony + "/begin", json={"username": self.user.email})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["publicKey"]["challenge"]

    async def test_registration_requires_session_on_both_endpoints(self):
        self.client.cookies.clear()
        for path, body in [("begin", {"username": self.user.email}), ("complete", completion(encoded(b"x" * 32)))]:
            for headers in [{}, {"Authorization": "Bearer permanent-api-token"}]:
                response = await self.client.post("/auth/webauthn/register/" + path, json=body, headers=headers)
                self.assertEqual(response.status_code, 401)

    async def test_registration_rejects_other_account_on_both_endpoints(self):
        for path, body in [("begin", {"username": "other@example.test"}), ("complete", completion(encoded(b"x" * 32), "other@example.test"))]:
            response = await self.client.post("/auth/webauthn/register/" + path, json=body)
            self.assertEqual(response.status_code, 403)

    async def test_bearer_session_can_register(self):
        self.client.cookies.clear()
        response = await self.client.post("/auth/webauthn/register/begin", json={"username": self.user.email}, headers={"Authorization": "Bearer session-token"})
        self.assertEqual(response.status_code, 200)

    async def test_two_tabs_independent_and_invalid_verification_burns_only_one(self):
        first, second = await self.begin(), await self.begin()
        self.assertNotEqual(first, second)
        with patch.object(routes.webauthn, "verify_registration_response", side_effect=ValueError("ordinary verification failure")) as verifier:
            for challenge in (second, second, first):
                response = await self.client.post("/auth/webauthn/register/complete", json=completion(challenge))
                self.assertEqual(response.status_code, 400)
            self.assertEqual(verifier.call_count, 2)

    async def test_browser_binding_rejects_without_consuming(self):
        challenge = await self.begin()
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="https://example.test", cookies={"beaver_auth": "session-token"}) as other:
            with patch.object(routes.webauthn, "verify_registration_response", side_effect=ValueError("ordinary failure")) as verifier:
                response = await other.post("/auth/webauthn/register/complete", json=completion(challenge))
                self.assertEqual(response.status_code, 400)
                verifier.assert_not_called()
                response = await self.client.post("/auth/webauthn/register/complete", json=completion(challenge))
                self.assertEqual(response.status_code, 400)
                verifier.assert_called_once()

    async def test_expiry_rejected_before_verification(self):
        with patch.object(challenges.time, "time", return_value=100):
            challenge = await self.begin()
        with patch.object(challenges.time, "time", return_value=10000), patch.object(routes.webauthn, "verify_registration_response") as verifier:
            response = await self.client.post("/auth/webauthn/register/complete", json=completion(challenge))
            self.assertEqual(response.status_code, 400)
            verifier.assert_not_called()

    async def test_bounded_client_data(self):
        for value in (None, 42, "!", "a" * 20000, encoded(b"[]"), encoded(b'{"challenge": []}')):
            body = completion(encoded(b"x" * 32))
            body["response"]["clientDataJSON"] = value
            response = await self.client.post("/auth/webauthn/register/complete", json=body)
            self.assertEqual(response.status_code, 400)

    async def test_store_persistent_atomic_single_use_and_binding(self):
        store = challenges.ChallengeStore()
        challenge = b"x" * 32
        await store.issue(challenge, "register", self.user.id, "browser", 60)
        for ceremony, user, browser in [("login", self.user.id, "browser"), ("register", uuid4(), "browser"), ("register", self.user.id, "other")]:
            self.assertFalse(await challenges.ChallengeStore().consume(challenge, ceremony, user, browser))
        results = await asyncio.gather(*[challenges.ChallengeStore().consume(challenge, "register", self.user.id, "browser") for _ in range(6)])
        self.assertEqual(sum(results), 1)

    async def test_login_persists_sign_count_in_real_session(self):
        challenge = await self.begin("login")
        with patch.object(routes.webauthn, "verify_authentication_response", return_value=SimpleNamespace(new_sign_count=2)):
            response = await self.client.post("/auth/webauthn/login/complete", json=completion(challenge, ceremony="webauthn.get"))
            self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as session:
            credential = await session.get(WebAuthnCredential, b"credential")
            self.assertEqual(credential.sign_count, 2)
        response = await self.client.post("/auth/webauthn/login/complete", json=completion(challenge, ceremony="webauthn.get"))
        self.assertEqual(response.status_code, 400)

    async def test_registration_success_stores_only_authenticated_owner(self):
        challenge = await self.begin()
        verification = SimpleNamespace(credential_id=b"second-key", credential_public_key=b"mock-key",
            sign_count=0, aaguid=None, credential_device_type=routes.CredentialDeviceType.MULTI_DEVICE,
            credential_backed_up=True)
        with patch.object(routes.webauthn, "verify_registration_response", return_value=verification) as verifier:
            response = await self.client.post("/auth/webauthn/register/complete", json=completion(challenge))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("beaver_auth=", response.headers["set-cookie"])
            self.assertEqual(verifier.call_args.kwargs["expected_challenge"], routes.base64url_to_bytes(challenge))
        async with self.sessions() as session:
            credential = await session.get(WebAuthnCredential, b"second-key")
            self.assertEqual(credential.user_id, self.user.id)

    async def test_browser_cookie_attributes_and_reuse(self):
        response = await self.client.post("/auth/webauthn/register/begin", json={"username": self.user.email})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=/;", cookie)
        response = await self.client.post("/auth/webauthn/register/begin", json={"username": self.user.email})
        self.assertNotIn("set-cookie", response.headers)

    async def test_challenge_survives_new_engine(self):
        challenge = b"y" * 32
        await challenges.ChallengeStore().issue(challenge, "login", self.user.id, "browser", 60)
        other_engine = create_async_engine("sqlite+aiosqlite:///" + self.tmp.name + "/test.db")
        try:
            with patch.object(challenges, "async_session_maker", async_sessionmaker(other_engine)):
                self.assertTrue(await challenges.ChallengeStore().consume(challenge, "login", self.user.id, "browser"))
        finally:
            await other_engine.dispose()

    async def test_session_validation_uses_versioned_jwt_not_api_token(self):
        from beaverhabits.app import auth
        from beaverhabits.app.users import get_jwt_strategy
        from contextlib import asynccontextmanager
        token = await get_jwt_strategy().write_token(self.user)
        self.client.cookies.clear()
        self.auth_patch.stop()
        with patch.object(auth, "get_async_session_context", asynccontextmanager(self._auth_session)):
            response = await self.client.post("/auth/webauthn/register/begin", json={"username": self.user.email}, headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 200, response.text)
            from sqlalchemy import update
            async with self.sessions() as session:
                await session.execute(update(User).where(User.id == self.user.id).values(token_version=1))
                await session.commit()
            response = await self.client.post("/auth/webauthn/register/begin", json={"username": self.user.email}, headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 401)
        self.auth_patch.start()

    async def _auth_session(self):
        async with self.sessions() as session:
            yield session

    async def test_inactive_login_rejected(self):
        self.user.is_active = False
        response = await self.client.post("/auth/webauthn/login/begin", json={"username": self.user.email})
        self.assertNotEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
