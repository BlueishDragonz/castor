"""Page-scoped loader for the structured security registration helper."""

from pathlib import Path

from nicegui import ui


def add_security_passkeys_javascript() -> None:
    """Add trusted, static JS without routes or interpolated account values.

    Call while building the Security page. Pass email/nickname to the browser
    function separately with JSON-safe arguments; its return value has no token.
    Repeated insertion is safe: the script preserves its in-flight ceremony.
    """
    script = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")
    ui.add_head_html(f"<script>{script}</script>")
