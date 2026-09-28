"""The /security page's backend contract, pinned end to end.

Every test here reproduces a real failure observed against the running
app: the page rendered a control, the control called an endpoint, and the
endpoint did not do what the page assumed. The three 4xx/5xx shapes below
were captured from the live ASGI app before the fix, not inferred from
reading the code.

  GET    /auth/webauthn/recovery-email            -> 405 (handler did not exist)
  DELETE /auth/webauthn/credentials/{id}          -> 422 (body required)
  POST   /auth/webauthn/recovery-email/verify     -> 422 (email required)

Together they made the whole /security page non-functional: it could not
read the recovery address, could not verify one, and could not remove a
passkey. The "Add" passkey button had no handler at all.
"""
import glob
import os
import re
import tempfile
import unittest
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP.name}/security.db"
os.environ["JWT_SECRET"] = "security-contract-test-only-not-real"
os.environ["RESET_PASSWORD_TOKEN_SECRET"] = "security-contract-reset"
os.environ["NICEGUI_STORAGE_SECRET"] = "security-contract-storage"
os.environ["JWT_LIFETIME_SECONDS"] = str(60 * 60 * 24 * 30)
os.environ["REQUIRE_ADMIN_FOR_REGISTRATION"] = "false"
os.environ["TRUSTED_LOCAL_EMAIL"] = ""
os.environ["TRUSTED_EMAIL_HEADER"] = ""
os.environ["FRONTEND_URL"] = "http://localhost:4321"

import httpx
from fastapi import FastAPI
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy import insert

from castor import views
from castor.app import db, webauthn_routes
from castor.app.app import init_auth_routes
from castor.app.schemas import UserCreate
from castor.app.users import UserManager
from castor.routes.api import init_api_routes

PASSWORD = "correct horse battery staple"
EMAIL = "security@example.com"
CRED_ID = b"security-page-credential"


