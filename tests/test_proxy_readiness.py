"""Deployment-boundary readiness: public URL, cookies, proxy-header trust.

These tests pin behaviour that a future TLS-terminating reverse proxy
(Caddy + Let's Encrypt) will depend on, WITHOUT installing or configuring
any proxy. Nothing here asserts that Caddy exists; the point is that the
application's own configuration surface is correct, explicit and
backward-compatible so the integration is a deployment step, not a code
change.

What is asserted:

  1. The cookie Secure flag follows the operator-declared public origin.
     Previously it read only APP_URL/TLS_TERMINATED, so an operator who
     set the documented FRONTEND_URL to https and left the SaaS-era
     APP_URL empty shipped a non-Secure 30-day session cookie.
  2. The public origin is a single source of truth: the CSRF allowlist
     and the Secure decision cannot disagree about the app's own URL.
  3. Proxy-header trust is OFF by default and never client-controlled.
  4. The WebAuthn browser cookie uses the same Secure predicate as the
     session cookie (it used to be a third, divergent one).
  5. The container entrypoint passes forwarded-header flags only when the
     operator explicitly opts in.
  6. Startup warns (never fails) about deployment-boundary mismatches.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BASE_ENV = {
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "JWT_SECRET": "boundary-test-only-not-a-real-secret",
    "RESET_PASSWORD_TOKEN_SECRET": "boundary-test-reset",
    "NICEGUI_STORAGE_SECRET": "boundary-test-storage",
    "JWT_LIFETIME_SECONDS": "3600",
}


def _cookie_settings(env: dict) -> dict:
    """Import castor.app.users under a specific environment and read the
    cookie settings. A subprocess is used so each case gets a clean
    Settings() construction (the module caches `settings` at import)."""
    script = (
        "from castor.app.users import get_cookie_settings;"
        "import json; print(json.dumps(get_cookie_settings()))"
    )
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-c", script],
        cwd=REPO,
        env={**os.environ, **BASE_ENV, **env},
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"import failed: {proc.stderr[-800:]}")
    import json

    return json.loads(proc.stdout.strip().splitlines()[-1])


def _settings_attr(attr: str, env: dict) -> object:
    script = f"from castor.configs import settings; print(repr(getattr(settings, {attr!r})))"
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-c", script],
        cwd=REPO,
        env={**os.environ, **BASE_ENV, **env},
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"import failed: {proc.stderr[-800:]}")
    return proc.stdout.strip().splitlines()[-1]


class PublicOriginCookieTests(unittest.TestCase):
    """The Secure flag must follow the operator's declared public origin."""

    def test_https_public_url_marks_session_cookie_secure(self):
        """The core fix: an operator who declares an https public URL gets
        a Secure cookie, without having to also set the legacy APP_URL."""
        settings_cookie = _cookie_settings(
            {
                "PUBLIC_URL": "https://castor.example",
                "FRONTEND_URL": "https://castor.example",
                "APP_URL": "",          # left empty, as a fresh deploy would
                "TLS_TERMINATED": "false",
            }
        )
        self.assertTrue(
            settings_cookie["secure"],
            "PUBLIC_URL=https:// must set Secure on the session cookie",
        )

    def test_https_frontend_url_marks_the_cookie_secure(self):
        """An HTTPS FRONTEND_URL is an operator declaration that the
        browser-facing app is served over TLS, so it must mark the session
        cookie Secure.

        This assertion previously read the opposite way ("an HTTPS
        FRONTEND_URL alone does NOT force Secure"), on the reasoning that
        FRONTEND_URL is only the frontend origin and can legitimately
        differ from the app origin. That reasoning is wrong on two counts:

          1. It contradicted the frontend. lib/auth.ts's isProd() has
             always treated an https:// public origin as production, so
             the two halves of the app disagreed about whether the
             deployment was secure — the exact class of bug the
             single-source-of-truth change exists to remove.
          2. The stated risk does not apply. The failure mode of marking a
             cookie Secure over plain HTTP is that the browser withholds
             it, so the user cannot sign in. But a deployment declaring
             https:// in FRONTEND_URL while actually serving http is
             already misconfigured, and silently downgrading the cookie to
             non-Secure hides that instead of surfacing it.

        The genuine hazard this was guarding against — deriving the flag
        from BACKEND_URL, the internal loopback address, which is plain
        HTTP in production — is handled by never consulting BACKEND_URL
        for this decision. See test_https_public_url_marks_session_cookie_secure.
        """
        self.assertTrue(
            _cookie_settings(
                {
                    "FRONTEND_URL": "https://castor.example",
                    "PUBLIC_URL": "",
                    "APP_URL": "",
                    "TLS_TERMINATED": "false",
                }
            )["secure"]
        )

    def test_legacy_app_url_still_marks_secure(self):
        """Backward compatibility: a deployment that only ever set APP_URL
        must keep the behaviour it had before PUBLIC_URL existed."""
        self.assertTrue(
            _cookie_settings({"APP_URL": "https://old.example", "PUBLIC_URL": ""})["secure"]
        )

    def test_legacy_tls_terminated_flag_still_marks_secure(self):
        """Same for the older TLS_TERMINATED switch."""
        self.assertTrue(
            _cookie_settings(
                {"APP_URL": "", "PUBLIC_URL": "", "TLS_TERMINATED": "true"}
            )["secure"]
        )

    def test_plain_http_deployment_is_not_secure(self):
        """A plain-HTTP dev/self-hosted install must keep working: a Secure
        cookie would never be sent back over http and every request would
        look logged out."""
        self.assertFalse(
            _cookie_settings(
                {
                    "PUBLIC_URL": "http://10.8.0.1:8080",
                    "APP_URL": "",
                    "TLS_TERMINATED": "false",
                }
            )["secure"]
        )

    def test_public_url_wins_over_conflicting_app_url(self):
        """When both are set and they disagree, PUBLIC_URL wins. It is the
        setting the documentation tells operators to use, whereas APP_URL
        is legacy ballast nothing else in the codebase reads."""
        self.assertTrue(
            _cookie_settings(
                {"PUBLIC_URL": "https://castor.example", "APP_URL": "http://castor.example"}
            )["secure"]
        )

    def test_cookie_is_httponly_and_strict_regardless(self):
        cookie = _cookie_settings({"PUBLIC_URL": "https://castor.example"})
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "strict")

    def test_trailing_slash_does_not_break_the_https_check(self):
        """An operator writing PUBLIC_URL=https://castor.example/ (a very
        natural typo) must not silently get a non-Secure cookie."""
        self.assertTrue(
            _cookie_settings({"PUBLIC_URL": "https://castor.example/"})["secure"]
        )


