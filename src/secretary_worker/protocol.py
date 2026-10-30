"""Strict RPC input contracts; callers cannot submit prompts, tools or model settings."""

import hashlib
import re
from dataclasses import dataclass, field

from .instructions import AUDIENCES

IDENTIFIER = re.compile(r"[a-f0-9]{64}\Z")
ROUTES = frozenset({"business", "public"})


def request_id(payload):
    if (
        not isinstance(payload, dict)
        or set(payload) != {"request_id"}
        or not isinstance(payload["request_id"], str)
        or not IDENTIFIER.fullmatch(payload["request_id"])
    ):
        raise ValueError("Invalid delivery acknowledgement.")
    return payload["request_id"]


@dataclass(frozen=True)
class ReplyRequest:
    identifier: str
    conversation: str
    text: str = field(repr=False)
    audience: str
    route: str = "public"

    @classmethod
    def parse(cls, payload):
        fields = {"request_id", "conversation", "text"}
        if (
            not isinstance(payload, dict)
            or not fields.issubset(payload)
            or set(payload) - fields - {"audience", "route"}
        ):
            raise ValueError("Unexpected request fields.")
        identifier, conversation, text = (
            payload[name] for name in ("request_id", "conversation", "text")
        )
        audience = payload.get("audience", "new-users")
        route = payload.get("route", "public")
        if (
            not all(
                isinstance(value, str)
                for value in (identifier, conversation, text, audience, route)
            )
            or not IDENTIFIER.fullmatch(identifier)
            or not IDENTIFIER.fullmatch(conversation)
            or not text.strip()
            or len(text) > 6000
            or audience not in AUDIENCES
            or route not in ROUTES
        ):
            raise ValueError("Invalid or oversized request.")
        return cls(identifier, conversation, text, audience, route)

    @property
    def context(self):
        # Keep the existing namespace stable across upgrades and separate each scenario.
        return hashlib.sha256(f"{self.conversation}:{self.audience}".encode()).hexdigest()
