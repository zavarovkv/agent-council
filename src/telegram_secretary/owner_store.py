"""Private durable guest receipts. Started work is never automatically resubmitted."""

import json
import time

from .guest_store import GuestStore


class OwnerStore(GuestStore):
    def __init__(self, path, *, clock=time.time):
        super().__init__(path, clock=clock)
        with self.database() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS owner_jobs (
                id TEXT PRIMARY KEY, epoch TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, inline_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '', created REAL NOT NULL
            )""")

    def admit(self, job, epoch):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM owner_jobs WHERE created<?", (self.clock() - 86400,))
            if db.execute("SELECT 1 FROM owner_jobs WHERE id=?", (job["id"],)).fetchone():
                return False
            if (
                db.execute(
                    "SELECT count(*) FROM owner_jobs WHERE status IN ('ack','running')"
                ).fetchone()[0]
                >= 1
            ):
                job["notice"] = "busy"
            if (
                db.execute(
                    "SELECT count(*) FROM owner_jobs WHERE created>?", (self.clock() - 3600,)
                ).fetchone()[0]
                >= 60
            ):
                return False
            db.execute(
                "INSERT INTO owner_jobs(id,epoch,payload,status,created) VALUES(?,?,?,'ack',?)",
                (job["id"], epoch, json.dumps(job), self.clock()),
            )
            return True

    def update(self, identifier, *, status=None, inline_id=None, session_id=None):
        values = {
            key: value
            for key, value in {
                "status": status,
                "inline_id": inline_id,
                "session_id": session_id,
            }.items()
            if value is not None
        }
        if status and status not in {"ack", "running", "done", "uncertain", "cancelled"}:
            raise ValueError("Invalid owner job state")
        if status in {"done", "uncertain", "cancelled"}:
            values["payload"] = "{}"
        with self.database() as db:
            db.execute(
                "UPDATE owner_jobs SET " + ",".join(key + "=?" for key in values) + " WHERE id=?",
                (*values.values(), identifier),
            )

    def interrupted(self):
        with self.database() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM owner_jobs WHERE status IN ('ack','running')")
            ]
