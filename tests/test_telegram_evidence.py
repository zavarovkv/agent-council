import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_workspace import config

from council_workspace import telegram_source
from council_workspace.service import Workspace


class TelegramEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.policy, _ = config(self.home)
        self.pin = self.home / ".council/telegram-reader.json"
        self.pin.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "managed_by": "agent-council",
                    "source": "chat_hotel",
                    "peer_id": 123,
                    "owner_id": 456,
                }
            )
        )
        self.pin.chmod(0o600)
        self.root = self.home / "evidence"
        self.root.mkdir()
        self.file = self.root / "2026-11-02.json"
        self.value = {
            "schema": 1,
            "source": "chat_hotel",
            "peer_id": 123,
            "generated_at": "2026-11-02T04:16:00+00:00",
            "window_start": "2026-11-01T04:15:00+00:00",
            "window_end": "2026-11-02T04:15:00+00:00",
            "status": "incomplete",
            "coverage": "partial_text_window",
            "messages": [
                {
                    "id": i,
                    "date": "2026-11-01T12:00:00+00:00",
                    "text": "я" * 8192,
                    "text_truncated": False,
                    "private_extra": "not exposed",
                }
                for i in range(1, 9)
            ],
        }
        self.save()

    def save(self):
        self.file.write_text(json.dumps(self.value))

    def read(self, **args):
        return telegram_source.read(self.home, args, root=self.root)

    def test_bounded_pages_preserve_scope_and_do_not_expose_extra_fields(self):
        listing = self.read(action="telegram_list")
        self.assertEqual(listing["artifacts"][0]["day"], "2026-11-02")
        first = self.read(action="telegram_read", id="2026-11-02")
        self.assertLess(len(json.dumps(first, ensure_ascii=False).encode()), 70000)
        self.assertEqual(first["coverage"], "partial_text_window")
        self.assertNotIn("peer_id", first)
        self.assertNotIn("private_extra", first["messages"][0])
        messages = first["messages"][:]
        page = first
        while page["next_offset"] is not None:
            page = self.read(
                action="telegram_read",
                id="2026-11-02",
                offset=page["next_offset"],
                revision=first["revision"],
            )
            messages.extend(page["messages"])
        self.assertEqual([m["id"] for m in messages], list(range(1, 9)))
        self.assertEqual(messages[0]["url"], "https://t.me/chat_hotel/1")

    def test_pin_and_revision_changes_reject_reads(self):
        first = self.read(action="telegram_read", id="2026-11-02")
        self.value["messages"][0]["text"] = "changed"
        self.save()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.read(action="telegram_read", id="2026-11-02", offset=1, revision=first["revision"])
        self.value["peer_id"] = 789
        self.save()
        with self.assertRaises(ValueError):
            self.read(action="telegram_read", id="2026-11-02")

    def test_paths_and_links_cannot_reach_account_data(self):
        with self.assertRaises(ValueError):
            self.read(action="telegram_read", id="../../config")
        self.file.unlink()
        self.file.symlink_to(self.pin)
        with self.assertRaises(ValueError):
            self.read(action="telegram_read", id="2026-11-02")

    def test_missing_access_is_not_an_empty_successful_scan(self):
        self.pin.unlink()
        result = self.read(action="telegram_list")
        self.assertFalse(result["configured"])
        self.assertEqual(result["reason"], "source_not_connected")
        self.assertNotIn("status", result)

    def test_revoked_and_public_clients_cannot_read_collected_messages(self):
        workspace = Workspace(self.home)
        with patch.object(telegram_source, "ROOT", self.root):
            for role in ("linear-qui-gon", "linear-dexter", "linear-lando", "linear-padme"):
                self.assertTrue(workspace.execute(role, {"action": "telegram_list"})["configured"])
            with self.assertRaises(PermissionError):
                workspace.execute("public", {"action": "telegram_list"})
            policy = json.loads(self.policy.read_text())
            del policy["clients"]["linear-dexter"]
            self.policy.write_text(json.dumps(policy))
            with self.assertRaises(PermissionError):
                workspace.execute("linear-dexter", {"action": "telegram_read", "id": "2026-11-02"})

    def test_invalid_dates_and_outside_window_messages_are_rejected(self):
        self.value["messages"][0]["date"] = "2026-10-31T12:00:00+00:00"
        self.save()
        with self.assertRaises(ValueError):
            self.read(action="telegram_list")


if __name__ == "__main__":
    unittest.main()
