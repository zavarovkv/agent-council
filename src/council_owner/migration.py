"""Adopt only admitted Council guest conversations using native policy transactions.

This is not an authentication boundary: admission/revocation remain in the Telegram
bridge, and the local control protocol remains owner authenticated. The evidence
here prevents an unrelated CLI conversation from being silently reconfigured.
"""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from .context import SOURCE, conversation


def private_json(path):
    if (
        path.resolve() != path
        or not path.is_file()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 4096
    ):
        raise ValueError("Invalid private context file")
    return json.loads(path.read_text())


def admitted_title(home, title):
    """A current, durably admitted request must identify this exact conversation."""
    try:
        folder = home / "plugin-data/council-secretary"
        state = private_json(folder / "guest.json")
        if state.get("schema") != 4 or not state.get("enabled") or not state.get("owner_agent"):
            return False
        owner = private_json(home / "telegram-owner.json")["telegram_id"]
        if type(owner) is not int or not 0 < owner < 2**63:
            return False
        path = folder / "owner-guest.sqlite3"
        if path.resolve() != path or not path.is_file() or path.stat().st_mode & 0o077:
            return False
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            rows = db.execute(
                "SELECT payload FROM owner_jobs WHERE status='running' AND epoch=? LIMIT 2",
                (state["revision"],),
            ).fetchall()
        if len(rows) != 1:
            return False
        job = json.loads(rows[0][0])
        return type(job["actor"]) is int and job["actor"] == owner and conversation(job) == title
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return False


def adopt_guest_policy(authority, session_id, policy, picked):
    from gateway.session_authorities import served_profile_name
    from gateway.session_policy import FOLLOW_ADOPTED

    if (
        policy.source != "oneshot"
        or policy.ignore_user_config
        or picked
        or served_profile_name(authority.profile_id) != "default"
    ):
        return policy
    request = json.loads(policy.request_json)
    # Preserve launch idempotency and never silently erase an explicit user choice.
    if set(request) - {"request_id", "source"} or request.get("source") != "oneshot":
        return policy
    home = Path(authority.db.db_path).resolve().parent
    row = authority.db.get_session(session_id) or {}
    if not admitted_title(home, row.get("title")):
        return policy
    config = json.loads(policy.config_json)
    config[FOLLOW_ADOPTED] = True
    return replace(policy, source=SOURCE, platform="telegram", config_json=json.dumps(config))


def invalidate_adopted_prompt(conn, session_id, target, old, new):
    """Inside the native idle/revision-fenced transaction; history stays intact."""
    if old["source"] == "oneshot" and new["source"] == SOURCE:
        conn.execute(
            "UPDATE sessions SET system_prompt=NULL, system_prompt_hash=NULL WHERE id IN (?,?)",
            (session_id, target),
        )
