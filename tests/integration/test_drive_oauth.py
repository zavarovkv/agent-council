"""The pinned Google SDK must request only Drive with PKCE and a loopback callback."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from google.oauth2.credentials import Credentials

SPEC = importlib.util.spec_from_file_location(
    "council_drive", "/opt/agent-council/configure-drive.py"
)
drive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drive)


class DriveOAuthIntegrationTests(unittest.TestCase):
    def test_expanded_scopes_keep_drive_and_exclude_unrequested_google_services(self):
        scopes = drive.requested_scopes(["calendar", "gmail", "analytics"])
        self.assertIn("https://www.googleapis.com/auth/drive", scopes)
        self.assertIn("https://www.googleapis.com/auth/gmail.modify", scopes)
        self.assertIn("https://www.googleapis.com/auth/calendar.events", scopes)
        self.assertIn("https://www.googleapis.com/auth/analytics.readonly", scopes)
        self.assertFalse(
            any(
                "contacts" in s or "analytics.edit" in s or s == "https://mail.google.com/"
                for s in scopes
            )
        )

    def test_failed_expansion_keeps_previous_grant_and_wrong_account_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "council-workspace.json").write_text(
                json.dumps({"files": {"provider": "google_drive", "folder_id": "folder-fixture"}})
            )
            previous = Credentials(
                "old-token",
                refresh_token="old-refresh",
                token_uri="https://oauth2.googleapis.com/token",
                client_id="fixture",
                client_secret="fixture-secret",
                scopes=drive.SCOPES,
            )
            drive.save_credentials(home, previous)
            original = (home / "google_token.json").read_bytes()
            expanded = Credentials(
                "new-token",
                refresh_token="new-refresh",
                token_uri="https://oauth2.googleapis.com/token",
                client_id="fixture",
                client_secret="fixture-secret",
                scopes=drive.requested_scopes(["calendar"]),
            )
            api, flow = MagicMock(), MagicMock()
            api.about().get().execute.return_value = {"user": {"emailAddress": "owner@example.com"}}
            flow.run_local_server.return_value = expanded
            with (
                patch("googleapiclient.discovery.build", return_value=api),
                patch.object(drive, "build_flow", return_value=flow),
                patch.object(Credentials, "refresh"),
                patch.object(drive, "check_services", side_effect=drive.SetupError("API disabled")),
            ):
                with self.assertRaises(drive.SetupError):
                    drive.authorize(home, ["calendar"])
            self.assertEqual((home / "google_token.json").read_bytes(), original)
            with (
                patch("googleapiclient.discovery.build", return_value=api),
                self.assertRaisesRegex(drive.SetupError, "same Google account"),
            ):
                drive.check_services(home, expanded, ["calendar"], "different@example.com")

    def test_real_sdk_uses_drive_only_pkce_and_no_secret_in_authorization_url(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            home = Path(directory)
            fixture = {
                "installed": {
                    "client_id": "fake.apps.googleusercontent.com",
                    "client_secret": "fixture-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
            drive.import_client(home, json.dumps(fixture).encode())
            flow = drive.build_flow(home)
            flow.redirect_uri = f"http://127.0.0.1:{drive.CALLBACK_PORT}/"
            url, state = flow.authorization_url(access_type="offline", prompt="consent")
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            self.assertEqual(parsed.hostname, "accounts.google.com")
            self.assertEqual(query["scope"], ["https://www.googleapis.com/auth/drive"])
            self.assertEqual(query["state"], [state])
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertTrue(query["code_challenge"][0])
            self.assertEqual(query["access_type"], ["offline"])
            self.assertEqual(query["redirect_uri"], [flow.redirect_uri])
            self.assertNotIn("fixture-secret", url)
            self.assertFalse((home / "google_token.json").exists())
