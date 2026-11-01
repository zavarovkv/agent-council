"""Invalidate derived Hermes instruction caches during a stopped-profile install."""

import os
import sqlite3
from contextlib import closing
from pathlib import PurePosixPath

from .config import OperationError


def affected_profiles(changed):
    profiles = set()
    for name in changed:
        parts = PurePosixPath(name).parts
        if parts[:2] == (".council", "secretary"):
            continue  # This worker reads its separate rules on each request.
        if parts[:2] == (".council", "linear"):
            profiles.add(PurePosixPath(*parts[:3]))
        elif parts[0] == "profiles":
            profiles.add(PurePosixPath(*parts[:2]))
        else:
            profiles.add(PurePosixPath("."))
    return profiles


def regular(path):
    if path.resolve() != path or (path.exists() and not path.is_file()):
        raise OperationError("Refusing a linked or non-file instruction cache.")


def refresh_profiles(home, changed, uid, gid):
    """Keep messages, session IDs, tool grants and memory; rebuild prompt on next turn.

    Hermes restores a saved prompt even after restart. Its pinned schema stores
    both a legacy inline value and a hash. Clear those pointers, not the archived
    prompt bodies or session transcripts. Call only while runtime writers are stopped.
    """
    for relative in sorted(affected_profiles(changed)):
        profile = home / relative
        snapshot = profile / ".skills_prompt_snapshot.json"
        regular(snapshot)
        database = profile / "state.db"
        for suffix in ("", "-wal", "-shm", "-journal"):
            regular(database.with_name(database.name + suffix))
        if database.exists():
            try:
                with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as db:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
                    if not {"id", "system_prompt", "system_prompt_hash"} <= columns:
                        raise OperationError(
                            "Unsupported Hermes prompt-cache schema; installation stopped."
                        )
                    with db:
                        db.execute(
                            "UPDATE sessions SET system_prompt=NULL, system_prompt_hash=NULL WHERE system_prompt IS NOT NULL OR system_prompt_hash IS NOT NULL"
                        )
            except sqlite3.Error as error:
                raise OperationError("Could not refresh Hermes instruction cache.") from error
            for suffix in ("-wal", "-shm", "-journal"):
                path = database.with_name(database.name + suffix)
                if path.exists():
                    os.chown(path, uid, gid)
        snapshot.unlink(missing_ok=True)
