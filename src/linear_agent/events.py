"""Verify Linear's raw signed bytes before accepting bounded session events."""

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from .config import identifier


@dataclass(frozen=True)
class Event:
    key: str
    agent: str
    session: str
    action: str
    activity: str
    actor: str
    text: str = field(repr=False)
    created: float
    stop: bool = False


def unique_json(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate event key.")
        value[key] = item
    return value


def verified_payload(body, signature, secret, now):
    if (
        not isinstance(body, bytes)
        or not 0 < len(body) <= 131072
        or not isinstance(signature, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", signature)
    ):
        raise ValueError("Invalid webhook envelope.")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise PermissionError("Invalid webhook signature.")
    payload = json.loads(body, object_pairs_hook=unique_json)
    stamp = payload.get("webhookTimestamp") if isinstance(payload, dict) else None
    if type(stamp) not in {int, float} or not math.isfinite(stamp) or abs(now - stamp / 1000) > 60:
        raise PermissionError("Expired webhook envelope.")
    return payload


def parse_event(payload, registry, agent):
    if payload.get("type") != "AgentSessionEvent":
        return None
    if payload.get("organizationId") != registry.organization:
        raise PermissionError("Unrecognized installation.")
    if not isinstance(payload.get("oauthClientId"), str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{8,128}", payload["oauthClientId"]
    ):
        raise ValueError("Missing OAuth application identity.")
    action = payload.get("action")
    if action not in {"created", "prompted"}:
        return None
    session = payload["agentSession"]
    sid = identifier(session["id"])
    if session.get("organizationId") != registry.organization or session.get(
        "appUserId"
    ) != payload.get("appUserId"):
        raise PermissionError("Conflicting session identity.")
    identifier(payload["appUserId"])
    activity, actor, stop = "", session.get("creatorId") or "", False
    text = payload.get("promptContext") or ""
    if action == "prompted":
        prompt = payload["agentActivity"]
        activity = identifier(prompt["id"])
        actor = identifier(prompt["userId"])
        content = prompt["content"]
        if prompt.get("agentSessionId") != sid or content.get("type") != "prompt":
            raise ValueError("Invalid session continuation.")
        text = content.get("body") or ""
        stop = prompt.get("signal") == "stop"
    if actor:
        identifier(actor)
    if (
        not isinstance(text, str)
        or len(text) > 48000
        or (action == "prompted" and not stop and not text.strip())
    ):
        raise ValueError("Invalid session text.")
    created = datetime.fromisoformat(payload["createdAt"].replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise ValueError("A timezone is required.")
    key = hashlib.sha256(f"{agent.slug}:{sid}:{action}:{activity}".encode()).hexdigest()
    return Event(key, agent.slug, sid, action, activity, actor, text, created.timestamp(), stop)
