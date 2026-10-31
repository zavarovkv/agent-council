#!/usr/bin/env python3
"""Verify a restored copy offline without starting bots, hooks or model calls."""

import json
import os
import runpy
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


def verify_managed_skills(home):
    marker = home / ".council/instructions.json"
    if not marker.exists():
        return
    from utils import fast_safe_load

    if marker.is_symlink():
        raise ValueError("Refusing a symlinked instruction marker.")
    files = json.loads(marker.read_text())["files"]
    if not isinstance(files, dict) or len(files) > 1024:
        raise ValueError("Invalid managed instruction inventory.")
    for name in files:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid managed instruction path.")
        if relative.name != "SKILL.md":
            continue
        path = home / relative
        if path.resolve() != path or not path.is_file() or path.stat().st_size > 1048576:
            raise ValueError("Invalid managed skill file.")
        parts = path.read_text().split("---\n", 2)
        if len(parts) != 3 or parts[0] or not parts[2].strip():
            raise ValueError("Managed skills need YAML front matter and a Markdown body.")
        try:
            metadata = fast_safe_load(parts[1])
        except Exception:
            raise ValueError("Invalid managed skill YAML metadata.") from None
        if not isinstance(metadata, dict) or not all(
            isinstance(metadata.get(field), str) and metadata[field].strip()
            for field in ("name", "description")
        ):
            raise ValueError("Managed skills need a name and description.")


def main():
    from hermes_cli.config import read_user_config_raw
    from hermes_state import SessionDB
    from plugins.dashboard_auth.telegram_owner import register

    home = Path(os.environ["HERMES_HOME"])
    if home != Path("/opt/data") or os.geteuid() == 0:
        raise ValueError("Verify a mounted copy as the unprivileged runtime user.")
    read_user_config_raw(home / "config.yaml")
    linear = home / ".council/linear"
    if (linear / ".managed").exists():
        sys.path.insert(0, "/opt/agent-council")
        from linear_agent.config import Registry, private_json

        plan = private_json(linear / "bridge/plan.json")
        if (linear / "bridge/agents.json").exists():
            Registry.load(linear / "bridge")
        for slug in plan["agents"]:
            profile = linear / slug
            config = read_user_config_raw(profile / "config.yaml")
            if (
                profile.resolve() != profile
                or set(config.get("platform_toolsets", {}).get("api_server", []))
                != {"web", "clarify", "council-linear"}
                or config.get("mcp_servers") != {}
                or not (profile / "SOUL.md").is_file()
            ):
                raise ValueError("Invalid isolated Linear executor policy.")
            for path in profile.rglob("*.db"):
                if path.resolve() != path:
                    raise ValueError("Refusing a symlinked executor store.")
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise ValueError("Linear executor store integrity check failed.")
        store = linear / "bridge/linear.sqlite3"
        if store.exists():
            with closing(sqlite3.connect(store.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("Linear bridge store integrity check failed.")
    verify_managed_skills(home)
    guest_policy = home / "plugin-data/council-secretary/guest.json"
    if guest_policy.exists() or guest_policy.is_symlink():
        state = runpy.run_path("/opt/hermes/plugins/council-secretary/guest_policy.py")[
            "load_state"
        ](home)
        if state.error:
            raise ValueError("Invalid restored guest policy.")
        from telegram import Bot, Update, User

        if "guest_message" not in Update.ALL_TYPES or not callable(
            getattr(Bot, "answer_guest_query", None)
        ):
            raise ValueError("Guest-compatible SDK required.")
        if not hasattr(User, "supports_guest_queries"):
            raise ValueError("Guest capability verification is unavailable.")
        sys.path.insert(0, "/opt/agent-council")
        from secretary_worker.instructions import guest_instructions

        guest_instructions()
    private_instructions = home / ".council/secretary/instructions"
    mode = os.environ.get("COUNCIL_VERIFY_SECRETARY_INSTRUCTIONS", "image")
    if mode not in {"image", "profile"}:
        raise ValueError("Invalid restored instruction source.")
    if mode == "profile":
        sys.path.insert(0, "/opt/agent-council")
        from secretary_worker.instructions import configured_instructions

        if private_instructions.resolve() != private_instructions:
            raise ValueError("Refusing a symlinked secretary instruction directory.")
        configured_instructions(
            private_instructions.parent, {"COUNCIL_SECRETARY_INSTRUCTIONS": "profile"}
        )
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
    for session_store in (
        home / "dashboard-sessions.sqlite3",
        home / "plugin-data/council-secretary/queue.sqlite3",
        home / "plugin-data/council-secretary/public-queue.sqlite3",
        home / "plugin-data/council-secretary/guest-queue.sqlite3",
        home / ".council/secretary/secretary-history.sqlite3",
        home / ".council/research-filing/outbox.sqlite3",
    ):
        if session_store.exists():
            if session_store.is_symlink():
                raise ValueError("Refusing a symlinked dashboard session store.")
            with closing(
                sqlite3.connect(session_store.as_uri() + "?mode=ro", uri=True)
            ) as connection:
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