class PublicUrlIsSingleSourceOfTruthTests(unittest.TestCase):
    """The CSRF allowlist and the Secure flag must agree about the app URL."""

    def _allowed_origins(self, env: dict) -> list:
        script = (
            "from castor.configs import settings;"
            " print(repr(settings.csrf_allowed_origins()))"
        )
        proc = subprocess.run(
            [str(REPO / ".venv/bin/python"), "-c", script],
            cwd=REPO,
            env={**os.environ, **BASE_ENV, **env},
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise AssertionError(f"import failed: {proc.stderr[-800:]}")
        import ast

        return ast.literal_eval(proc.stdout.strip().splitlines()[-1])

    def test_public_url_is_in_csrf_allowlist(self):
        """If the origin is declared public it must also be an allowed
        write origin, or every browser write 403s the moment TLS lands."""
        origins = self._allowed_origins(
            {"PUBLIC_URL": "https://castor.example", "FRONTEND_URL": ""}
        )
        self.assertIn("https://castor.example", origins)

    def test_empty_public_url_adds_nothing(self):
        """With nothing declared, the allowlist is whatever the operator
        configured elsewhere — PUBLIC_URL must contribute no entry, not an
        empty string that could never match a real origin."""
        origins = self._allowed_origins({"PUBLIC_URL": "", "FRONTEND_URL": ""})
        self.assertNotIn("", origins)

    def test_trailing_slash_is_normalised_out_of_the_allowlist(self):
        """Origins are compared as (scheme, host, port) tuples, so a
        trailing slash would not match and every write would 403."""
        origins = self._allowed_origins({"PUBLIC_URL": "https://castor.example/"})
        self.assertIn("https://castor.example", origins)
        self.assertNotIn("https://castor.example/", origins)


class ProxyTrustDefaultsTests(unittest.TestCase):
    """Forwarded headers must not be authoritative by default."""

    def test_proxy_trust_is_off_by_default(self):
        self.assertEqual(
            _settings_attr("TRUST_PROXY_HEADERS", {}),
            "False",
            "TRUST_PROXY_HEADERS must default to False",
        )
        self.assertEqual(_settings_attr("TRUSTED_PROXY_IPS", {}), "''")

    def test_rate_limiter_never_reads_forwarded_headers(self):
        """The per-IP rate limit keys on the ASGI peer address. Trusting
        X-Forwarded-For without an allowlist would let a client mint a
        fresh rate-limit bucket per request by varying a header."""
        source = (REPO / "castor/app/rate_limits.py").read_text()
        self.assertNotIn(
            'headers.get("x-forwarded-for")',
            source.lower(),
            "rate limiting must not read X-Forwarded-For",
        )

    def test_no_module_reads_x_forwarded_proto_for_security_decisions(self):
        """Nothing in the app may derive scheme/host from a request header
        to decide cookies, HSTS or the WebAuthn origin."""
        for path in (REPO / "castor").rglob("*.py"):
            text = path.read_text().lower()
            self.assertNotIn(
                'get("x-forwarded-proto")',
                text,
                f"{path} reads X-Forwarded-Proto",
            )

    def test_entrypoint_passes_proxy_flags_only_when_opted_in(self):
        """The container supervisor must not enable forwarded-header trust
        unless the operator asked for it, and must never use '*'."""
        script = (REPO / "docker/entrypoint.sh").read_text()
        self.assertIn("TRUST_PROXY_HEADERS", script)
        self.assertIn("TRUSTED_PROXY_IPS", script)
        # No unconditional trust-all anywhere in the supervisor.
        self.assertNotIn("--forwarded-allow-ips=*", script)
        # The flags must be conditional, not baked into the gunicorn line.
        self.assertRegex(script, r'if \[ "\$\{TRUST_PROXY_HEADERS')


class WebAuthnCookieParityTests(unittest.TestCase):
    """The WebAuthn browser cookie and the session cookie must agree."""

    def test_browser_cookie_uses_the_shared_secure_predicate(self):
        """It used a third predicate (APP_URL only, ignoring TLS_TERMINATED
        and PUBLIC_URL), so the two cookies could disagree about Secure
        within a single deployment."""
        source = (REPO / "castor/app/webauthn_routes.py").read_text()
        self.assertIn("secure=settings.is_https_public()", source)
        self.assertNotIn('secure=settings.APP_URL.startswith("https://")', source)

    def test_rp_id_and_origin_remain_explicit_settings(self):
        """RP ID and expected origin must stay operator-declared and must
        not be derived per-request: WebAuthn verification compares the
        browser's origin against WEBAUTHN_ORIGIN exactly, so a header-
        derived value would let a request dictate what counts as a valid
        origin."""
        source = (REPO / "castor/app/webauthn_routes.py").read_text()
        self.assertIn("expected_rp_id=settings.WEBAUTHN_RP_ID", source)
        self.assertIn("expected_origin=settings.WEBAUTHN_ORIGIN", source)


class StartupBoundaryWarningsTests(unittest.TestCase):
    """Misconfiguration should be loud, not fatal and not silent."""

    def _boot(self, env: dict) -> subprocess.CompletedProcess:
        """Import the app AND run its lifespan.

        Import alone is not enough: every deployment-boundary check lives
        in the lifespan startup hook (so it runs on the real boot path,
        not at import), and the secret fail-fast checks live there too.
        A test that only imported the module would pass against code that
        never executes in production.
        """
        with tempfile.TemporaryDirectory() as tmp:
            full = {
                **BASE_ENV,
                "ENV": "production",
                "DEBUG": "false",
                "DATABASE_URL": f"sqlite+aiosqlite:///{tmp}/boot.db",
                "NICEGUI_STORAGE_SECRET": "boundary-test-storage",
                **env,
            }
            script = (
                "import anyio;"
                "from castor.main import app, lifespan;"
                "\n"
                "async def run():\n"
                "    async with lifespan(app):\n"
                "        print('BOOT_OK')\n"
                "\n"
                "anyio.run(run)"
            )
            return subprocess.run(
                [str(REPO / ".venv/bin/python"), "-c", script],
                cwd=REPO,
                env={**os.environ, **full},
                capture_output=True,
                text=True,
            )

    def test_missing_https_passkey_origin_warns_but_boots(self):
        """A production deployment that has not yet put TLS in front must
        still start. Refusing to boot would turn a staged migration into an
        outage."""
        proc = self._boot(
            {
                "WEBAUTHN_ORIGIN": "http://castor.example",
                "WEBAUTHN_RP_ID": "castor.example",
                "PUBLIC_URL": "",
                "APP_URL": "",
                "TLS_TERMINATED": "false",
            }
        )
        self.assertIn("BOOT_OK", proc.stdout, proc.stderr[-500:])
        self.assertIn("secure context", (proc.stdout + proc.stderr).lower())

    def test_https_declared_with_http_passkey_origin_warns(self):
        """The two halves of the deployment boundary disagreeing is the
        single most likely Caddy-migration mistake: TLS lands, cookies go
        Secure, but every WebAuthn ceremony still fails on origin."""
        proc = self._boot(
            {
                "WEBAUTHN_ORIGIN": "http://castor.example",
                "WEBAUTHN_RP_ID": "castor.example",
                "PUBLIC_URL": "https://castor.example",
            }
        )
        self.assertIn("BOOT_OK", proc.stdout, proc.stderr[-500:])
        combined = proc.stdout + proc.stderr
        self.assertIn("ceremony will be rejected", combined)

    def test_trust_all_proxies_is_an_error_level_warning(self):
        proc = self._boot(
            {
                "WEBAUTHN_ORIGIN": "https://castor.example",
                "WEBAUTHN_RP_ID": "castor.example",
                "PUBLIC_URL": "https://castor.example",
                "TRUST_PROXY_HEADERS": "true",
                "TRUSTED_PROXY_IPS": "*",
            }
        )
        self.assertIn("BOOT_OK", proc.stdout, proc.stderr[-500:])
        self.assertIn("TRUSTED_PROXY_IPS", proc.stdout + proc.stderr)

    def test_fully_configured_https_deployment_emits_no_boundary_warning(self):
        proc = self._boot(
            {
                "WEBAUTHN_ORIGIN": "https://castor.example",
                "WEBAUTHN_RP_ID": "castor.example",
                "PUBLIC_URL": "https://castor.example",
                "FRONTEND_URL": "https://castor.example",
            }
        )
        self.assertIn("BOOT_OK", proc.stdout, proc.stderr[-500:])
        combined = proc.stdout + proc.stderr
        self.assertNotIn("secure context for WebAuthn", combined)
        self.assertNotIn("ceremony will be rejected", combined)

    def test_weak_secrets_still_fail_fast(self):
        """The boundary checks must not have displaced the existing
        fail-fast secret validation, which is what actually stops an
        insecure deployment from starting."""
        proc = self._boot({"JWT_SECRET": "SECRET"})
        self.assertNotIn("BOOT_OK", proc.stdout)
        self.assertIn("JWT_SECRET", proc.stdout + proc.stderr)


class NoCaddyDependencyTests(unittest.TestCase):
    """The app must remain free of any reverse-proxy runtime dependency."""

    def test_no_caddy_or_letsencrypt_runtime_dependency(self):
        pyproject = (REPO / "pyproject.toml").read_text().lower()
        for banned in ("caddy", "certbot", "acme", "letsencrypt"):
            self.assertNotIn(banned, pyproject)

    def test_no_caddyfile_is_committed(self):
        self.assertFalse(
            list(REPO.glob("**/Caddyfile*")),
            "a Caddyfile would be an active deployment change, not readiness",
        )

    def test_backend_binds_loopback_in_the_image(self):
        """The backend is internal-only; the published port is Astro's.
        This is the app-to-proxy boundary a future Caddy points at."""
        entrypoint = (REPO / "docker/entrypoint.sh").read_text()
        self.assertIn("--bind 127.0.0.1:8081", entrypoint)

    def test_health_endpoint_is_proxy_friendly_plaintext(self):
        """A future proxy health check needs a cheap, unauthenticated,
        non-redirecting endpoint. /health asserts the database and returns
        plain text, which is what a probe can assert on."""
        source = (REPO / "castor/main.py").read_text()
        self.assertIn('@app.get("/health"', source)
        self.assertIn("PlainTextResponse", source)
        self.assertIn('@app.get("/live"', source)


if __name__ == "__main__":
    unittest.main()


class IpLiteralRpIdTests(unittest.TestCase):
    """A WebAuthn relying-party ID must be a hostname, not an IP.

    The apollo deployment pins WEBAUTHN_RP_ID=10.8.0.1. Browsers reject
    a literal IP as an RP ID, so passkeys cannot work there — but the
    only symptom is an opaque "The operation is insecure" at the moment
    of use, long after boot. These pin the detection that turns that into
    an actionable start-up warning.
    """

    def test_detects_bare_ipv4(self):
        from castor.main import _looks_like_ip_literal

        for host in ("10.8.0.1", "127.0.0.1", "192.168.1.10", "8.8.8.8"):
            self.assertTrue(_looks_like_ip_literal(host), host)

    def test_detects_bare_ipv6(self):
        from castor.main import _looks_like_ip_literal

        self.assertTrue(_looks_like_ip_literal("::1"))
        self.assertTrue(_looks_like_ip_literal("[::1]"), "bracketed form")

    def test_hostnames_are_not_flagged(self):
        from castor.main import _looks_like_ip_literal

        for host in ("localhost", "castor.example.com", "app.internal", ""):
            self.assertFalse(_looks_like_ip_literal(host), host)

    def test_importing_main_is_side_effect_free(self):
        """The helper must not require booting the app to test."""
        from castor.main import _looks_like_ip_literal

        self.assertTrue(callable(_looks_like_ip_literal))


class WebauthnRelyingPartyDerivationTests(unittest.TestCase):
    """WEBAUTHN_RP_ID / WEBAUTHN_ORIGIN are derived from the public origin.

    The bug this prevents: WEBAUTHN_RP_ID was hardcoded to "localhost"
    regardless of where the app was served, so on a host reached by any
    other name the browser refused every ceremony. Verified directly:
    Firefox rejected rp.id="127.0.0.1" on a 127.0.0.1 page with
    "The operation is insecure", and accepted rp.id="localhost".
    """

    def _settings(self, **kw):
        """A Settings instance with the relying party reset to its defaults.

        `Settings` is a pydantic-settings model, so keyword arguments
        override both the class defaults and anything in the ambient
        environment. Resetting the two relying-party fields is still
        necessary: if a previously-imported test module called
        resolve_webauthn_settings() on the process singleton, the
        inherited environment would otherwise decide the outcome and this
        test would silently stop testing the derivation.
        """
        from castor.configs import (
            DEFAULT_WEBAUTHN_ORIGIN,
            DEFAULT_WEBAUTHN_RP_ID,
            Settings,
        )

        kw.setdefault("PUBLIC_URL", "")
        kw.setdefault("FRONTEND_URL", "")
        kw.setdefault("APP_URL", "")
        kw.setdefault("WEBAUTHN_RP_ID", DEFAULT_WEBAUTHN_RP_ID)
        kw.setdefault("WEBAUTHN_ORIGIN", DEFAULT_WEBAUTHN_ORIGIN)
        return Settings(**kw)

    def test_derives_from_the_declared_public_url(self):
        s = self._settings(PUBLIC_URL="https://castor.example")
        s.resolve_webauthn_settings()
        self.assertEqual(s.WEBAUTHN_RP_ID, "castor.example")
        self.assertEqual(s.WEBAUTHN_ORIGIN, "https://castor.example")

    def test_derives_keeping_the_port_in_the_origin(self):
        """The dev stack serves on 4321; dropping the port would make the
        expected origin disagree with the browser's actual origin."""
        s = self._settings(PUBLIC_URL="http://localhost:4321")
        s.resolve_webauthn_settings()
        self.assertEqual(s.WEBAUTHN_ORIGIN, "http://localhost:4321")
        self.assertEqual(s.WEBAUTHN_RP_ID, "localhost")

    def test_explicit_settings_are_never_overwritten(self):
        s = self._settings(
            PUBLIC_URL="https://declared.example",
            WEBAUTHN_RP_ID="pinned.example",
            WEBAUTHN_ORIGIN="https://pinned.example",
        )
        s.resolve_webauthn_settings()
        self.assertEqual(s.WEBAUTHN_RP_ID, "pinned.example")
        self.assertEqual(s.WEBAUTHN_ORIGIN, "https://pinned.example")

    def test_no_declared_origin_keeps_a_usable_fallback(self):
        """An empty RP ID makes py-webauthn raise "rp_id cannot be an
        empty string", which would crash any code path that builds options
        without booting the lifespan. The fallback must stay usable."""
        s = self._settings(PUBLIC_URL="", FRONTEND_URL="", APP_URL="")
        s.resolve_webauthn_settings()
        self.assertEqual(s.WEBAUTHN_RP_ID, "localhost")
        self.assertTrue(s.WEBAUTHN_ORIGIN)

    def test_rp_id_omits_scheme_and_port(self):
        """py-webauthn rejects an rp.id carrying a scheme or port."""
        s = self._settings(PUBLIC_URL="https://castor.example:8443")
        s.resolve_webauthn_settings()
        self.assertNotIn("://", s.WEBAUTHN_RP_ID)
        self.assertNotIn(":", s.WEBAUTHN_RP_ID)
        # The port belongs in the origin, not the RP ID.
        self.assertEqual(s.WEBAUTHN_ORIGIN, "https://castor.example:8443")

    def test_malformed_public_url_is_ignored_not_fatal(self):
        s = self._settings(PUBLIC_URL="://not a url")
        s.resolve_webauthn_settings()  # must not raise
        self.assertTrue(s.WEBAUTHN_RP_ID)
