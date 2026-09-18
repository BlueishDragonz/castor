# Solve the problem of the checkbox hover effect hide by other checkboxes
# https://github.com/zauberzeug/nicegui/blob/b4bc24bae3d965e0b58e21d9026ec66ba28ae64d/nicegui/static/quasar.css#L1087
from beaverhabits.frontend.design_tokens import (
    CARD_RADIUS,
    DIALOG_RADIUS,
    PANEL_RADIUS,
    BUTTON_RADIUS,
    EMBLEM_RADIUS,
    CARD_PADDING,
    CARD_PADDING_COMPACT,
    DIALOG_PADDING,
    CARD_GAP,
    EMBLEM_SIZE,
    EMBLEM_BG_PRIMARY,
    EMBLEM_BG_SUCCESS,
    COPY_FONT_SIZE,
    COPY_LINE_HEIGHT,
    COPY_OPACITY,
    TRANSITION_DURATION,
    TRANSITION_EASING,
)

CHECK_BOX_CSS = """...
body.desktop .q-checkbox--dense:not(.disabled):hover {
    z-index: 10;
}
body.desktop .q-checkbox--dense:not(.disabled):focus .q-checkbox__inner:before, body.desktop .q-checkbox--dense:not(.disabled):hover .q-checkbox__inner:before {
  transform: scale3d(1.4, 1.4, 1);
}

.q-icon img {
    // transition: opacity 0.3s ease;
}
.q-icon img:hover {
    // opacity: 0.5;
}
"""
CALENDAR_CSS = """...
.q-date {
    width: 100%;
}
.q-date__calendar, .q-date__actions {
    padding: 0;
}
.q-date__calendar {
    min-height: 0;
}
"""
EXPANSION_CSS = """...
.q-item__section--side {
    min-width: 0 !important;
}
"""
HIDE_TIMELINE_TITLE = """...
.q-timeline__title {
    display: none;
}
"""
NOTE_CSS = """...
.q-textarea textarea {
  field-sizing: content;
  min-height: 118px;
}

.q-timeline .q-timeline__content {
    white-space: break-spaces;
}
"""
YOUTUBE_CSS = """...
.videowrapper {
    float: none;
    clear: both;
    width: 100%;
    position: relative;
    padding-bottom: 56.25%;
    padding-top: 25px;
    height: 0;
}
.videowrapper iframe {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
}
"""
TEXTAREA_CSS = """...
.textarea-style {
    border: 1px solid #ccc;
    border-radius: 4px;
    padding: 8px;
    font-size: 14px;
    line-height: 1.5;
}
"""

# Self-hosted Inter (variable, latin). Replaces Quasar's default Roboto
# (which also phones home to Google Fonts) across every page.
FONT_CSS = """...
@font-face {
    font-family: 'Inter';
    font-style: normal;
    font-weight: 100 900;
    font-display: swap;
    src: url('/statics/fonts/inter-latin.woff2') format('woff2');
}

body {
    font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
}
"""


WHITE_FLASH_PREVENT = """...
:root {
  --theme-color: #121212;
  --background-color: #f9f9f9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --theme-color: #121212;
    --background-color: #121212;
  }
}

body {
  background-color: var(--background-color);
  color: var(--theme-color);

  user-select: none;
  touch-callout:none;
  -webkit-touch-callout:none;
  -webkit-touch-callout: none;
  -webkit-user-callout: none;
  -webkit-user-select: none;
  -webkit-user-drag: none;
  -webkit-user-modify: none;
  -webkit-highlight: none;
}

.body--light {
  background-color: #f9f9f9;
  color: #121212;
  
  .nicegui-link {
    color: #121212;
  }

  .theme-icon-checkbox .q-checkbox__inner--falsy .q-checkbox__icon {
    color: #BDBDBD;
  }
  .theme-icon-checkbox .q-checkbox__inner--truthy .q-checkbox__icon {
    color: #1976d2;
  }

  .theme-header-date {
    font-size: 85%; 
    font-weight: 500; 
    color: #616161;
  }

  .theme-menu-icon {
    color: #121212;
  }

  .theme-card-shadow {
    box-shadow: 0 1px 3px rgba(0,0,0,0.12);
  }
}

.body--dark {
  background-color: #121212;
  color: #fff;

  .nicegui-link {
    color: #FFF;
  }

  .theme-icon-checkbox .q-checkbox__inner--falsy .q-checkbox__icon {
    color: #616161;
  }
  .theme-icon-checkbox .q-checkbox__inner--truthy .q-checkbox__icon {
    color: #6796CF;
  }
  .q-checkbox--dark .q-checkbox__inner--falsy {
    color: hsla(0, 0%, 100%, .5);
  }

  .theme-header-date {
    font-size: 85%; 
    font-weight: 500; 
    color: #9E9E9E;
  }

  .theme-menu-icon {
    color: #FFF;
  }

  .theme-card-shadow {
    box-shadow: none;
  }
}
"""


# === Beaver Habits Design System (promoted from security page) ===
# Uses Quasar CSS custom properties for colours so it adapts to light/dark/themes.
BH_DESIGN_CSS = f"""/* Card base */
.bh-card {{
  border-radius: {CARD_RADIUS};
  padding: {CARD_PADDING};
  gap: {CARD_GAP};
  box-shadow: none;
}}

/* Compact card variant for dense lists (habit rows) */
.bh-card-compact {{
  border-radius: {CARD_RADIUS};
  padding: {CARD_PADDING_COMPACT};
  gap: {CARD_GAP};
  box-shadow: none;
}}

/* Dialog / panel variant */
.bh-dialog-panel {{
  border-radius: {DIALOG_RADIUS};
  padding: {DIALOG_PADDING};
  gap: {CARD_GAP};
  /* No fixed width: the panel is w-full inside its responsive parent
     (e.g. auth_card's w-80 sm:w-96). A hardcode here would override the
     parent's responsive sizing and overflow small viewports (P3-9). */
  max-width: calc(100vw - 32px);
}}

/* Emblem/icon treatment */
.bh-emblem {{
  width: {EMBLEM_SIZE};
  height: {EMBLEM_SIZE};
  border-radius: {EMBLEM_RADIUS};
  display: flex;
  align-items: center;
  justify-content: center;
  background: {EMBLEM_BG_PRIMARY};
  color: var(--q-primary);
  font-size: 28px;
}}
.bh-emblem--success {{
  background: {EMBLEM_BG_SUCCESS};
  color: #16814b;
}}

/* Copy text */
.bh-copy {{
  font-size: {COPY_FONT_SIZE};
  line-height: {COPY_LINE_HEIGHT};
  opacity: {COPY_OPACITY};
}}

/* Text wrapping helper */
.bh-wrap {{
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: normal;
}}

/* Button radius override */
.bh-btn {{
  border-radius: {BUTTON_RADIUS} !important;
}}

/* Enter animation for dialog stages */
@keyframes bh-enter {{
  from {{ opacity: 0; transform: translateY(5px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}
.bh-stage {{ animation: bh-enter {TRANSITION_DURATION} {TRANSITION_EASING}; }}

/* Reduced motion — global */
@media (prefers-reduced-motion: reduce) {{
  .bh-dialog, .bh-dialog *, .bh-dialog *::before, .bh-dialog *::after,
  .bh-card, .bh-card *, .bh-stage {{
    animation: none !important;
    transition: none !important;
    --q-transition-duration: 0ms !important;
  }}
}}
"""