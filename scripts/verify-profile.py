#!/usr/bin/env python3
"""Verify a restored copy offline without starting bots, hooks or model calls."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def main():
    from hermes_cli.config import read_user_config_raw
    from hermes_state import SessionDB
    from plugins.dashboard_auth.telegram_owner import register

    home = Path(os.environ["HERMES_HOME"])
    if home != Path("/opt/data") or os.geteuid() == 0:
        raise ValueError("Verify a mounted copy as the unprivileged runtime user.")
    read_user_config_raw(home / "config.yaml")
    for path in [home / "state.db", *sorted((home / "profiles").glob("*/state.db"))]:
        if not path.exists():
            continue
        if path.is_symlink():
            raise ValueError("Refusing a symlinked session store.")
        database = SessionDB(db_path=path)
        database.close()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Session database integrity check failed.")
    session_store = home / "dashboard-sessions.sqlite3"
    if session_store.exists():
        if session_store.is_symlink():
            raise ValueError("Refusing a symlinked dashboard session store.")
        with closing(sqlite3.connect(session_store.as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Dashboard session integrity check failed.")

    class Context:
        def __init__(self):
            self.providers = []

        def register_dashboard_auth_provider(self, provider):
            self.providers.append(provider)

    context = Context()
    register(context)
    if [provider.name for provider in context.providers] != ["telegram-owner"]:
        raise ValueError("The owner auth provider was not registered.")
    pin = home / "telegram-owner.json"
    if pin.exists():
        from plugins.dashboard_auth.telegram_owner.owner import telegram_id

        telegram_id(json.loads(pin.read_text())["telegram_id"])
    print("Restored session databases and closed owner-auth provider: OK")


if __name__ == "__main__":
    os.umask(0o077)
    main()
