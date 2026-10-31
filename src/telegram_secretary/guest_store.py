"""Short-lived guest contexts, bounded admission and no replay of started work."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class GuestStore:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        if self.path.resolve() != self.path:
            raise ValueError("Refusing a symlinked guest queue.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, query_id TEXT NOT NULL,
                    connection TEXT NOT NULL, actor INTEGER NOT NULL,
                    chat INTEGER NOT NULL, message INTEGER NOT NULL, thread INTEGER NOT NULL,
                    text TEXT NOT NULL, reply_context TEXT NOT NULL, notice TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued'
                );
                CREATE INDEX IF NOT EXISTS guest_actor ON jobs(actor,created);
                CREATE INDEX IF NOT EXISTS guest_chat ON jobs(connection,chat,created);
            """)
            # A lost model response or guest send is not replayable after restart.
            db.execute("UPDATE jobs SET status='failed' WHERE status='working'")
            db.execute("UPDATE jobs SET status='uncertain' WHERE status='sending'")
            self._prune(db)
        self.path.chmod(0o600)

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _prune(self, db):
        now = self.clock()
        db.execute("DELETE FROM jobs WHERE created<?", (now - 3600,))
        db.execute(
            "UPDATE jobs SET status=CASE WHEN status='sending' THEN 'uncertain' ELSE 'expired' END "
            "WHERE expires<=? AND status IN ('queued','working','sending')",
            (now,),
        )
        db.execute(
            "UPDATE jobs SET query_id='',text='',reply_context='' "
            "WHERE status NOT IN ('queued','working','sending')"
        )

    def prune(self, revision):
        with self.database() as db:
            db.execute(
                "UPDATE jobs SET status='cancelled' WHERE connection!=? "
                "AND status IN ('queued','working')",
                (revision or "",),
            )
            self._prune(db)

    def submit(self, job, revision):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db)
            if db.execute("SELECT 1 FROM jobs WHERE id=?", (job["id"],)).fetchone():
                return False
            if (
                db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= 60
                or db.execute(
                    "SELECT count(*) FROM jobs WHERE actor=?", (job["actor"],)
                ).fetchone()[0]
                >= 10
                or db.execute(
                    "SELECT count(*) FROM jobs WHERE status IN ('queued','working','sending')"
                ).fetchone()[0]
                >= 16
                or db.execute("SELECT count(*) FROM jobs WHERE chat=?", (job["chat"],)).fetchone()[
                    0
                ]
                >= 20
                or db.execute(
                    "SELECT count(*) FROM jobs WHERE chat=? AND status IN ('queued','working','sending')",
                    (job["chat"],),
                ).fetchone()[0]
                >= 4
                or db.execute(
                    "SELECT 1 FROM jobs WHERE actor=? AND status IN ('queued','working','sending')",
                    (job["actor"],),
                ).fetchone()
            ):
                return False
            db.execute(
                "INSERT INTO jobs(id,query_id,connection,actor,chat,message,thread,text,"
                "reply_context,notice,created,expires) VALUES "
                "(:id,:query_id,:connection,:actor,:chat,:message,:thread,:text,"
                ":reply_context,:notice,:created,:expires)",
                {**job, "connection": revision},
            )
            return True

    def chats(self):
        with self.database() as db:
            self._prune(db)
            return [
                tuple(row)
                for row in db.execute(
                    "SELECT DISTINCT connection,chat FROM jobs WHERE status='queued'"
                )
            ]

    def claim(self, connection, chat):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db)
            row = db.execute(
                "SELECT * FROM jobs WHERE connection=? AND chat=? AND status='queued' "
                "ORDER BY created,message,id LIMIT 1",
                (connection, chat),
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='working' WHERE id=?", (row["id"],))
            return dict(row)

    def can_generate(self, identifier):
        with self.database() as db:
            return (
                db.execute(
                    "SELECT 1 FROM jobs WHERE id=? AND status='working' AND expires>?",
                    (identifier, self.clock()),
                ).fetchone()
                is not None
            )

    def reserve(self, identifier):
        with self.database() as db:
            return (
                db.execute(
                    "UPDATE jobs SET status='sending' WHERE id=? AND status='working' AND expires>?",
                    (identifier, self.clock()),
                ).rowcount
                == 1
            )

    def finish(self, identifier, status):
        if status not in {"sent", "failed", "cancelled", "uncertain"}:
            raise ValueError("Invalid guest delivery state.")
        with self.database() as db:
            db.execute(
                "UPDATE jobs SET status=?,query_id='',text='',reply_context='' "
                "WHERE id=? AND status IN ('queued','working','sending')",
                (status, identifier),
            )
