"""The real WebAuthn ceremony, end to end, through the HTTP API.

Why this file exists
--------------------
The pre-existing passkey tests seeded credential rows directly and then
exercised the *surrounding* endpoints. Their own docstring admitted the
gap: "This file does NOT test the full cryptographic ceremony
(begin+complete round-trip)."

That left the security-critical server-side code — the part that decides
whether a passkey is real — with no coverage at all. Nothing in the suite
ever reached `webauthn.verify_registration_response` or
`webauthn.verify_authentication_response` with genuine cryptographic
material. A regression that disabled signature verification, ignored the
expected origin, or skipped the challenge check would have passed every
existing test.

These tests drive a software authenticator (tests/software_authenticator.py)
that produces real ES256 attestations and assertions, and are verified by
the same py-webauthn library the application uses. The server does all the
cryptography; the test only plays the role of the browser and the
authenticator.

Run:
  python -m pytest tests/test_passkey_ceremony.py -v
"""
import base64
import json
import os
import sys
import tempfile
import unittest

# The ceremony needs a specific relying party, but `Settings` re-reads the
# process environment on every construction, and several other test
# modules construct Settings() expecting a clean slate. Snapshot the
# variables this module sets and restore them at module teardown, so
# collection order cannot make an unrelated module's config assertions
# depend on whether this module ran first.
_ENV_OVERRIDES = {
    'DATABASE_URL',
    'JWT_SECRET',
    'RESET_PASSWORD_TOKEN_SECRET',
    'NICEGUI_STORAGE_SECRET',
    'REQUIRE_ADMIN_FOR_REGISTRATION',
    'TRUSTED_LOCAL_EMAIL',
    'TRUSTED_EMAIL_HEADER',
    'AUTH_RATE_USER_PER_MINUTE',
    'AUTH_RATE_IP_PER_MINUTE',
    'WEBAUTHN_RATE_USER_PER_MINUTE',
    'PUBLIC_URL',
    'WEBAUTHN_RP_ID',
    'WEBAUTHN_ORIGIN',
}
_ENV_SNAPSHOT = {k: os.environ.get(k) for k in _ENV_OVERRIDES}

_TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_TMP.name}/ceremony.db'
os.environ['JWT_SECRET'] = 'test-only-jwt-secret-not-for-production'
os.environ['RESET_PASSWORD_TOKEN_SECRET'] = 'test-only-reset-secret'
os.environ['NICEGUI_STORAGE_SECRET'] = 'test-only-storage-secret'
os.environ['REQUIRE_ADMIN_FOR_REGISTRATION'] = 'false'
os.environ['TRUSTED_LOCAL_EMAIL'] = ''
os.environ['TRUSTED_EMAIL_HEADER'] = ''
os.environ['AUTH_RATE_USER_PER_MINUTE'] = '10000'
os.environ['AUTH_RATE_IP_PER_MINUTE'] = '10000'
os.environ['WEBAUTHN_RATE_USER_PER_MINUTE'] = '10000'

# The relying party must be pinned explicitly, not derived: the ceremony
# compares the RP ID and origin the server sends against what the
# authenticator claims, and a test must assert on known values rather
# than on whatever the ambient environment happens to declare.
os.environ['PUBLIC_URL'] = 'http://localhost:4321'
os.environ['WEBAUTHN_RP_ID'] = 'localhost'
os.environ['WEBAUTHN_ORIGIN'] = 'http://localhost:4321'

import httpx
from fastapi import FastAPI
from fastapi_users.db import SQLAlchemyUserDatabase

import castor.main  # noqa: F401  - ensures the package initialises
from castor import views
from castor.app import db, reset_routes
from castor.app.users import UserManager
from castor.app.schemas import UserCreate
from castor.app.app import init_auth_routes
from castor.routes.api import init_api_routes
from castor.configs import settings

# The helper lives beside this file; tests/ is not a package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from software_authenticator import SoftwareAuthenticator  # noqa: E402

