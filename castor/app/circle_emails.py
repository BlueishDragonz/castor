"""Circle email rendering.

Pure-function helpers that load `castor/templates/circle_invite.{html,txt}`
and substitute the invite variables. Lives in its own module so the
template-loading import chain stays out of the request hot-path; the
production request handler imports this only when `delivery=='email'`,
which is the small minority of mints.

Why string.Template and not str.format? Because the HTML template has
many literal `{ ... }` blocks (CSS rules) and Jinja isn't worth pulling
in for one template. string.Template uses `$var` and `${var}`, so the
CSS braces don't need doubling and the visible source is just what we
ship.

Public API:
    render_invite_email(*, inviter_email, circle_name, join_url,
                        frontend_origin, expires_hours) -> dict
        Returns ``{"html": str, "text": str, "subject": str}`` ready to
        pass straight to ``castor.utils.send_email``. Subject is built
        here so a future rebrand changes one file, not two.
"""
from __future__ import annotations

import os
import string
from functools import lru_cache

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


@lru_cache(maxsize=4)
def _load(name: str) -> str:
    """Read a template by file name. Cached because templates only change on deploy."""
    path = os.path.normpath(os.path.join(_TEMPLATE_DIR, name))
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _render(name: str, mapping: dict) -> str:
    # safe_substitute: unknown $placeholders (e.g. those we mention in
    # documentation comments inside the template) are left as-is rather
    # than raising KeyError. Useful here because the HTML template's
    # leading doc comment lists every variable we could substitute —
    # including ones (inviter_name, circle_id, join_token) that this
    # slice does NOT inject.
    return string.Template(_load(name)).safe_substitute(mapping)


def render_invite_email(
    *,
    inviter_email: str,
    circle_name: str,
    join_url: str,
    frontend_origin: str,
    expires_hours: int,
) -> dict[str, str]:
    """Render both parts of the invite email.

    Variables exposed to the templates:
        inviter_email, circle_name, join_url, expires_hours,
        frontend_origin.

    The plaintext template uses the same variable names so editing
    one place (this function) covers both parts.
    """
    safe_circle = circle_name.replace("]", "]")  # no-op; placeholder for future escaping
    common = {
        "inviter_email": inviter_email,
        "circle_name": safe_circle,
        "join_url": join_url,
        "expires_hours": str(expires_hours),
        "frontend_origin": frontend_origin,
    }
    html = _render("circle_invite.html", common)
    text = _render("circle_invite.txt", common)
    subject = (
        f"{inviter_email} invited you to join "
        f"'{circle_name}' on Castor"
    )
    return {"html": html, "text": text, "subject": subject}