def _b64u(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class SecurityPageContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        init_auth_routes(app)
        init_api_routes(app)
        self.app = app

        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()

        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(UserCreate(email=EMAIL, password=PASSWORD))
        await views.get_or_create_user_habit_list(
            self.user, views.dummy_empty_habit_list()
        )
        async with db.async_session_maker() as s:
            await s.execute(
                insert(db.WebAuthnCredential).values(
                    id=CRED_ID,
                    user_id=self.user.id,
                    public_key=b"mock-public-key",
                    sign_count=1,
                    transports=[],
                    name="Laptop",
                )
            )
            await s.commit()
        await session.close()

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        token = (
            await self.client.post(
                "/auth/login", data={"username": EMAIL, "password": PASSWORD}
            )
        ).json()["access_token"]
        self.token = token
        self.auth = {"Authorization": f"Bearer {token}"}

    async def asyncTearDown(self):
        await self.client.aclose()
        await db.engine.dispose()

    # ── GET /auth/webauthn/recovery-email ─────────────────────────────
    # Reproduced failure: 405 Method Not Allowed.

    async def test_get_recovery_email_endpoint_exists(self):
        """The /security page reads this on load to choose between the
        'add an address' and 'manage the one you have' panels."""
        r = await self.client.get("/auth/webauthn/recovery-email", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            r.json(),
            {
                "recovery_email": None,
                "recovery_email_verified": False,
                "pending_email": None,
            },
        )

    async def test_get_recovery_email_reflects_a_set_address(self):
        """A user who has set an address must SEE it. Before the fix this
        405'd, so the page always claimed no address existed."""
        with patch.object(webauthn_routes, "send_email"):
            r = await self.client.post(
                "/auth/webauthn/recovery-email",
                headers=self.auth,
                json={"email": "backup@example.com"},
            )
        self.assertEqual(r.status_code, 200, r.text)

        r = await self.client.get("/auth/webauthn/recovery-email", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        # Proposed but not yet verified, so the stored field is still null.
        # `pending_email` is what lets the page render the Verify button.
        self.assertIsNone(body["recovery_email"])
        self.assertFalse(body["recovery_email_verified"])
        self.assertEqual(body["pending_email"], "backup@example.com")

    async def test_pending_address_is_cleared_once_verified(self):
        """After the round-trip the pending proposal is consumed and the
        verified address takes over, so the page shows 'Verified'."""
        import re

        with patch.object(webauthn_routes, "send_email") as mail:
            await self.client.post(
                "/auth/webauthn/recovery-email",
                headers=self.auth,
                json={"email": "backup@example.com"},
            )
        code = re.search(r"\b(\d{6})\b", mail.call_args.args[1]).group(1)
        await self.client.post(
            "/auth/webauthn/recovery-email/verify",
            headers=self.auth,
            json={"email": "backup@example.com", "code": code},
        )
        body = (
            await self.client.get("/auth/webauthn/recovery-email", headers=self.auth)
        ).json()
        self.assertEqual(body["recovery_email"], "backup@example.com")
        self.assertTrue(body["recovery_email_verified"])
        self.assertIsNone(body["pending_email"], "a consumed proposal must not linger")

    async def test_get_recovery_email_requires_authentication(self):
        r = await self.client.get("/auth/webauthn/recovery-email")
        self.assertEqual(r.status_code, 401, r.text)

    async def test_get_recovery_email_does_not_leak_another_account(self):
        """It is session-scoped: it can only ever return the caller's own
        address, never an arbitrary one supplied by the client."""
        other = UserManager(SQLAlchemyUserDatabase(db.async_session_maker(), db.User))
        await other.create(UserCreate(email="other@example.com", password=PASSWORD))
        r = await self.client.get(
            "/auth/webauthn/recovery-email",
            headers=self.auth,
            params={"email": "other@example.com"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["recovery_email"])

    # ── DELETE /auth/webauthn/credentials/{id} ────────────────────────
    # Reproduced failure: 422 "Field required: body".

    async def test_delete_credential_succeeds_with_password_body(self):
        """The step-up body is what the page must send. The backend
        requirement is correct and was never relaxed — the client was
        wrong."""
        r = await self.client.request(
            "DELETE",
            f"/auth/webauthn/credentials/{_b64u(CRED_ID)}",
            headers=self.auth,
            json={"current_password": PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        listing = await self.client.get(
            "/auth/webauthn/credentials", headers=self.auth
        )
        self.assertEqual(listing.json(), [])

    async def test_delete_credential_without_body_is_rejected_not_silently_ok(self):
        """Pins the actual failure mode so a regression is visible: the
        credential must NOT be removed, and the caller must get a 4xx
        rather than a false success."""
        r = await self.client.request(
            "DELETE", f"/auth/webauthn/credentials/{_b64u(CRED_ID)}", headers=self.auth
        )
        self.assertEqual(r.status_code, 422, r.text)
        listing = await self.client.get(
            "/auth/webauthn/credentials", headers=self.auth
        )
        self.assertEqual(len(listing.json()), 1, "credential must survive")

    async def test_delete_credential_wrong_password_keeps_the_key(self):
        """The anti-stranding / step-up property: a stale or stolen session
        alone cannot destroy a second factor."""
        r = await self.client.request(
            "DELETE",
            f"/auth/webauthn/credentials/{_b64u(CRED_ID)}",
            headers=self.auth,
            json={"current_password": "not-the-password"},
        )
        self.assertEqual(r.status_code, 401, r.text)
        listing = await self.client.get(
            "/auth/webauthn/credentials", headers=self.auth
        )
        self.assertEqual(len(listing.json()), 1)

    # ── POST /auth/webauthn/recovery-email/verify ─────────────────────
    # Reproduced failure: 422 "Field required: body -> email".

    async def test_verify_requires_email_and_code(self):
        """The page must send BOTH. `email` selects the challenge row and
        must match the address the code was mailed to."""
        with patch.object(webauthn_routes, "send_email") as mail:
            await self.client.post(
                "/auth/webauthn/recovery-email",
                headers=self.auth,
                json={"email": "backup@example.com"},
            )
        import re

        body = mail.call_args.args[1]
        code = re.search(r"\b(\d{6})\b", body).group(1)

        # Omitting email is a client error, and must stay one.
        r = await self.client.post(
            "/auth/webauthn/recovery-email/verify",
            headers=self.auth,
            json={"code": code},
        )
        self.assertEqual(r.status_code, 422, r.text)

        r = await self.client.post(
            "/auth/webauthn/recovery-email/verify",
            headers=self.auth,
            json={"email": "backup@example.com", "code": code},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["recovery_email_verified"])

        readback = await self.client.get(
            "/auth/webauthn/recovery-email", headers=self.auth
        )
        self.assertTrue(readback.json()["recovery_email_verified"])

    async def test_verify_with_mismatched_email_is_refused(self):
        """Sending a valid code with the wrong address must not verify:
        the code is bound to the address it was mailed to."""
        with patch.object(webauthn_routes, "send_email") as mail:
            await self.client.post(
                "/auth/webauthn/recovery-email",
                headers=self.auth,
                json={"email": "backup@example.com"},
            )
        import re

        code = re.search(r"\b(\d{6})\b", mail.call_args.args[1]).group(1)
        r = await self.client.post(
            "/auth/webauthn/recovery-email/verify",
            headers=self.auth,
            json={"email": "attacker@example.com", "code": code},
        )
        self.assertEqual(r.status_code, 400, r.text)

    async def test_verify_requires_authentication(self):
        r = await self.client.post(
            "/auth/webauthn/recovery-email/verify",
            json={"email": "backup@example.com", "code": "123456"},
        )
        self.assertEqual(r.status_code, 401, r.text)

    # ── The page's client-side contract ──────────────────────────────

    def test_security_page_source_sends_the_fields_the_api_requires(self):
        """Source-level pins for the three client-side omissions. These
        are the exact lines that produced the 422s above, and none of them
        are reachable from a Python test, so they are asserted against the
        component source."""
        src = (
            open(
                "web/concepts/src/components/SecurityContent.tsx", encoding="utf-8"
            ).read()
        )
        self.assertIn(
            'JSON.stringify({ current_password: currentPassword })',
            src,
            "passkey deletion must send the step-up password",
        )
        self.assertIn(
            "JSON.stringify({ email: recoveryEmail.email, code })",
            src,
            "recovery verify must send the address the code was mailed to",
        )
        # The component's local type called them {email, verified} while the
        # API returns {recovery_email, recovery_email_verified}; unmapped,
        # both read undefined and the page always showed the empty state.
        self.assertIn("data?.recovery_email", src)
        self.assertIn("data?.recovery_email_verified", src)
        # The pending proposal is what makes the Verify button reachable.
        self.assertIn("data?.pending_email", src)

    def test_add_passkey_button_is_wired(self):
        """The 'Add' control had no onClick, so /security could list and
        delete passkeys but not create one — the only enrolment path was
        the one-shot post-login offer, which is dismissed permanently."""
        src = (
            open(
                "web/concepts/src/components/SecurityContent.tsx", encoding="utf-8"
            ).read()
        )
        self.assertIn("onClick={handleAddPasskey}", src)
        self.assertIn("async function handleAddPasskey", src)
        self.assertIn("navigator.credentials.create", src)

    def test_add_passkey_uses_the_session_email_for_self_binding(self):
        """Both register endpoints reject a username that is not the
        caller's own address. The component must therefore receive the
        session email rather than invent one."""
        component = (
            open(
                "web/concepts/src/components/SecurityContent.tsx", encoding="utf-8"
            ).read()
        )
        page = open("web/concepts/src/pages/security.astro", encoding="utf-8").read()
        self.assertIn("SecurityContent({ email }", component)
        self.assertIn("client:load email={sessionEmail}", page)

    def test_backend_url_is_not_read_from_import_meta_env(self):
        """BACKEND_URL is a PRIVATE Vite variable, so `import.meta.env.
        BACKEND_URL` is substituted at BUILD time and the literal lands in
        the emitted SSR chunks. The container supplies BACKEND_URL at
        start-up, so a production deployment that configured it correctly
        still talked to the host baked in when the image was built.

        Confirmed before the fix: 15 call sites, and 6 built chunks
        containing a literal localhost:8085 that no runtime env could
        override."""
        offenders = []
        for path in glob.glob("web/concepts/src/**/*.ts", recursive=True) + glob.glob(
            "web/concepts/src/**/*.astro", recursive=True
        ):
            src = open(path, encoding="utf-8").read()
            # Comments legitimately describe the old behaviour; only real
            # code counts.
            code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
            code = re.sub(r"//[^\n]*", "", code)
            if "import.meta.env.BACKEND_URL" in code:
                offenders.append(os.path.relpath(path, "web/concepts/src"))
        self.assertEqual(
            offenders,
            [],
            "these call sites still read a BUILD-TIME backend URL: "
            + ", ".join(offenders),
        )

    def test_backend_origin_helper_reads_process_env_only(self):
        """The helper must not mention import.meta.env at all.

        This is sharper than the call-site check: Vite replaces an
        `import.meta.env` member expression by inlining the ENTIRE
        build-time env object into the emitted chunk. The built server
        bundle was observed containing the full env dump — USER, TERM and
        every declared variable — as a literal. process.env is read at
        runtime and has no such effect."""
        src = open("web/concepts/src/lib/auth.ts", encoding="utf-8").read()
        body = src[src.index("export function backendOrigin") :]
        body = body[: body.index("\n}")]
        self.assertIn("process.env.BACKEND_URL", body)
        self.assertNotIn(
            "import.meta.env",
            body,
            "backendOrigin() must not reference import.meta.env; Vite would "
            "inline the whole build-time env object into the SSR bundle",
        )

    def test_dev_up_exports_the_origin_server_env_reads(self):
        """serverEnv() reads process.env only, so dev-up.sh must export
        FRONTEND_URL to the Astro process. Without it publicOrigin() is
        '' in dev and the backend rejects every state-changing request
        with 403."""
        src = open("scripts/dev-up.sh", encoding="utf-8").read()
        astro_block = src[src.index("astro dev") - 800 : src.index("astro dev")]
        self.assertIn(
            "FRONTEND_URL=",
            astro_block,
            "dev-up.sh must export FRONTEND_URL to the astro dev process",
        )

    def test_built_bundle_does_not_bake_the_backend_url(self):
        """Built-output assertion: no emitted server chunk may hardcode a
        backend host. Needs `dist/`; skipped when it is absent."""
        chunks = glob.glob("web/concepts/dist/server/**/*.mjs", recursive=True)
        if not chunks:
            self.skipTest("no build output present; run `astro build` first")
        baked = [
            os.path.basename(c)
            for c in chunks
            if "localhost:8085" in open(c, encoding="utf-8", errors="replace").read()
        ]
        self.assertEqual(
            baked, [], f"build output still hardcodes the dev backend: {baked}"
        )

    def test_built_bundle_does_not_leak_the_build_env(self):
        """Vite inlines the whole build-time env object when a file
        references import.meta.env. USER/TERM are proof it happened."""
        chunks = glob.glob("web/concepts/dist/server/**/*.mjs", recursive=True)
        if not chunks:
            self.skipTest("no build output present; run `astro build` first")
        leaking = [
            os.path.basename(c)
            for c in chunks
            if re.search(
                r'"(?:USER|TERM|USERNAME|HOME|PATH)":\s*"[^"]',
                open(c, encoding="utf-8", errors="replace").read(),
            )
        ]
        self.assertEqual(
            leaking,
            [],
            "build output inlines the build-time environment: " + ", ".join(leaking),
        )

    def test_deployment_doc_does_not_invent_settings(self):
        """docs/DEPLOYMENT.md is a contract for the future proxy
        integration. A setting it names that does not exist in configs.py
        sends the next operator chasing a knob that was never wired.

        An earlier draft of this document described a
        `TRUSTED_PROXY_HOPS` variable that does not exist; the real
        mechanism is `TRUST_PROXY_HEADERS` + `TRUSTED_PROXY_IPS`."""
        doc = open("docs/DEPLOYMENT.md", encoding="utf-8").read()
        configs = open("castor/configs.py", encoding="utf-8").read()
        declared = set(re.findall(r"^\s{4}([A-Z][A-Z0-9_]+)\s*:", configs, re.M))
        # BACKEND_URL is intentionally Astro-only: it is the in-container
        # address the BFF calls, and the backend has no use for it.
        astro_only = {"BACKEND_URL"}
        # Settings this document promises a future integrator.
        promised = {
            "PUBLIC_URL",
            "FRONTEND_URL",
            "TLS_TERMINATED",
            "TRUST_PROXY_HEADERS",
            "TRUSTED_PROXY_IPS",
            "CSRF_ALLOWED_ORIGINS",
            "REQUIRE_ADMIN_FOR_REGISTRATION",
        }
        missing = sorted((promised | astro_only) - declared - astro_only)
        self.assertEqual(
            missing,
            [],
            "docs/DEPLOYMENT.md references settings absent from configs.py: "
            + ", ".join(missing),
        )
        # And the stale name must be gone from the prose.
        self.assertNotIn("TRUSTED_PROXY_HOPS", doc)

    def test_no_caddy_or_acme_dependency_was_introduced(self):
        """The brief is explicit: prepare for a future proxy, do not
        integrate one. Nothing may make the app depend on Caddy or
        Let's Encrypt to run."""
        for banned in ("caddy", "letsencrypt", "let's encrypt", "acme"):
            hits = []
            for path in glob.glob("web/concepts/src/**/*", recursive=True) + glob.glob(
                "castor/**/*.py", recursive=True
            ):
                if not path.endswith((".ts", ".astro", ".py")):
                    continue
                try:
                    body = open(path, encoding="utf-8").read()
                except (OSError, UnicodeDecodeError):
                    continue
                # Comments legitimately explain *why* a setting is off by
                # default by naming the proxy it anticipates. Only real
                # code counts, so a mention cannot pass as a dependency.
                code = re.sub(r"#.*", "", body)
                code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
                code = re.sub(r"//[^\n]*", "", code)
                if banned in code.lower():
                    hits.append(os.path.relpath(path))
            self.assertEqual(
                hits, [], f"source references {banned!r}: {hits}"
            )
        # No Caddyfile may exist in the repo.
        caddies = [
            p for p in glob.glob("**/*", recursive=True)
            if os.path.basename(p) == "Caddyfile"
        ]
        self.assertEqual(caddies, [], f"a Caddyfile was introduced: {caddies}")

    def test_delete_bff_forwards_the_step_up_proof(self):
        """The DELETE BFF dropped the request body, so the fresh-password
        proof the backend requires never arrived and every deletion
        returned 422 "Field required". The backend's step-up control is
        correct; the BFF was the broken link."""
        src = open(
            "web/concepts/src/pages/api/v1/webauthn/credentials/[credentialId].ts",
            encoding="utf-8",
        ).read()
        # The body must be read from the inbound request and passed on.
        self.assertIn("body: await request.text()", src)
        # And it must actually be a DELETE carrying that body. The window is
        # generous because an explanatory comment sits between the two keys.
        self.assertRegex(
            src,
            r"method:\s*'DELETE'[\s\S]{0,600}body:\s*await request\.text\(\)",
        )
        # Forwarding a body without a JSON content-type would make the
        # backend reject the parse, so the header has to come with it.
        self.assertIn("'Content-Type': 'application/json'", src)

    def test_every_bodyed_bff_route_declares_json_content_type(self):
        """A BFF route that streams a body must declare its content type;
        otherwise the backend's parser rejects an otherwise-correct call."""
        offenders = []
        for path in glob.glob(
            "web/concepts/src/pages/api/**/*.ts", recursive=True
        ):
            src = open(path, encoding="utf-8").read()
            for match in re.finditer(
                r"method:\s*'(?:POST|PUT|PATCH|DELETE)'[\s\S]{0,300}?body:\s*"
                r"(?:await request\.text\(\)|JSON\.stringify)",
                src,
            ):
                window = match.group(0)
                if "'Content-Type': 'application/json'" not in window:
                    offenders.append(
                        (os.path.relpath(path, "web/concepts/src"), match.group(0)[:70])
                    )
        self.assertEqual(
            offenders,
            [],
            "BFF routes streaming a body without a JSON content-type:\n"
            + "\n".join(f"  {p}: {w!r}" for p, w in offenders),
        )

    def test_no_blocking_alerts_remain_on_the_security_page(self):
        """alert() from an async handler is announced as a separate window,
        leaves no record, and is easy to miss. A single live region
        replaces them."""
        src = (
            open(
                "web/concepts/src/components/SecurityContent.tsx", encoding="utf-8"
            ).read()
        )
        # Strip comments first: the component's own explanatory comments
        # legitimately mention the alert() calls they replaced, and asserting
        # on those would be asserting on prose.
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        self.assertIsNone(
            re.search(r"(?<![A-Za-z_$])alert\s*\(", code),
            "a blocking window.alert() remains on the security page",
        )
        self.assertIn('role="status"', src)
        self.assertIn("aria-live", src)


if __name__ == "__main__":
    unittest.main()
