"""Conservative, parser-based CSS for embedding in an HTML ``style`` element.

Only ordinary style rules and nested @media rules are supported. A bad or
unsupported construct rejects the *whole* stylesheet (returns an empty string).
The property/function allowlists deliberately exclude custom properties/var(),
attr(), URL/image sources, imports, font faces, animation rules, filters, vendor
extensions, CSS nesting, and every at-rule other than @media. Basic selectors,
typography, colors, gradients, flex/grid layout, and transforms remain available.

This is a security subset, not a full CSS semantic/selector validator: browsers
may ignore grammatically well-tokenized but semantically invalid values. CSS can
still hide/reposition UI; this is not isolation from the application's UI. Less-
than characters (including escaped ones) are unsupported even inside strings or
media ranges, to make HTML style-element termination impossible in the output.
"""
from __future__ import annotations

# ruff: noqa: SIM905 -- space-separated security allowlists stay reviewable.
import math

import tinycss2

MAX_CSS_LENGTH = 32_768
MAX_NESTING = 16
MAX_TOKENS = 8_192

# Keep the security allowlists grouped by feature for review.
_PROPERTIES = frozenset("""
color background background-color background-image background-position
background-size background-repeat background-origin background-clip
background-attachment opacity
font font-family font-size font-weight font-style font-variant font-stretch
line-height letter-spacing word-spacing text-align text-align-last text-indent
text-transform text-decoration text-decoration-line text-decoration-color
text-decoration-style text-decoration-thickness text-underline-offset text-shadow
text-overflow white-space overflow-wrap word-break hyphens tab-size
width min-width max-width height min-height max-height aspect-ratio
inline-size min-inline-size max-inline-size block-size min-block-size max-block-size
margin margin-top margin-right margin-bottom margin-left margin-inline
margin-inline-start margin-inline-end margin-block margin-block-start margin-block-end
padding padding-top padding-right padding-bottom padding-left padding-inline
padding-inline-start padding-inline-end padding-block padding-block-start padding-block-end
border border-width border-style border-color border-top border-right border-bottom
border-left border-top-width border-right-width border-bottom-width border-left-width
border-top-style border-right-style border-bottom-style border-left-style
border-top-color border-right-color border-bottom-color border-left-color border-radius
border-top-left-radius border-top-right-radius border-bottom-left-radius border-bottom-right-radius
border-collapse border-spacing outline outline-width outline-color outline-style outline-offset
box-sizing box-shadow display visibility position top right bottom left inset
inset-inline inset-block z-index float clear overflow overflow-x overflow-y
overscroll-behavior resize vertical-align
flex flex-basis flex-direction flex-flow flex-grow flex-shrink flex-wrap order
align-content align-items align-self justify-content justify-items justify-self
place-content place-items place-self gap row-gap column-gap
grid grid-template grid-template-columns grid-template-rows grid-template-areas
grid-auto-columns grid-auto-rows grid-auto-flow grid-column grid-column-start
grid-column-end grid-row grid-row-start grid-row-end grid-area
columns column-count column-width column-rule column-rule-color column-rule-style
column-rule-width column-span column-fill break-before break-after break-inside
list-style-type list-style-position table-layout caption-side empty-cells
object-fit object-position transform transform-origin transform-style
perspective perspective-origin backface-visibility cursor user-select pointer-events
accent-color caret-color color-scheme appearance isolation mix-blend-mode
""".split())

_VALUE_FUNCTIONS = frozenset("""
rgb rgba hsl hsla hwb lab lch oklab oklch color color-mix
calc min max clamp round mod rem abs sign
linear-gradient repeating-linear-gradient radial-gradient repeating-radial-gradient
conic-gradient repeating-conic-gradient
repeat minmax fit-content
matrix matrix3d translate translatex translatey translatez translate3d
scale scalex scaley scalez scale3d rotate rotatex rotatey rotatez rotate3d
skew skewx skewy perspective
""".split())
_SELECTOR_FUNCTIONS = frozenset(
    'is where not has nth-child nth-last-child nth-of-type nth-last-of-type lang dir'.split()
)


class _Rejected(ValueError):
    pass


