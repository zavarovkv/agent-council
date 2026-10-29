"""Persistent sessions must survive process restarts, expire and be revocable."""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).parents[1] / "src/telegram_auth/sessions.py"
spec = importlib.util.spec_from_file_location("council_session_store", MODULE)
sessions = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sessions
spec.loader.exec_module(sessions)


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "dashboard-sessions.sqlite3"
        self.now = 1000
        self.store = sessions.SessionStore(self.path, "client", clock=lambda: self.now)
        self.claims = {"sub": "owner", "id": 12345, "iss": "https://oauth.telegram.org"}

    def test_restart_keeps_session_but_not_raw_token_or_telegram_nonce_on_disk(self):
        token, issued = self.store.issue({**self.claims, "nonce": "private-login-nonce"})
        restarted = sessions.SessionStore(self.path, "client", clock=lambda: self.now)
        self.assertEqual(restarted.lookup(token), issued)
        self.assertEqual(issued.expires_at, self.now + 30 * 86400)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.path) as db:
            stored = json.dumps(db.execute("SELECT * FROM sessions").fetchall())
        self.assertNotIn(token, stored)
        self.assertNotIn("private-login-nonce", stored)

    def test_expiry_and_revocation_reject_previously_valid_tokens(self):
        token, _ = self.store.issue(self.claims)
        self.now += 30 * 86400
        self.assertIsNone(self.store.lookup(token))
        token, _ = self.store.issue(self.claims)
        self.store.revoke(token)
        self.assertIsNone(self.store.lookup(token))

    def test_other_client_and_forged_tokens_are_rejected(self):
        token, _ = self.store.issue(self.claims)
        foreign = sessions.SessionStore(self.path, "foreign", clock=lambda: self.now)
        self.assertIsNone(foreign.lookup(token))
        for forged in ("", "invalid", token[:-1], token + "A", None):
            self.assertIsNone(self.store.lookup(forged))

    def test_corrupted_database_fails_closed_and_invalid_lifetime_is_rejected(self):
        token, _ = self.store.issue(self.claims)
        self.path.write_bytes(b"corrupt")
        with self.assertRaises(sessions.SessionStoreError):
            self.store.lookup(token)
        for days in (0, 91):
            with self.assertRaises(ValueError):
                sessions.SessionStore(self.path, "client", days=days)
