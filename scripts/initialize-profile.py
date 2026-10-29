#!/usr/bin/env python3
"""Initialize a fresh dashboard's chat store without starting a gateway or bot."""

import os
from pathlib import Path


def initialize(home):
    from gateway.runtime_ownership import exclusive_maintenance
    from hermes_state import SessionDB

    path = home / "state.db"
    if path.exists() and path.stat().st_size:
        return
    with exclusive_maintenance([home]):
        if not path.exists() or not path.stat().st_size:
            database = SessionDB(db_path=path)
            database.close()


if __name__ == "__main__":
    os.umask(0o077)
    initialize(Path(os.environ["HERMES_HOME"]).resolve())