# These tests build a bare FastAPI app rather than booting castor.main, so
# the startup resolver that derives the relying-party config never runs.
# Derive it here, exactly as the application does at boot, so the test
# exercises the same code path a real deployment takes.
#
# `settings` is a module-level singleton shared with every other test
# module, and resolve_webauthn_settings() MUTATES it. Without the save and
# restore below, the ceremony RP config leaks into later modules and six
# unrelated origin/cookie tests fail depending on collection order.
_SAVED_WEAUTHN = (
    settings.WEBAUTHN_RP_ID,
    settings.WEBAUTHN_ORIGIN,
)


def _restore_weauthn_settings() -> None:
    settings.WEBAUTHN_RP_ID, settings.WEBAUTHN_ORIGIN = _SAVED_WEAUTHN


# `settings` is a singleton built when castor.configs is first imported, and
# configs.py calls dotenv.load_dotenv(), which OVERRIDES os.environ with the
# repo's .env files. Re-assert the relying party directly on the instance so
# this module's ceremony config wins regardless of what .env contains.
# (relying-party values are assigned just below, once RP_ID/ORIGIN exist)


def tearDownModule() -> None:
    _restore_weauthn_settings()
    for _k, _v in _ENV_SNAPSHOT.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

app = FastAPI()
init_auth_routes(app)
init_api_routes(app)
app.include_router(reset_routes.router)

PASSWORD = 'correct horse battery staple-correct horse'
EMAIL = 'ceremony@example.com'

RP_ID = 'localhost'
ORIGIN = 'http://localhost:4321'

settings.PUBLIC_URL = ORIGIN
settings.WEBAUTHN_RP_ID = RP_ID
settings.WEBAUTHN_ORIGIN = ORIGIN


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


