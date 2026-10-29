import os
import tempfile
import unittest
from pathlib import Path

from scripts.env_utils import parse_environment, render_environment, write_environment


class EnvironmentTests(unittest.TestCase):
    def test_raw_credentials_preserve_interpolation_and_quote_characters(self):
        values = {"SECRET": 'literal$HOME${OTHER} "quotes" \\ slash #hash =value'}
        self.assertEqual(parse_environment(render_environment(values, raw=True), raw=True), values)

    def test_raw_credentials_can_start_with_a_quote(self):
        values = {"SECRET": '"literal-leading-quote'}
        self.assertEqual(parse_environment(render_environment(values, raw=True), raw=True), values)

    def test_legacy_quoted_values_are_converted_without_variable_expansion(self):
        values = {"SECRET": 'literal$HOME "quoted" \\path'}
        legacy = render_environment(values, raw=False)
        self.assertEqual(parse_environment(legacy, raw=False), values)

    def test_missing_values_and_comments_are_supported(self):
        self.assertEqual(parse_environment("# placeholder\nOWNER=\n", raw=True), {"OWNER": ""})

    def test_control_characters_cannot_inject_environment_entries(self):
        for value in ("a\nINJECTED=yes", "a\rb", "a\0b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                render_environment({"SECRET": value}, raw=True)

    def test_invalid_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            render_environment({"BAD=KEY": "value"}, raw=True)

    def test_atomic_replacement_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telegram.env"
            path.write_text("OLD=yes\n", encoding="utf-8")
            write_environment(path, {"SECRET": "new$value"}, raw=True)
            self.assertEqual(path.read_text(encoding="utf-8"), "SECRET=new$value\n")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
