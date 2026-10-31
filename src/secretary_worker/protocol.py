"""Strict RPC input contracts; callers cannot submit prompts, tools or model settings."""

import hashlib
import json
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
    mode: str = "chat"
    reply_context: str = field(default="", repr=False)

    @classmethod
    def parse(cls, payload):
        fields = {"request_id", "conversation", "text"}
        if (
            not isinstance(payload, dict)
            or not fields.issubset(payload)
            or set(payload) - fields - {"audience", "route", "mode", "reply_context"}
        ):
            raise ValueError("Unexpected request fields.")
        identifier, conversation, text = (
            payload[name] for name in ("request_id", "conversation", "text")
        )
        audience = payload.get("audience", "new-users")
        route = payload.get("route", "public")
        mode = payload.get("mode", "chat")
        context = payload.get("reply_context", "")
        if (
            not all(
                isinstance(value, str)
                for value in (identifier, conversation, text, audience, route, mode, context)
            )
            or not IDENTIFIER.fullmatch(identifier)
            or not IDENTIFIER.fullmatch(conversation)
            or not text.strip()
            or len(text) > 6000
            or audience not in AUDIENCES
            or route not in ROUTES
            or mode not in {"chat", "guest"}
            or (mode == "chat" and context)
            or (
                mode == "guest"
                and (
                    route != "public"
                    or audience != "new-users"
                    or len(text) > 4000
                    or len(context) > 2000
                )
            )
        ):
            raise ValueError("Invalid or oversized request.")
        return cls(identifier, conversation, text, audience, route, mode, context)

    @property
    def context(self):
        # Keep the existing namespace stable across upgrades and separate each scenario.
        if self.mode == "guest":
            return hashlib.sha256(f"guest:{self.conversation}".encode()).hexdigest()
        return hashlib.sha256(f"{self.conversation}:{self.audience}".encode()).hexdigest()

    @property
    def storage_question(self):
        if self.mode == "guest":
            return hashlib.sha256(
                json.dumps(
                    [self.text, self.reply_context], ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
        return self.text
