"""Beaver Habits Design Tokens — single source for spacing, radii, transitions.
Colours reference Quasar CSS custom properties (--q-primary, --q-negative, etc.)
so they adapt to light/dark and any theme customization.
"""
from __future__ import annotations

# ─── Radii ──────────────────────────────────────────────────────────────
CARD_RADIUS = "18px"
DIALOG_RADIUS = "20px"
PANEL_RADIUS = "20px"
BUTTON_RADIUS = "10px"
EMBLEM_RADIUS = "16px"

# ─── Spacing ────────────────────────────────────────────────────────────
CARD_PADDING = "22px"
DIALOG_PADDING = "24px"
CARD_GAP = "18px"

# ─── Emblem ─────────────────────────────────────────────────────────────
EMBLEM_SIZE = "52px"
# Primary emblem uses Quasar primary colour with 9% opacity
EMBLEM_BG_PRIMARY = "rgba(var(--q-primary-rgb), .09)"
# Success green — not in Quasar palette, keep literal
EMBLEM_BG_SUCCESS = "rgba(22, 129, 75, .1)"

# ─── Copy / Typography ──────────────────────────────────────────────────
COPY_FONT_SIZE = "13px"
COPY_LINE_HEIGHT = "1.65"
COPY_OPACITY = ".72"

# ─── Transitions / Animation ────────────────────────────────────────────
TRANSITION_DURATION = "200ms"
TRANSITION_EASING = "ease-out"

# ─── Helper to build CSS custom property references ─────────────────────
def qvar(name: str) -> str:
    """Reference a Quasar CSS custom property, e.g. qvar('primary') -> 'var(--q-primary)'."""
    return f"var(--q-{name})"

def qrgb(name: str) -> str:
    """Reference a Quasar RGB custom property, e.g. qrgb('primary') -> 'var(--q-primary-rgb)'."""
    return f"var(--q-{name}-rgb)"