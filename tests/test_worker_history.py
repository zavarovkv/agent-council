import hashlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from secretary_worker.instructions import load_instructions
from secretary_worker.protocol import ReplyRequest
from secretary_worker.service import Service


def payload(number, text=None):
    return {
        "request_id": hashlib.sha256(str(number).encode()).hexdigest(),
        "conversation": "c" * 64,
        "text": text or f"turn {number}",
    }


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = temporary.name
        self.instructions = load_instructions(
            Path(__file__).resolve().parents[1] / "instructions/secretary"
        )
        self.calls = []

    def service(self, generate=None):
        def reply(messages):
            self.calls.append(messages)
            return "Fixture answer"

        return Service(
            self.home,
            "a" * 64,
            skills=self.instructions.skills,
            prompt=self.instructions.policy,
            generate=generate or reply,
            clock=lambda: 1000,
        )

    def test_equal_timestamps_keep_the_last_eight_delivered_turns_in_order(self):
        service = self.service()
        for number in range(1, 14):
            value = payload(number)
            service.reply(value)
            service.acknowledge({"request_id": value["request_id"]})
        questions = [message["content"] for message in self.calls[-1] if message["role"] == "user"]
        self.assertEqual(questions, [f"turn {number}" for number in range(5, 14)])

    def test_concurrent_instances_cannot_generate_the_same_request_twice(self):
        started, release = threading.Event(), threading.Event()

        def delayed(messages):
            self.calls.append(messages)
            started.set()
            release.wait(timeout=5)
            return "Fixture answer"

        first, second = self.service(delayed), self.service()
        value = payload(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.reply, value)
            try:
                self.assertTrue(started.wait(timeout=2))
                with self.assertRaises(BlockingIOError):
                    second.reply(value)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=2), "Fixture answer")
        self.assertEqual(second.reply(value), "Fixture answer")
        self.assertEqual(len(self.calls), 1)

    def test_protocol_rejects_nonobjects_and_omits_private_text_from_diagnostics(self):
        for invalid in (None, [], "string", {**payload(1), "text": "  "}):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                ReplyRequest.parse(invalid)
        self.assertNotIn("private phrase", repr(ReplyRequest.parse(payload(1, "private phrase"))))

    def test_slow_public_generation_cannot_occupy_the_reserved_business_slot(self):
        started, release = threading.Event(), threading.Event()

        def delayed(messages):
            if messages[-1]["content"] == "slow public":
                started.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("Fixture did not release generation")
            return "Fixture answer"

        service = self.service(delayed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(service.reply, {**payload(1, "slow public"), "route": "public"})
            try:
                self.assertTrue(started.wait(timeout=2))
                with self.assertRaises(BlockingIOError):
                    service.reply({**payload(2), "route": "public"})
                self.assertEqual(
                    service.reply({**payload(3), "route": "business"}), "Fixture answer"
                )
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=2), "Fixture answer")

    def test_public_spend_limit_reserves_business_budget_across_restarts(self):
        service = self.service()
        for number in range(40):
            service.reply(
                {
                    **payload(number),
                    "conversation": hashlib.sha256(str(number).encode()).hexdigest(),
                }
            )
        restarted = self.service()
        with self.assertRaises(BlockingIOError):
            restarted.reply({**payload(100), "conversation": "d" * 64})
        self.assertEqual(restarted.reply({**payload(101), "route": "business"}), "Fixture answer")
        with self.assertRaises(ValueError):
            restarted.reply({**payload(101), "route": "public"})
        with self.assertRaises(ValueError):
            restarted.reply({**payload(102), "route": "owner"})

    def test_legacy_history_is_preserved_and_cannot_claim_business_budget(self):
        path = Path(self.home) / "secretary-history.sqlite3"
        request = ReplyRequest.parse(payload(1))
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE turns (request_id TEXT PRIMARY KEY, conversation TEXT, "
                "question TEXT, answer TEXT, created REAL, delivered INTEGER)"
            )
            db.execute(
                "INSERT INTO turns VALUES (?,?,?,?,?,?)",
                (request.identifier, request.context, request.text, "Existing answer", 1000, 1),
            )
        service = self.service()
        self.assertEqual(service.reply(payload(1)), "Existing answer")
        self.assertFalse(self.calls)
        with self.assertRaises(ValueError):
            service.reply({**payload(1), "route": "business"})
