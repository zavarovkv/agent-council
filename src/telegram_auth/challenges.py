"""Bounded, short-lived, single-use challenges for the Telegram Login popup."""

import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass


class ChallengeRejected(ValueError):
    pass


@dataclass(frozen=True)
class Challenge:
    expires: float
    next_path: str


class ChallengeStore:
    def __init__(self, *, clock=time.monotonic, ttl=600, capacity=512, per_minute=20):
        self.clock = clock
        self.ttl = ttl
        self.capacity = capacity
        self.per_minute = per_minute
        self.pending = {}
        self.rates = {}
        self.lock = threading.Lock()

    def issue(self, *, client: str, next_path: str) -> str:
        with self.lock:
            now = self.clock()
            self.pending = {
                key: value for key, value in self.pending.items() if value.expires > now
            }
            for key in list(self.rates):
                bucket = self.rates[key]
                while bucket and bucket[0] <= now - 60:
                    bucket.popleft()
                if not bucket:
                    del self.rates[key]
            if len(self.pending) >= self.capacity or (
                client not in self.rates and len(self.rates) >= self.capacity
            ):
                raise ChallengeRejected("Login capacity reached.")
            bucket = self.rates.setdefault(client, deque())
            if len(bucket) >= self.per_minute:
                raise ChallengeRejected("Too many login attempts.")
            bucket.append(now)
            nonce = secrets.token_urlsafe(32)
            self.pending[nonce] = Challenge(now + self.ttl, next_path)
            return nonce

    def consume(self, nonce: str) -> Challenge:
        with self.lock:
            challenge = self.pending.pop(nonce, None)
            if challenge is None or challenge.expires <= self.clock():
                raise ChallengeRejected("Login challenge expired or already used.")
            return challenge
