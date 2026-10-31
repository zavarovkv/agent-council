"""Private, operator-controlled destinations and per-profile bearer grants."""

import json
import re
from pathlib import Path

ROLES = {"qui-gon", "dexter", "lando", "padme"}


def private_json(path):
    path = Path(path)
    if path.resolve() != path or not path.is_file() or path.stat().st_mode & 0o077:
        raise PermissionError("A private regular workspace configuration is required.")
    if path.stat().st_size > 65536:
        raise ValueError("Workspace configuration exceeds its size limit.")
    return json.loads(path.read_text())


def server_policy(home):
    value = private_json(home / ".council/workspace/access.json")
    if (
        set(value) != {"schema", "enabled", "clients", "notion_root", "drive_root"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
    ):
        raise ValueError("Invalid workspace policy.")
    if value["enabled"] is not True:
        raise PermissionError("Shared workspace access is disabled.")
    if not re.fullmatch(r"[a-f0-9]{32}", value["notion_root"]) or not re.fullmatch(
        r"[A-Za-z0-9_-]{10,128}", value["drive_root"]
    ):
        raise ValueError("Explicit cloud destinations are required.")
    clients = value["clients"]
    if not isinstance(clients, dict) or not 1 <= len(clients) <= 8:
        raise ValueError("Invalid workspace clients.")
    keys = set()
    for name, entry in clients.items():
        if (
            not re.fullmatch(r"(?:native|linear)-(?:qui-gon|dexter|lando|padme)", name)
            or not isinstance(entry, dict)
            or set(entry) != {"key", "role"}
            or entry["role"] not in ROLES
            or name.split("-", 1)[1] != entry["role"]
            or not re.fullmatch(r"[a-f0-9]{64}", entry["key"])
            or entry["key"] in keys
        ):
            raise ValueError("Invalid workspace grant.")
        keys.add(entry["key"])
    return value
