import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from council_ops.doctor import secretary_status
from telegram_secretary.policy import Settings
from telegram_secretary.store import Store


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.pin = self.home / "telegram-owner.json"
        self.pin.write_text(json.dumps({"telegram_id": 12345}))
        self.environment = {
            "HERMES_HOME": str(self.home),
            "HERMES_TELEGRAM_ALLOWED_ID": "12345",
            "COUNCIL_SECRETARY_OWNER_ID": "12345",
            "COUNCIL_SECRETARY_KEY": "a" * 64,
            "COUNCIL_SECRETARY_ENABLED": "true",
            "COUNCIL_SECRETARY_PUBLIC_DMS": "true",
        }

    def test_one_verified_owner_policy_drives_both_external_routes_and_hides_the_key(self):
        settings = Settings.load(self.environment)
        self.assertTrue(settings.enabled and settings.public_enabled)
        self.assertEqual(settings.owner, 12345)
        self.assertNotIn("a" * 64, repr(settings))
        disabled = Settings.load({**self.environment, "COUNCIL_SECRETARY_ENABLED": "false"})
        self.assertEqual(disabled.owner, 12345)
        self.assertFalse(disabled.enabled or disabled.public_enabled)

    def test_owner_mismatch_corruption_and_symlinks_cannot_enable_public_execution(self):
        mismatch = Settings.load({**self.environment, "COUNCIL_SECRETARY_OWNER_ID": "54321"})
        self.assertIsNone(mismatch.owner)
        self.assertFalse(mismatch.public_enabled)
        for content in ("{broken", "[]", '{"telegram_id":true}', '{"telegram_id":-1}'):
            self.pin.write_text(content)
            settings = Settings.load(self.environment)
            self.assertIsNone(settings.owner)
            self.assertFalse(settings.enabled or settings.public_enabled)
        self.pin.unlink()
        self.pin.symlink_to(self.home / "missing")
        settings = Settings.load(self.environment)
        self.assertIsNone(settings.owner)
        self.assertFalse(settings.enabled or settings.public_enabled)

    def test_invalid_conversation_preferences_close_external_routes_and_preserve_owner_route(self):
        for key, value in (
            ("COUNCIL_SECRETARY_CONTACT_IDS", "@friend"),
            ("COUNCIL_SECRETARY_SPOUSE_USERNAME", "https://t.me/friend"),
        ):
            settings = Settings.load({**self.environment, key: value})
            self.assertEqual(settings.owner, 12345)
            self.assertFalse(settings.enabled or settings.public_enabled)
            self.assertTrue(settings.error)

    def test_operator_diagnostics_report_queue_health_without_exposing_messages_or_credentials(
        self,
    ):
        root = self.home / "plugin-data/council-secretary"
        store = Store(root / "queue.sqlite3", clock=lambda: 1000)
        store.connection("connection", True)
        store.submit("request", "connection", 67890, 1, "private phrase", 1000)
        Store(root / "public-queue.sqlite3", clock=lambda: 1000)
        inspected = {
            "Config": {"Env": [f"{key}={value}" for key, value in self.environment.items()]}
        }
        settings = SimpleNamespace(data_dir=self.home)
        before = store.path.read_bytes()
        report = secretary_status(settings, inspected)
        self.assertTrue(report["policy_valid"])
        self.assertEqual(report["queues"]["business"]["counts"], {"queued": 1})
        self.assertEqual(store.path.read_bytes(), before)
        for private in ("private phrase", "a" * 64, "67890", "12345"):
            self.assertNotIn(private, json.dumps(report))
        store.path.write_bytes(b"corrupt")
        degraded = secretary_status(settings, inspected)
        self.assertFalse(degraded["queues"]["business"]["available"])
        self.assertTrue(degraded["queues"]["public"]["available"])
