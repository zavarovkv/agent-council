"""Access changes preserve credentials and recover if a restart fails."""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import telegram_access
from env_utils import parse_environment, write_environment


class AccessManagementTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Path(directory.name) / "telegram.env"
        self.config.with_suffix(".env.format").write_text("raw\n", encoding="utf-8")
        self.original = {
            "HERMES_TELEGRAM_CLIENT_ID": "123456789",
            "HERMES_TELEGRAM_CLIENT_SECRET": 'fake$secret "quotes" \\ #literal',
            "HERMES_TELEGRAM_ALLOWED_ID": "12345",
            "HERMES_TELEGRAM_OWNER_USERNAME": "owner_account",
            "HERMES_TELEGRAM_ALLOWED_IDS": "54321",
        }
        write_environment(self.config, self.original, raw=True)
        self.output = io.StringIO()

    def command(self, *arguments, restart_results=(0,)):
        with (
            patch.object(telegram_access, "CONFIG", self.config),
            patch("os.geteuid", return_value=0),
            patch.object(sys, "argv", ["telegram_access.py", *arguments]),
            patch.object(telegram_access, "restart_hermes", side_effect=restart_results) as restart,
            contextlib.redirect_stdout(self.output),
            contextlib.redirect_stderr(self.output),
        ):
            telegram_access.main()
        return restart

    def read_values(self):
        return parse_environment(self.config.read_text(encoding="utf-8"), raw=True)

    def test_add_preserves_secrets_and_owner_settings(self):
        restart = self.command("add", "67890", "54321")
        expected = {**self.original, "HERMES_TELEGRAM_ALLOWED_IDS": "54321,67890"}
        self.assertEqual(self.read_values(), expected)
        restart.assert_called_once()
        self.assertNotIn(self.original["HERMES_TELEGRAM_CLIENT_SECRET"], self.output.getvalue())

    def test_remove_preserves_the_owner(self):
        self.command("remove", "54321")
        self.assertEqual(self.read_values(), {**self.original, "HERMES_TELEGRAM_ALLOWED_IDS": ""})

    def test_list_does_not_restart_or_expose_credentials(self):
        restart = self.command("list")
        restart.assert_not_called()
        self.assertIn("54321", self.output.getvalue())
        self.assertNotIn(self.original["HERMES_TELEGRAM_CLIENT_SECRET"], self.output.getvalue())

    def test_invalid_id_cannot_change_access(self):
        with self.assertRaises(SystemExit):
            self.command("add", "0")
        self.assertEqual(self.read_values(), self.original)

    def test_failed_restart_restores_previous_access_settings(self):
        with self.assertRaises(SystemExit):
            self.command("add", "67890", restart_results=(1, 0))
        self.assertEqual(self.read_values(), self.original)

    def test_duplicate_add_does_not_restart_hermes(self):
        restart = self.command("add", "54321")
        restart.assert_not_called()
        self.assertEqual(self.read_values(), self.original)
