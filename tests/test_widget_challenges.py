"""Challenges expire, are single-use under concurrency, and have bounded capacity."""

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telegram_auth.challenges import ChallengeRejected, ChallengeStore


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.store = ChallengeStore(clock=lambda: self.now, ttl=10, capacity=3, per_minute=2)

    def test_challenge_is_single_use(self):
        nonce = self.store.issue(client="one", next_path="/sessions")
        self.assertGreaterEqual(len(nonce), 40)
        self.assertEqual(self.store.consume(nonce).next_path, "/sessions")
        with self.assertRaises(ChallengeRejected):
            self.store.consume(nonce)

    def test_expired_challenge_cannot_be_used(self):
        nonce = self.store.issue(client="one", next_path="/")
        self.now += 10
        with self.assertRaises(ChallengeRejected):
            self.store.consume(nonce)

    def test_capacity_is_bounded_and_expired_entries_are_reclaimed(self):
        for client in ("one", "two", "three"):
            self.store.issue(client=client, next_path="/")
        with self.assertRaises(ChallengeRejected):
            self.store.issue(client="four", next_path="/")
        self.now += 61
        nonce = self.store.issue(client="four", next_path="/")
        self.assertEqual(self.store.consume(nonce).next_path, "/")

    def test_client_rate_limit_expires(self):
        for _ in range(2):
            nonce = self.store.issue(client="one", next_path="/")
            self.store.consume(nonce)
        with self.assertRaises(ChallengeRejected):
            self.store.issue(client="one", next_path="/")
        self.now += 61
        self.store.issue(client="one", next_path="/")

    def test_concurrent_consumption_has_one_winner(self):
        nonce = self.store.issue(client="one", next_path="/")

        def consume(_):
            try:
                self.store.consume(nonce)
                return True
            except ChallengeRejected:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(consume, range(8))), 1)
