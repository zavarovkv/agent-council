"""Restrict already-verified Telegram claims to one persistent account ID."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping


class OwnerDenied(ValueError):
    """The verified Telegram identity is not the configured owner."""


def telegram_id(value: Any) -> int:
    """Accept Telegram's positive integer IDs, never booleans or floats."""
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        result = int(value)
    else:
        raise OwnerDenied("Telegram did not return a valid account ID.")
    if not 0 < result < 2**63:
        raise OwnerDenied("Telegram did not return a valid account ID.")
    return result


class OwnerPolicy:
    """Pin the first verified owner login; later logins use its immutable ID.

    Call only AFTER the OIDC provider has verified the token signature, issuer,
    audience and expiry. Username bootstrapping is disabled once a pin exists.
    """

    def __init__(self, pin_path: Path, username: str, account_id: str = ""):
        self.pin_path = pin_path
        self.username = username.removeprefix("@").lower()
        self.account_id = telegram_id(account_id) if account_id else None
        if self.account_id is None and not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", self.username):
            raise OwnerDenied("Configure a Telegram account ID or valid owner username.")

    def authorize(self, claims: Mapping[str, Any]) -> int:
        account_id = telegram_id(claims.get("id"))
        if self.account_id is not None:
            if account_id != self.account_id:
                raise OwnerDenied("This Telegram account is not allowed.")
            return account_id

        self.pin_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.pin_path.with_suffix(".lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(lock_path, flags, 0o600), "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.pin_path.exists():
                try:
                    data = json.loads(self.pin_path.read_text(encoding="utf-8"))
                    owner_id = telegram_id(data["telegram_id"])
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    raise OwnerDenied("The owner identity file needs operator attention.") from exc
                if account_id != owner_id:
                    raise OwnerDenied("This Telegram account is not allowed.")
                return account_id

            username = claims.get("preferred_username")
            if not isinstance(username, str) or username.removeprefix("@").lower() != self.username:
                raise OwnerDenied("This Telegram account is not allowed.")

            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix=".owner-",
                    dir=self.pin_path.parent,
                    delete=False,
                ) as output:
                    temporary = Path(output.name)
                    json.dump(
                        {"telegram_id": account_id, "initial_username": self.username}, output
                    )
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.pin_path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return account_id
