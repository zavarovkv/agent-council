"""Persistent scheduling, separate from execution progress and model admission."""

import hashlib


class Recovery:
    def __init__(self, store):
        self.store = store

    def get(self, key):
        with self.store.database() as db:
            row = db.execute("SELECT * FROM recovery WHERE key=?", (key,)).fetchone()
            return dict(row) if row else {"next_at": 0, "failures": 0, "category": ""}

    def due(self, key):
        return self.get(key)["next_at"] <= self.store.clock()

    def success(self, key, *, delay=0):
        with self.store.database() as db:
            db.execute(
                "INSERT INTO recovery(key,next_at,failures,category,updated) VALUES(?,?,0,'',?) "
                "ON CONFLICT(key) DO UPDATE SET next_at=excluded.next_at,failures=0,category='',updated=excluded.updated",
                (key, self.store.clock() + delay, self.store.clock()),
            )

    def failure(self, key, error):
        with self.store.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT failures FROM recovery WHERE key=?", (key,)).fetchone()
            failures = (row[0] if row else 0) + 1
            # Stable jitter spreads independent work without making tests nondeterministic.
            jitter = int(hashlib.sha256(key.encode()).hexdigest()[:4], 16) % 7
            next_at = max(
                self.store.clock() + min(300, 10 * 2 ** min(failures - 1, 5)) + jitter,
                error.retry_at,
            )
            category = "rate_limited" if error.status == 429 else "integration_unavailable"
            db.execute(
                "INSERT INTO recovery VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "next_at=excluded.next_at,failures=excluded.failures,category=excluded.category,updated=excluded.updated",
                (key, next_at, failures, category, self.store.clock()),
            )
            return next_at