class PasskeyCeremonyTests(unittest.IsolatedAsyncioTestCase):
    """Registration and sign-in verified with real cryptography."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()

        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(
            UserCreate(email=EMAIL, password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(
            self.user, views.dummy_empty_habit_list()
        )
        self.session = session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        )
        self.authn = SoftwareAuthenticator(RP_ID, ORIGIN)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    # ── helpers ─────────────────────────────────────────────────────────
    async def _token(self) -> str:
        r = await self.client.post(
            '/auth/login', data={'username': EMAIL, 'password': PASSWORD}
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    def _headers(self, token: str) -> dict:
        return {
            'Authorization': f'Bearer {token}',
            'Origin': ORIGIN,
        }

    async def _register(self, authn=None, username: str = EMAIL, name: str = 'Test Key'):
        """Run a full registration ceremony. Returns the complete response."""
        authn = authn or self.authn
        token = await self._token()
        h = self._headers(token)

        begin = await self.client.post(
            '/auth/webauthn/register/begin', json={'username': username}, headers=h
        )
        self.assertEqual(begin.status_code, 200, begin.text)
        options = begin.json()['publicKey']

        credential = authn.register(options)
        complete = await self.client.post(
            '/auth/webauthn/register/complete',
            json={
                'id': credential['id'],
                'rawId': credential['rawId'],
                'type': credential['type'],
                'response': credential['response'],
                'name': name,
                'username': username,
            },
            headers=h,
        )
        return complete, credential, token

    async def _credentials(self, token: str):
        r = await self.client.get(
            '/auth/webauthn/credentials', headers=self._headers(token)
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ── the ceremony actually works ─────────────────────────────────────
    async def test_full_registration_ceremony_succeeds(self):
        """A genuine ES256 attestation must be accepted and stored.

        This is the assertion the whole suite was missing: the server
        verifies the client-data challenge, the RP ID hash, the origin and
        the attestation structure before persisting a credential.
        """
        complete, credential, token = await self._register()
        self.assertEqual(complete.status_code, 200, complete.text)

        creds = await self._credentials(token)
        self.assertEqual(len(creds), 1, creds)
        stored = creds[0]
        self.assertEqual(stored['name'], 'Test Key')
        # The credential ID the server stored is the one we generated.
        self.assertEqual(_unb64u(stored['id']), self.authn.credential_id)

    async def test_stored_public_key_is_the_authenticators_key(self):
        """The server must persist the key from the attestation, not a
        placeholder — otherwise the first sign-in could never verify."""
        from castor.app.db import WebAuthnCredential
        from sqlalchemy import select

        await self._register()
        async with db.async_session_maker() as s:
            row = (
                await s.execute(
                    select(WebAuthnCredential).where(
                        WebAuthnCredential.id == self.authn.credential_id
                    )
                )
            ).scalar_one()
        self.assertTrue(row.public_key)
        self.assertNotEqual(
            bytes(row.public_key), b'', 'an empty public key would break sign-in'
        )
        # COSE-encoded ES256 key, not raw SEC1.
        self.assertEqual(bytes(row.public_key)[:1], b'\xa5')

    async def test_sign_count_is_persisted_from_the_attestation(self):
        """Authenticator metadata must round-trip: the counter the
        authenticator reported has to be the counter the server stores,
        or clone detection is broken from the first sign-in."""
        await self._register()
        async with db.async_session_maker() as s:
            from sqlalchemy import select

            from castor.app.db import WebAuthnCredential

            row = (
                await s.execute(
                    select(WebAuthnCredential).where(
                        WebAuthnCredential.id == self.authn.credential_id
                    )
                )
            ).scalar_one()
        self.assertEqual(row.sign_count, 1)

    async def test_full_sign_in_ceremony_succeeds(self):
        """The assertion half of the lifecycle, with a real signature."""
        await self._register()
        token = await self._token()

        begin = await self.client.post(
            '/auth/webauthn/login/begin',
            json={'username': EMAIL},
            headers=self._headers(token),
        )
        self.assertEqual(begin.status_code, 200, begin.text)
        options = begin.json()['publicKey']

        assertion = self.authn.authenticate(options['challenge'])
        complete = await self.client.post(
            '/auth/webauthn/login/complete',
            json={
                'id': assertion['id'],
                'rawId': assertion['rawId'],
                'type': assertion['type'],
                'response': assertion['response'],
                'username': EMAIL,
            },
            headers=self._headers(token),
        )
        self.assertEqual(complete.status_code, 200, complete.text)

    async def test_sign_in_advances_the_signature_counter(self):
        """A successful assertion must persist the new counter, so a
        replayed earlier assertion is detectable."""
        await self._register()
        token = await self._token()

        async def sign_in_once():
            begin = await self.client.post(
                '/auth/webauthn/login/begin',
                json={'username': EMAIL},
                headers=self._headers(token),
            )
            options = begin.json()['publicKey']
            assertion = self.authn.authenticate(options['challenge'])
            return await self.client.post(
                '/auth/webauthn/login/complete',
                json={
                    'id': assertion['id'],
                    'rawId': assertion['rawId'],
                    'type': assertion['type'],
                    'response': assertion['response'],
                    'username': EMAIL,
                },
                headers=self._headers(token),
            )

        r = await sign_in_once()
        self.assertEqual(r.status_code, 200, r.text)
        async with db.async_session_maker() as s:
            from sqlalchemy import select

            from castor.app.db import WebAuthnCredential

            row = (
                await s.execute(
                    select(WebAuthnCredential).where(
                        WebAuthnCredential.id == self.authn.credential_id
                    )
                )
            ).scalar_one()
        self.assertGreater(row.sign_count, 1, 'counter did not advance')

    # ── negative paths: verification must actually reject ───────────────
    async def test_registration_from_the_wrong_origin_is_rejected(self):
        """The origin assertion is a phishing defence. An authenticator
        claiming a different origin must not be able to enrol.

        This is the check most likely to be silently weakened, so it is
        pinned explicitly.
        """
        token = await self._token()
        h = self._headers(token)
        begin = await self.client.post(
            '/auth/webauthn/register/begin', json={'username': EMAIL}, headers=h
        )
        options = begin.json()['publicKey']

        # Same RP ID, but the client data claims an attacker's origin.
        evil = SoftwareAuthenticator(RP_ID, 'https://evil.example')
        evil.credential_id = self.authn.credential_id
        evil._key = self.authn._key
        credential = evil.register(options)

        complete = await self.client.post(
            '/auth/webauthn/register/complete',
            json={
                'id': credential['id'],
                'rawId': credential['rawId'],
                'type': credential['type'],
                'response': credential['response'],
                'name': 'Evil Key',
                'username': EMAIL,
            },
            headers=h,
        )
        self.assertEqual(
            complete.status_code, 400, f"evil origin was accepted: {complete.text}"
        )
        self.assertIn('verification failed', complete.json()['detail'].lower())

    async def test_registration_with_a_foreign_rp_id_is_rejected(self):
        """The RP ID hash inside the authenticator data must match the
        configured relying party."""
        token = await self._token()
        h = self._headers(token)
        begin = await self.client.post(
            '/auth/webauthn/register/begin', json={'username': EMAIL}, headers=h
        )
        options = begin.json()['publicKey']

        foreign = SoftwareAuthenticator('attacker.example', ORIGIN)
        credential = foreign.register(options)
        complete = await self.client.post(
            '/auth/webauthn/register/complete',
            json={
                'id': credential['id'],
                'rawId': credential['rawId'],
                'type': credential['type'],
                'response': credential['response'],
                'name': 'Foreign Key',
                'username': EMAIL,
            },
            headers=h,
        )
        self.assertEqual(complete.status_code, 400, complete.text)

    async def test_registration_with_a_tampered_challenge_is_rejected(self):
        """A challenge the server never issued must not be accepted."""
        token = await self._token()
        h = self._headers(token)
        begin = await self.client.post(
            '/auth/webauthn/register/begin', json={'username': EMAIL}, headers=h
        )
        options = begin.json()['publicKey']

        forged = dict(options)
        forged['challenge'] = _b64u(b'this-challenge-was-never-issued-32byte')
        credential = self.authn.register(forged)
        complete = await self.client.post(
            '/auth/webauthn/register/complete',
            json={
                'id': credential['id'],
                'rawId': credential['rawId'],
                'type': credential['type'],
                'response': credential['response'],
                'name': 'Forged Key',
                'username': EMAIL,
            },
            headers=h,
        )
        self.assertEqual(complete.status_code, 400, complete.text)
        self.assertEqual(await self._credentials(token), [])

    async def test_sign_in_with_a_tampered_assertion_is_rejected(self):
        """Flipping a byte of the authenticator data must invalidate the
        signature. If this passed, the signature was never checked."""
        await self._register()
        token = await self._token()
        begin = await self.client.post(
            '/auth/webauthn/login/begin',
            json={'username': EMAIL},
            headers=self._headers(token),
        )
        options = begin.json()['publicKey']
        assertion = self.authn.authenticate(options['challenge'])

        raw = bytearray(_unb64u(assertion['response']['authenticatorData']))
        raw[-1] ^= 0xFF  # corrupt the signature counter
        assertion['response']['authenticatorData'] = _b64u(bytes(raw))

        complete = await self.client.post(
            '/auth/webauthn/login/complete',
            json={
                'id': assertion['id'],
                'rawId': assertion['rawId'],
                'type': assertion['type'],
                'response': assertion['response'],
                'username': EMAIL,
            },
            headers=self._headers(token),
        )
        self.assertEqual(complete.status_code, 400, complete.text)

    async def test_sign_in_with_an_unregistered_credential_is_rejected(self):
        """A valid assertion from a key the server never stored must not
        establish a session."""
        await self._register()
        token = await self._token()
        stranger = SoftwareAuthenticator(RP_ID, ORIGIN)

        begin = await self.client.post(
            '/auth/webauthn/login/begin',
            json={'username': EMAIL},
            headers=self._headers(token),
        )
        options = begin.json()['publicKey']
        assertion = stranger.authenticate(options['challenge'])
        complete = await self.client.post(
            '/auth/webauthn/login/complete',
            json={
                'id': assertion['id'],
                'rawId': assertion['rawId'],
                'type': assertion['type'],
                'response': assertion['response'],
                'username': EMAIL,
            },
            headers=self._headers(token),
        )
        self.assertIn(complete.status_code, (400, 401, 404), complete.text)

    # ── challenge hygiene ───────────────────────────────────────────────
    async def test_registration_challenge_is_single_use(self):
        """A challenge must not be replayable. Registering twice with the
        same challenge has to fail the second time."""
        token = await self._token()
        h = self._headers(token)
        begin = await self.client.post(
            '/auth/webauthn/register/begin', json={'username': EMAIL}, headers=h
        )
        options = begin.json()['publicKey']

        first = self.authn.register(options)
        r1 = await self.client.post(
            '/auth/webauthn/register/complete',
            json={
                'id': first['id'],
                'rawId': first['rawId'],
                'type': first['type'],
                'response': first['response'],
                'name': 'Key One',
                'username': EMAIL,
            },
            headers=h,
        )
        self.assertEqual(r1.status_code, 200, r1.text)

        # Replay the identical challenge with a fresh attestation.
        second_authn = SoftwareAuthenticator(RP_ID, ORIGIN)
        second = second_authn.register(options)
        r2 = await self.client.post(
            '/auth/webauthn/register/complete',
            json={
                'id': second['id'],
                'rawId': second['rawId'],
                'type': second['type'],
                'response': second['response'],
                'name': 'Key Two',
                'username': EMAIL,
            },
            headers=h,
        )
        self.assertEqual(
            r2.status_code, 400, f"challenge replay was accepted: {r2.text}"
        )

    async def test_registration_challenges_are_unpredictable_and_unique(self):
        """Two challenges must never collide, or a replayed challenge
        would be indistinguishable from a fresh one."""
        token = await self._token()
        h = self._headers(token)
        seen = set()
        for _ in range(5):
            r = await self.client.post(
                '/auth/webauthn/register/begin',
                json={'username': EMAIL},
                headers=h,
            )
            self.assertEqual(r.status_code, 200, r.text)
            ch = r.json()['publicKey']['challenge']
            self.assertNotIn(ch, seen, 'challenge repeated')
            # At least 128 bits of entropy.
            self.assertGreaterEqual(len(_unb64u(ch)), 16)
            seen.add(ch)
        self.assertEqual(len(seen), 5)

    # ── account binding ─────────────────────────────────────────────────
    async def test_registration_cannot_target_another_account(self):
        """Registering a passkey must be bound to the signed-in account.
        A self-binding mismatch has to be refused."""
        from castor.app.schemas import UserCreate as UC

        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        other = await manager.create(
            UC(email='victim@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(
            other, views.dummy_empty_habit_list()
        )
        await session.close()

        token = await self._token()
        h = self._headers(token)
        begin = await self.client.post(
            '/auth/webauthn/register/begin', json={'username': EMAIL}, headers=h
        )
        options = begin.json()['publicKey']
        credential = self.authn.register(options)

        complete = await self.client.post(
            '/auth/webauthn/register/complete',
            json={
                'id': credential['id'],
                'rawId': credential['rawId'],
                'type': credential['type'],
                'response': credential['response'],
                'name': 'Cross Account',
                # Claim a different account than the one authenticated.
                'username': 'victim@example.com',
            },
            headers=h,
        )
        self.assertIn(
            complete.status_code, (400, 403), f"cross-account bind: {complete.text}"
        )

    # ── credential management after a real registration ─────────────────
    async def test_removing_the_only_passkey_requires_the_password(self):
        """A passkey is a second factor. Deleting one must require fresh
        account-password proof, so a stolen session alone cannot strip a
        user's second factor."""
        await self._register()
        token = await self._token()
        cid = _b64u(self.authn.credential_id)

        # A bodyless DELETE is a 422 from the request model, not a 401 —
        # either way the credential must survive. Assert on the outcome
        # that matters rather than the exact validation code.
        no_proof = await self.client.delete(
            f'/auth/webauthn/credentials/{cid}', headers=self._headers(token)
        )
        self.assertIn(no_proof.status_code, (401, 422), no_proof.text)
        self.assertEqual(len(await self._credentials(token)), 1)

        # httpx's convenience .delete() takes no body, and this endpoint
        # requires one, so use the generic request method.
        wrong = await self.client.request(
            'DELETE',
            f'/auth/webauthn/credentials/{cid}',
            content=json.dumps({'current_password': 'not-the-password'}),
            headers={**self._headers(token), 'Content-Type': 'application/json'},
        )
        self.assertEqual(wrong.status_code, 401, wrong.text)
        self.assertEqual(len(await self._credentials(token)), 1)

        ok = await self.client.request(
            'DELETE',
            f'/auth/webauthn/credentials/{cid}',
            content=json.dumps({'current_password': PASSWORD}),
            headers={**self._headers(token), 'Content-Type': 'application/json'},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(await self._credentials(token), [])


if __name__ == '__main__':
    unittest.main()