def _preflight(css: str) -> bool:
    """Bound nesting *before* the recursive parser; reject its EOF repairs.

    This small lexical guard is not the sanitizer: the parsed token tree below
    is the allowlist authority. It tracks only delimiters, comments and strings.
    tinycss2 intentionally repairs unterminated blocks/strings/comments at EOF,
    which we instead reject to avoid saving surprising, rewritten CSS.
    """
    stack: list[str] = []
    quote = ''
    i = 0
    while i < len(css):
        char = css[i]
        if char == '\\':
            if i + 1 == len(css):
                return False
            if not quote and css[i + 1] in '\r\n\f':
                return False
            # CRLF is one escaped newline in a string.
            i += 3 if css[i + 1:i + 3] == '\r\n' else 2
            continue
        if quote:
            if char == quote:
                quote = ''
            elif char in '\n\r\f':
                return False
            i += 1
            continue
        if css.startswith('/*', i):
            end = css.find('*/', i + 2)
            if end < 0:
                return False
            i = end + 2
            continue
        if char in '\"\'':
            quote = char
        elif char in '([{':
            stack.append(char)
            if len(stack) > MAX_NESTING:
                return False
        elif char in ')]}' and (
            not stack or stack.pop() != {')': '(', ']': '[', '}': '{'}[char]
        ):
            return False
        i += 1
    return not stack and not quote


def _check_tokens(tokens: list, context: str, budget: list[int], depth: int = 0) -> None:
    if depth > MAX_NESTING:
        raise _Rejected()
    for token in tokens:
        budget[0] -= 1
        if budget[0] < 0:
            raise _Rejected()
        kind = token.type
        value = getattr(token, 'value', '')
        if isinstance(value, str) and (
            '<' in value or any(ord(c) < 32 and c not in '\t\n\r\f' for c in value)
        ):
            raise _Rejected()
        if kind in ('whitespace', 'comment', 'ident', 'hash', 'string'):
            continue
        if kind in ('number', 'percentage', 'dimension'):
            if not math.isfinite(token.value):
                raise _Rejected()
            continue
        if kind == 'literal':
            allowed = ',/+ -*%' if context == 'value' else '.:#*>,+~|=^$-/ %'
            if context == 'media':
                allowed = ':,/=>+-'
            if token.value not in allowed:
                raise _Rejected()
            continue
        if kind == 'function':
            allowed_functions = _VALUE_FUNCTIONS if context == 'value' else _SELECTOR_FUNCTIONS
            if context == 'media' or token.lower_name not in allowed_functions:
                raise _Rejected()
            _check_tokens(token.arguments, context, budget, depth + 1)
            continue
        if kind == '() block' or (kind == '[] block' and context in ('selector', 'value')):
            # [] in values permits named grid lines. Its children still cannot
            # contain URLs, at-rules, arbitrary functions, or declaration syntax.
            _check_tokens(token.content, context, budget, depth + 1)
            continue
        # Includes URLToken (unquoted url), ParseError, at-keyword, and {} blocks.
        raise _Rejected()


def _meaningful(tokens: list) -> bool:
    return any(token.type not in ('whitespace', 'comment') for token in tokens)


def _rules(rules: list, budget: list[int], depth: int = 0) -> str:
    if depth > MAX_NESTING:
        raise _Rejected()
    result = []
    for rule in rules:
        budget[0] -= 1
        if budget[0] < 0:
            raise _Rejected()
        if rule.type == 'at-rule':
            if rule.lower_at_keyword != 'media' or rule.content is None or not _meaningful(rule.prelude):
                raise _Rejected()
            _check_tokens(rule.prelude, 'media', budget)
            children = tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True)
            content = _rules(children, budget, depth + 1)
            if content:
                result.append('@media ' + tinycss2.serialize(rule.prelude).strip() + '{' + content + '}')
        elif rule.type == 'qualified-rule':
            if not _meaningful(rule.prelude):
                raise _Rejected()
            _check_tokens(rule.prelude, 'selector', budget)
            declarations = tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True)
            for declaration in declarations:
                budget[0] -= 1
                if budget[0] < 0 or declaration.type != 'declaration' or declaration.lower_name not in _PROPERTIES:
                    raise _Rejected()
                if not _meaningful(declaration.value):
                    raise _Rejected()
                _check_tokens(declaration.value, 'value', budget)
            if declarations:
                result.append(tinycss2.serialize(rule.prelude).strip() + '{' + tinycss2.serialize(declarations) + '}')
        else:
            raise _Rejected()
    return ''.join(result)


def sanitize_css(css: str) -> str:
    """Return supported CSS, or ``''`` on any unsupported/unsafe input.

    No network or application state is accessed. Safe to call both before saving
    user CSS and again when rendering historical values into a style element.
    """
    if not isinstance(css, str) or len(css) > MAX_CSS_LENGTH:
        return ''
    if '<' in css or any(ord(c) < 32 and c not in '\t\n\r\f' for c in css):
        return ''
    if not _preflight(css):
        return ''
    try:
        rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
        result = _rules(rules, [MAX_TOKENS])
        # Defensive HTML boundary check on actual serialized output as well.
        if '<' in result or len(result) > MAX_CSS_LENGTH:
            return ''
        return result
    except (ValueError, RecursionError):
        return ''
