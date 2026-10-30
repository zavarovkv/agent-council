"""Deployment-selected identity and conversation policy, independent of Telegram SDK."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


def contact_ids(value):
    """An explicit private operator list, never usernames or message claims."""
    if not isinstance(value, str):
        raise ValueError("Contact IDs must be a comma-separated string.")
    if not value:
        return frozenset()
    parts = value.split(",")
    if len(parts) > 10000 or any(
        not re.fullmatch(r"[1-9][0-9]{0,15}", part) or int(part) >= 2**52 for part in parts
    ):
        raise ValueError("Use comma-separated positive numeric Telegram contact IDs.")
    return frozenset(map(int, parts))


def normalize_username(value):
    """A conversation-style selector only; never an authorization identity."""
    if not isinstance(value, str):
        raise ValueError("Use a literal Telegram username.")
    if not value:
        return ""
    value = value.removeprefix("@")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{3,31}", value):
        raise ValueError("Invalid Telegram username for the spouse conversation style.")
    return value.lower()


def sender_username(sender) -> str:
    """Unknown future Telegram username formats fall back to the new-user style."""
    try:
        return normalize_username(getattr(sender, "username", None) or "")
    except ValueError:
        return ""


def pinned_owner(home, *, expected="", explicit="") -> int | None:
    if home is None:
        return None
    try:
        pin = Path(home) / "telegram-owner.json"
        if pin.is_symlink() or not pin.is_file():
            return None
        owner = json.loads(pin.read_text())["telegram_id"]
        if (
            type(owner) is int
            and 0 < owner < 2**63
            and (not expected or expected == str(owner))
            and (not explicit or explicit == str(owner))
        ):
            return owner
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return None


@dataclass(frozen=True)
class Settings:
    home: Path | None
    owner: int | None
    key: str = field(repr=False)
    enabled: bool
    public_enabled: bool
    contacts: frozenset[int]
    spouse_username: str
    owner_name: str
    assistant_name: str
    error: bool = False

    @classmethod
    def load(cls, environment: Mapping[str, str]):
        home = Path(environment["HERMES_HOME"]) if environment.get("HERMES_HOME") else None
        expected = environment.get("COUNCIL_SECRETARY_OWNER_ID", "")
        owner = pinned_owner(
            home, expected=expected, explicit=environment.get("HERMES_TELEGRAM_ALLOWED_ID", "")
        )
        key = environment.get("COUNCIL_SECRETARY_KEY", "")
        enabled = (
            environment.get("COUNCIL_SECRETARY_ENABLED") == "true"
            and owner is not None
            and expected == str(owner)
            and re.fullmatch(r"[a-f0-9]{64}", key) is not None
        )
        error = False
        try:
            contacts = contact_ids(environment.get("COUNCIL_SECRETARY_CONTACT_IDS", ""))
            spouse = normalize_username(environment.get("COUNCIL_SECRETARY_SPOUSE_USERNAME", ""))
        except ValueError:
            contacts, spouse, enabled, error = frozenset(), "", False, True
        return cls(
            home=home,
            owner=owner,
            key=key,
            enabled=enabled,
            public_enabled=enabled and environment.get("COUNCIL_SECRETARY_PUBLIC_DMS") == "true",
            contacts=contacts,
            spouse_username=spouse,
            owner_name=environment.get("COUNCIL_SECRETARY_OWNER_NAME", "владельца аккаунта"),
            assistant_name=environment.get("COUNCIL_SECRETARY_ASSISTANT_NAME", "Ассистент"),
            error=error,
        )
