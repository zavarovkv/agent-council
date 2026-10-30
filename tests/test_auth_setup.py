import tempfile
import unittest
from pathlib import Path

from scripts.configure_telegram import deployment_origin


class AuthSetupTests(unittest.TestCase):
    def test_docker_setup_requires_its_explicit_origin_instead_of_a_legacy_server_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "compose.env"
            path.write_text("HERMES_PUBLIC_URL=https://council.example.test/\n")
            self.assertEqual(deployment_origin(path), "https://council.example.test")
            for value in (
                "",
                "http://example.test",
                "https://user:secret@example.test",
                "https://example.test/path",
                "https://example.test?next=other",
                "https://example.test#other",
            ):
                path.write_text("HERMES_PUBLIC_URL=" + value + "\n")
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    deployment_origin(path)
