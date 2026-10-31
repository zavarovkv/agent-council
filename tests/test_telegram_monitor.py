"""Closed-source collection never implies permission to send, join or inspect other chats."""

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telegram_monitor.collector import collect
from telegram_monitor.config import Settings
from telegram_monitor.evidence import (
    MAX_EVIDENCE_BYTES,
    daily_window,
    encode_evidence,
    select_context,
    write_evidence,
)
from telegram_monitor.setup import verify_source


class Channel:
    def __init__(self, identity=20, left=False):
        self.id, self.left = identity, left


class Reader:
    def __init__(self, messages):
        self.messages = messages
        self.account = SimpleNamespace(id=10, bot=False)
        self.peer = Channel()
        self.authorized = True
        self.history_calls = []

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.account

    async def get_entity(self, source):
        if source != "fixture_source":
            raise AssertionError("Unexpected source lookup")
        return self.peer

    async def iter_messages(self, peer, **options):
        if peer is not self.peer:
            raise AssertionError("Unexpected source history")
        self.history_calls.append(options)
        for message in self.messages:
            yield message


def settings():
    return Settings(1, "a" * 32, "1" + "x" * 200, 10, 20, "fixture_source")


NOW = datetime(2026, 10, 31, 4, 16, tzinfo=UTC)


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_reauthorization_rejects_a_reassigned_source_before_reading_a_message(self):
        reader = Reader([])
        reader.peer = Channel(99)
        with self.assertRaises(ValueError):
            await verify_source(reader, "fixture_source", expected_peer=20)

    async def test_exact_pinned_window_and_evidence_without_sender_data_or_write_calls(self):
        start, end = daily_window(NOW)
        messages = [
            SimpleNamespace(id=4, date=end, message="Agoda outside window"),
            SimpleNamespace(
                id=3, date=end - timedelta(minutes=1), message="Agoda announced a change"
            ),
            SimpleNamespace(id=2, date=start, message="Nearby context"),
            SimpleNamespace(id=1, date=start - timedelta(seconds=1), message="Booking old"),
        ]
        reader = Reader(messages)
        result = await collect(reader, settings(), NOW)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["coverage"], "full_text_window")
        self.assertEqual([m["id"] for m in result["messages"]], [2, 3])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(reader.history_calls[0]["offset_date"], end)
        self.assertNotIn("sender", json.dumps(result))

    async def test_reassigned_username_wrong_account_and_lost_membership_fail_closed(self):
        for field, value in (
            ("authorized", False),
            ("account", SimpleNamespace(id=99, bot=False)),
            ("account", SimpleNamespace(id=10, bot=True)),
            ("peer", Channel(99)),
            ("peer", Channel(left=True)),
            ("peer", SimpleNamespace(id=20, left=False)),
        ):
            reader = Reader([])
            setattr(reader, field, value)
            result = await collect(reader, settings(), NOW)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["coverage"], "unknown")
            self.assertFalse(reader.history_calls)

    async def test_limits_and_truncated_text_are_not_a_successful_empty_scan(self):
        start, end = daily_window(NOW)
        messages = [
            SimpleNamespace(id=i, date=end - timedelta(seconds=i), message="Long text " * 2000)
            for i in range(1, 5)
        ]
        reader = Reader(messages)
        result = await collect(reader, settings(), NOW, limit=2)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["coverage"], "partial_text_window")
        self.assertEqual(result["messages_seen"], 2)


class EvidenceTests(unittest.TestCase):
    def test_moscow_cutoff_does_not_depend_on_host_timezone(self):
        start, end = daily_window(NOW)
        self.assertEqual(end.isoformat(), "2026-10-31T04:15:00+00:00")
        self.assertEqual(end - start, timedelta(days=1))
        self.assertEqual(daily_window(NOW - timedelta(minutes=2))[1], end - timedelta(days=1))
        with self.assertRaises(ValueError):
            daily_window(NOW.replace(tzinfo=None))

    def test_candidate_context_is_bounded_and_keeps_recent_evidence(self):
        messages = [
            {"id": i, "date": str(i).zfill(3), "text": "Booking" if i in (2, 8) else "Context"}
            for i in range(10)
        ]
        selected, count, truncated = select_context(messages, limit=5)
        self.assertEqual(count, 2)
        self.assertTrue(truncated)
        self.assertEqual(selected[-1]["id"], 9)

    def test_small_windows_keep_nonkeyword_market_evidence(self):
        messages = [
            {
                "id": 1,
                "date": "2026-10-31",
                "text": "An unlisted hotel platform changed its pricing",
            }
        ]
        selected, count, truncated = select_context(messages)
        self.assertEqual(selected[0]["text"], messages[0]["text"])
        self.assertEqual(count, 0)
        self.assertFalse(truncated)

    def test_byte_budget_is_explicitly_partial_and_atomic_retention_preserves_unknown_files(self):
        value = {
            "schema": 1,
            "peer_id": 20,
            "window_end": "2026-10-31T04:15:00+00:00",
            "status": "complete",
            "coverage": "full_text_window",
            "messages": [{"id": i, "text": "Я" * 8192} for i in range(300)],
        }
        data = encode_evidence(value)
        self.assertLessEqual(len(data), MAX_EVIDENCE_BYTES)
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["messages"][-1]["id"], 299)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            unknown = root / "2026-10-01.json"
            unknown.write_text("unmanaged corrupt file")
            for day in range(2, 11):
                (root / f"2026-10-{day:02d}.json").write_text(
                    json.dumps({"schema": 1, "peer_id": 20})
                )
            path = write_evidence(root, value)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(unknown.exists())
            self.assertFalse((root / "2026-10-02.json").exists())
            self.assertEqual(json.loads(path.read_text())["messages"][-1]["id"], 299)

    def test_credentials_are_not_represented_and_source_is_explicit(self):
        self.assertNotIn("x" * 50, repr(settings()))
        with self.assertRaises(KeyError):
            Settings.load({})
