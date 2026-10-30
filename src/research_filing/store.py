"""Durable filing steps, separate from native task/run state."""

import json
import sqlite3
import time
from contextlib import contextmanager


class FilingError(RuntimeError):
    """A safe failure category; never includes provider responses or report text."""


class Store:
    def __init__(self, home):
        self.root = home / ".council/research-filing"
        if self.root.resolve() != self.root:
            raise FilingError("invalid_state_path")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "outbox.sqlite3"
        if self.path.is_symlink():
            raise FilingError("invalid_state_path")
        with self.database() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS filings (id TEXT PRIMARY KEY, state TEXT NOT NULL, "
                "created REAL NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL)"
            )
        self.path.chmod(0o600)

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, identifier):
        with self.database() as db:
            row = db.execute("SELECT payload FROM filings WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, record):
        record = {**record, "updated_at": time.time()}
        with self.database() as db:
            db.execute(
                "INSERT OR REPLACE INTO filings VALUES (?,?,?,?,?)",
                (
                    record["id"],
                    record["state"],
                    record["created_at"],
                    record["updated_at"],
                    json.dumps(record),
                ),
            )
        return record

    def stage(self, record):
        previous = self.get(record["id"])
        if previous is not None:
            if previous["parent"] != record["parent"]:
                raise FilingError("destination_changed")
            if previous["sha256"] == record["sha256"] and previous["run_id"] == record["run_id"]:
                return previous
            if previous["state"] != "done":
                raise FilingError("reconcile_pending_version_first")
            record.update(
                page_id=previous["page_id"],
                previous_body=previous["body"],
                previous_remote_snapshot=previous.get("remote_snapshot"),
                index_snapshot=previous.get("index_snapshot"),
            )
        return self.save(
            {
                "page_id": None,
                "previous_body": None,
                "index_attempted": False,
                "index_bootstrap_attempted": False,
                "created_at": time.time(),
                **record,
                "state": "queued",
            }
        )
