"""Explicit user session and immutable source pin; no bot or default account."""

import re
from dataclasses import dataclass, field


def source_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", value):
        raise ValueError("Use one explicit Telegram source username without @ or a URL.")
    return value.lower()


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str = field(repr=False)
    session: str = field(repr=False)
    account: int
    peer: int
    source: str

    @classmethod
    def load(cls, environment):
        values = {
            name: environment["COUNCIL_TG_READER_" + name]
            for name in ("API_ID", "API_HASH", "SESSION", "ACCOUNT_ID", "PEER_ID", "SOURCE")
        }
        if any(
            not re.fullmatch(r"[0-9]{1,16}", values[name]) or int(values[name]) <= 0
            for name in ("API_ID", "ACCOUNT_ID", "PEER_ID")
        ):
            raise ValueError("Explicit positive account and source IDs are required.")
        if not re.fullmatch(r"[a-f0-9]{32}", values["API_HASH"]) or not re.fullmatch(
            r"1[A-Za-z0-9_=+-]{100,1024}", values["SESSION"]
        ):
            raise ValueError("Enter Telegram API credentials through interactive setup.")
        return cls(
            int(values["API_ID"]),
            values["API_HASH"],
            values["SESSION"],
            int(values["ACCOUNT_ID"]),
            int(values["PEER_ID"]),
            source_name(values["SOURCE"]),
        )
