"""F25 — optional integrations must fail soft, and the advice they print must work.

The audit's F25: `paddle_billing` and `sentry_sdk` were imported inside
`if settings.SENTRY_DSN:` but declared nowhere, so configuring error reporting
crashed the app at import time. The bug was a hard boot failure on a path that
looks like an opt-in.

The fix wraps the import. That alone is half of it — the other half is that the
error message must be actionable. A graceful message telling the operator to
install a package that is not installable is a slower dead end than a crash,
because it sends someone away believing the problem is solved.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

from castor.configs import settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_sentry_dsn_without_the_package_does_not_crash_the_app(tmp_path):
    """F25: configuring Sentry with no sentry_sdk must log, not crash.

    The audit's failure was a ModuleNotFoundError at module-import time — a
    boot crash on the exact path an operator uses to turn error reporting ON.

    A subprocess is the only honest way to test this: by the time an in-process
    test runs, `castor.main` is already imported and the guard has already run
    (or crashed) once. Reimporting in-process would not reproduce the condition.
    """
    script = tmp_path / "boot_with_sentry.py"
    script.write_text(
        """
import sys

# CPython raises ImportError for `import x` when sys.modules["x"] is None.
# This is the standard way to simulate a genuinely absent package.
sys.modules["sentry_sdk"] = None

import castor.configs as configs_module
configs_module.settings.SENTRY_DSN = "https://example.invalid/123"

import castor.main  # noqa: F401
print("BOOT_OK")
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = result.stdout + result.stderr

    assert "BOOT_OK" in result.stdout, (
        "Importing castor.main with a configured SENTRY_DSN and no sentry_sdk "
        f"must not fail. rc={result.returncode}\n{combined}"
    )
    # It must also say something useful, not fail silently.
    assert "sentry-sdk package is not installed" in combined, (
        "The failure must be visible to the operator, not swallowed."
    )


def test_sentry_extra_is_declared_and_resolvable():
    """`uv sync --extra sentry` is what the runtime error tells operators to run.

    If the extra does not exist, that instruction is wrong, and following it
    produces a second failure that looks like a network problem.
    """
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        pyproject = tomllib.load(handle)

    extras = pyproject.get("project", {}).get("optional-dependencies", {})
    assert "sentry" in extras, (
        "The F25 error message tells the operator to run "
        "`uv sync --extra sentry`, but pyproject.toml declares no such extra."
    )
    assert extras["sentry"], "The sentry extra is declared but empty."


def test_paddle_is_not_declared_because_nothing_imports_it():
    """Paddle's import is gone; declaring a dead package would break uv lock.

    Guards against a future "fix" that helpfully adds `paddle-billing` back and
    breaks every build, since it is not resolvable from PyPI.
    """
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        pyproject = tomllib.load(handle)

    extras = pyproject.get("project", {}).get("optional-dependencies", {})
    assert "paddle" not in extras

    # And nothing should be importing it either.
    for path in (REPO_ROOT / "castor").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import paddle_billing" not in text, f"{path} imports paddle_billing"


def test_send_pii_defaults_off():
    """F11: enabling telemetry must not silently start exporting personal data."""
    assert settings.SENTRY_SEND_PII is False
