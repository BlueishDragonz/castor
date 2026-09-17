"""Standalone defensive tests: python -m unittest tests.test_lane2_css -v.

Fixtures are inert CSS/markers; no executable scripts or remote resources.
"""
import unittest
from unittest.mock import patch

from beaverhabits.css_sanitizer import MAX_CSS_LENGTH, MAX_NESTING, sanitize_css


class CssSanitizerTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(sanitize_css(""), "")
        self.assertEqual(sanitize_css(" /* note */ "), "")

    def test_practical_styles(self):
        css = '.card, #main > p:hover {color: #123abc; font-family: "Open Sans", sans-serif; font-size: 1.2rem; display: grid; grid-template-columns: 1fr 2fr; gap: 8px; margin: 0 auto; border: 1px solid red; background: linear-gradient(red, blue);}'
        result = sanitize_css(css)
        for part in ('#123abc', '"Open Sans"', 'grid-template-columns', 'linear-gradient'):
            self.assertIn(part, result)

    def test_allowed_functions(self):
        css = '.a {width: calc(100% - 2rem); padding: clamp(1px, 2vw, 8px); color: rgba(1, 2, 3, .5); background-color: hsl(10, 20%, 30%); transform: translateX(2px);}'
        self.assertIn('calc(', sanitize_css(css))
        self.assertIn('translateX(', sanitize_css(css))

    def test_media_queries(self):
        css = '@media screen and (max-width: 640px) {.a {display: flex;}}'
        self.assertIn('@media screen', sanitize_css(css))
        self.assertIn('display: flex', sanitize_css(css))

    def test_selector_functions_and_attributes(self):
        self.assertTrue(sanitize_css('.a:not(.b) > [data-kind="note"] {color: red;}'))

    def test_important_and_comments(self):
        result = sanitize_css('.a {/* inert */ color: red !important;}')
        self.assertIn('!important', result)
        self.assertNotIn('inert', result)

    def test_url_tokens_rejected(self):
        for value in ('url()', 'url("")', 'URL("")', r'u\72l("")'):
            with self.subTest(value=value):
                self.assertEqual(sanitize_css('.a {background: ' + value + ';}'), '')

    def test_non_media_at_rules_rejected(self):
        for css in ('@import "";', '@font-face {font-family: note;}', '@supports (display: grid) {.a {color: red;}}', '@keyframes note {from {opacity: 0;}}', '.a {@media screen {color: red;}}'):
            with self.subTest(css=css):
                self.assertEqual(sanitize_css(css), '')

    def test_unsupported_properties_and_functions(self):
        for declaration in ('behavior: none', '-moz-binding: none', 'unknown-property: red', '--note: red', 'color: var(--note)', 'width: expression(0)', 'background: image-set("")', 'color: attr(data-note)', 'filter: blur(1px)'):
            with self.subTest(declaration=declaration):
                self.assertEqual(sanitize_css('.a {' + declaration + ';}'), '')

    def test_html_termination_markers_rejected(self):
        for marker in ('</style>', '</STYLE >', '<!-- note -->'):
            with self.subTest(marker=marker):
                self.assertEqual(sanitize_css('.a {font-family: "' + marker + '";}'), '')

    def test_escaped_html_marker_rejected(self):
        self.assertEqual(sanitize_css(r'.a {font-family: "\3c /style>";}'), '')

    def test_malformed_css_rejected(self):
        for css in ('.a {color: red', '.a {color red;}', '.a {color: ;}', '.a {color: red;}}', '.a {width: calc(2px;}', '.a {font-family: "note;}', '.a {color: red;} /* note', 'color: red;', '{color: red;}', '.a {color: red; broken}', '.a {color: red; nested {color: blue;}}'):
            with self.subTest(css=css):
                self.assertEqual(sanitize_css(css), '')

    def test_one_invalid_rule_rejects_whole_input(self):
        self.assertEqual(sanitize_css('.a {color: red;} .b {background: url();}'), '')

    def test_input_bound_checked_before_parser(self):
        with patch('beaverhabits.css_sanitizer.tinycss2.parse_stylesheet') as parser:
            self.assertEqual(sanitize_css(' ' * (MAX_CSS_LENGTH + 1)), '')
            parser.assert_not_called()

    def test_nesting_bound_checked_before_parser(self):
        css = '.a {width: ' + 'calc(' * (MAX_NESTING + 1) + '1px' + ')' * (MAX_NESTING + 1) + ';}'
        with patch('beaverhabits.css_sanitizer.tinycss2.parse_stylesheet') as parser:
            self.assertEqual(sanitize_css(css), '')
            parser.assert_not_called()

    def test_token_budget(self):
        with patch('beaverhabits.css_sanitizer.MAX_TOKENS', 4):
            self.assertEqual(sanitize_css('.a {color: red; margin: 1px;}'), '')

    def test_escaped_supported_names(self):
        result = sanitize_css(r'.a {c\6flor: r\65 d;}')
        self.assertIn('color', result)
        self.assertIn('red', result)

    def test_parse_errors_inside_selector_and_value(self):
        for css in ('.a:lang("note\n") {color: red;}', '.a {color: rgb(1, 2, ]);}', '.a {width: 1e999px;}'):
            with self.subTest(css=css):
                self.assertEqual(sanitize_css(css), '')

    def test_grid_named_lines_and_empty_rules(self):
        self.assertTrue(sanitize_css('.a {grid-template-columns: [start] 1fr [end];}'))
        self.assertEqual(sanitize_css('.a {} @media screen {}'), '')

    def test_nested_media_queries(self):
        self.assertTrue(sanitize_css('@media screen {@media (min-width: 1px) {.a {color: red;}}}'))

    def test_idempotence(self):
        css = '@media screen and (min-width: 1px) {.a:hover { color: rgb(1, 2, 3); padding: 2px!important; }}'
        result = sanitize_css(css)
        self.assertTrue(result)
        self.assertEqual(sanitize_css(result), result)

    def test_non_string_and_control_input(self):
        for css in (None, 1, b'', '.a {color: r\x00ed;}'):
            with self.subTest(css=css):
                self.assertEqual(sanitize_css(css), '')


if __name__ == '__main__':
    unittest.main()
