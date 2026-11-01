"""Metadata invariants; the pinned-image tests exercise actual safe YAML decoding."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from council_skills import metadata


class SkillMetadataTests(unittest.TestCase):
    def test_decoded_types_names_and_description_are_checked(self):
        content = "---\nname: example\ndescription: A skill\n---\nBody"
        for value in (
            [],
            None,
            {"name": "example", "description": True},
            {"name": "different", "description": "A skill"},
            {"name": "example", "description": " "},
            {"name": "example", "description": "x" * 1025},
        ):
            with self.subTest(value=value), patch("council_skills.load_yaml", return_value=value):
                with self.assertRaises(ValueError):
                    metadata(content, "example")
        for name in ("a--b", "-name", "name-", "UPPER", "a" * 65):
            with (
                self.subTest(name=name),
                patch(
                    "council_skills.load_yaml",
                    return_value={"name": name, "description": "A skill"},
                ),
                self.assertRaises(ValueError),
            ):
                metadata(content, name)

    def test_frontmatter_boundaries_body_and_utf8_byte_limit(self):
        with patch(
            "council_skills.load_yaml",
            return_value={"name": "example", "description": " A skill\n"},
        ) as loader:
            result = metadata("---\nname: example\ndescription: A skill---\n---\nBody", "example")
            self.assertEqual(result["description"], "A skill")
            loader.assert_called_once_with("name: example\ndescription: A skill---")
            for content in (
                "prefix---\nname: example\n---\nBody",
                "---\nname: example",
                "---\nname: example\n---\n   ",
                "я" * 32769,
            ):
                with self.subTest(content=content[:50]), self.assertRaises(ValueError):
                    metadata(content, "example")
