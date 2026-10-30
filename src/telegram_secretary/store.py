"""Private durable reply queue with explicit delivery ambiguity and revocation."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        if self.path.is_symlink():
            raise ValueError("Refusing a symlinked secretary queue.")
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pauses (connection TEXT, chat INTEGER, until REAL,
                    PRIMARY KEY(connection,chat));
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, connection TEXT NOT NULL, chat INTEGER NOT NULL,
                    message INTEGER NOT NULL, text TEXT NOT NULL, created REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued'
                );
                CREATE INDEX IF NOT EXISTS jobs_chat ON jobs(connection,chat,created);
            """)
            # Generation may be resumed idempotently. A possibly delivered send must never replay.
            db.execute("UPDATE jobs SET status='queued' WHERE status='working'")
            db.execute("UPDATE jobs SET status='uncertain' WHERE status='sending'")
            db.execute("DELETE FROM jobs WHERE created < ?", (clock() - 14 * 86400,))
        self.path.chmod(0o600)

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def connection(self, identifier, enabled):
        with self.database() as db:
            if enabled:
                db.execute(
                    "INSERT INTO connections VALUES (?,1) ON CONFLICT(id) DO UPDATE SET enabled=1",
                    (identifier,),
                )
            else:
                db.execute("UPDATE connections SET enabled=0 WHERE id=?", (identifier,))
                db.execute(
                    "UPDATE jobs SET status='cancelled' WHERE connection=? AND status IN ('queued','working')",
                    (identifier,),
                )

    def pause(self, connection, chat):
        with self.database() as db:
            db.execute(
                "INSERT INTO pauses VALUES (?,?,?) ON CONFLICT(connection,chat) DO UPDATE SET until=excluded.until",
                (connection, chat, self.clock() + 300),
            )
            db.execute(
                "UPDATE jobs SET status='cancelled' WHERE connection=? AND chat=? AND status IN ('queued','working')",
                (connection, chat),
            )

    def cancel(self, connection, chat, messages):
        with self.database() as db:
            for message in messages:
                db.execute(
                    "UPDATE jobs SET status='cancelled' WHERE connection=? AND chat=? AND message=? AND status IN ('queued','working')",
                    (connection, chat, message),
                )

    def allowed(self, connection, chat):
        with self.database() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM connections WHERE id=? AND enabled=1 AND NOT EXISTS (SELECT 1 FROM pauses WHERE connection=? AND chat=? AND until>?)",
                    (connection, connection, chat, self.clock()),
                ).fetchone()
            )

    def submit(self, identifier, connection, chat, message, text, created):
        with self.database() as db:
            db.execute("DELETE FROM jobs WHERE created < ?", (self.clock() - 14 * 86400,))
            # Bound private queue size and cost even while the worker is unavailable.
            if (
                db.execute(
                    "SELECT count(*) FROM jobs WHERE created>?", (self.clock() - 3600,)
                ).fetchone()[0]
                >= 200
            ):
                return False
            db.execute(
                "INSERT OR IGNORE INTO jobs(id,connection,chat,message,text,created) VALUES (?,?,?,?,?,?)",
                (identifier, connection, chat, message, text, created),
            )
            return True

    def chats(self):
        with self.database() as db:
            return [
                tuple(row)
                for row in db.execute(
                    "SELECT DISTINCT connection,chat FROM jobs WHERE status='queued'"
                )
            ]

    def claim(self, connection, chat):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET status='cancelled' WHERE status='queued' AND created<=?",
                (self.clock() - 23 * 3600,),
            )
            row = db.execute(
                "SELECT * FROM jobs WHERE connection=? AND chat=? AND status='queued' ORDER BY created,id LIMIT 1",
                (connection, chat),
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='working' WHERE id=?", (row["id"],))
            return dict(row)

    def reserve(self, identifier):
        with self.database() as db:
            result = db.execute(
                """
                UPDATE jobs SET status='sending' WHERE id=? AND status='working'
                AND created>? AND connection IN (SELECT id FROM connections WHERE enabled=1)
                AND NOT EXISTS(SELECT 1 FROM pauses WHERE pauses.connection=jobs.connection
                    AND pauses.chat=jobs.chat AND until>?)
            """,
                (identifier, self.clock() - 23 * 3600, self.clock()),
            )
            return result.rowcount == 1

    def finish(self, identifier, status):
        if status not in {"sent", "failed", "cancelled", "uncertain"}:
            raise ValueError("Invalid terminal delivery state.")
        with self.database() as db:
            db.execute(
                "UPDATE jobs SET status=? WHERE id=? AND status IN ('working','sending')",
                (status, identifier),
            )
