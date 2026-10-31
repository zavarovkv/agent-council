"""An opt-in policy epoch; toggling it invalidates pending guest answers."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

RELATIVE_POLICY = Path("plugin-data/council-secretary/guest.json")


@dataclass(frozen=True)
class GuestState:
    enabled: bool = False
    revision: str = ""
    error: bool = False
    schema: int = 2


def load_state(home):
    path = Path(home) / RELATIVE_POLICY
    if not path.exists() and not path.is_symlink():
        return GuestState()
    try:
        if (
            path.resolve() != path
            or not path.is_file()
            or path.stat().st_mode & 0o077
            or path.stat().st_size > 4096
        ):
            raise ValueError("Invalid guest policy file.")
        value = json.loads(path.read_text())
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "enabled", "revision"}
            or type(value["schema"]) is not int
            or value["schema"] not in {1, 2}
            or type(value["enabled"]) is not bool
            or not isinstance(value["revision"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["revision"])
        ):
            raise ValueError("Invalid guest policy.")
        return GuestState(value["enabled"], value["revision"], schema=value["schema"])
    except (OSError, ValueError, TypeError, KeyError):
        return GuestState(error=True)
