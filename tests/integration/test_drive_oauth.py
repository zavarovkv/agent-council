"""The pinned Google SDK must request only Drive with PKCE and a loopback callback."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SPEC = importlib.util.spec_from_file_location(
    "council_drive", "/opt/agent-council/configure-drive.py"
)
drive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drive)


class DriveOAuthIntegrationTests(unittest.TestCase):
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
