"""Explicit installation identities and fixed executor destinations; no implicit owner."""

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

SLUGS = {"qui-gon": "linear-executor", "dexter": "dexter-executor"}
SCOPES = frozenset({"read", "write", "app:assignable", "app:mentionable"})
CAPABILITY = "linear-agents-v1"


def identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Expected a canonical immutable Linear ID.")
    return value


def private_json(path, *, limit=131072):
    path = Path(path)
    if (
        path.resolve() != path
        or not path.is_file()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > limit
    ):
        raise ValueError("Expected a regular private integration file.")
    return json.loads(path.read_text())


@dataclass(frozen=True)
class Agent:
    slug: str
    client_id: str
    client_secret: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    executor_url: str
    executor_key: str = field(repr=False)
    delegates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Registry:
    enabled: bool
    organization: str
    owner: str
    teams: frozenset[str]
    origin: str
    revision: str
    agents: dict[str, Agent] = field(repr=False)

    @classmethod
    def load(cls, home):
        value = private_json(Path(home) / "agents.json")
        if (
            set(value)
            != {
                "schema",
                "enabled",
                "organization",
                "owner",
                "teams",
                "origin",
                "revision",
                "agents",
            }
            or type(value["schema"]) is not int
            or value["schema"] != 1
            or type(value["enabled"]) is not bool
        ):
            raise ValueError("Invalid Linear registry schema.")
        origin = urlsplit(value["origin"])
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("Use an explicit HTTPS integration origin.")
        if (
            not isinstance(value["teams"], list)
            or not 1 <= len(value["teams"]) <= 32
            or len(set(value["teams"])) != len(value["teams"])
        ):
            raise ValueError("Select explicit Linear teams.")
        teams = frozenset(identifier(item) for item in value["teams"])
        if (
            not re.fullmatch(r"[0-9a-f]{32}", value["revision"])
            or not isinstance(value["agents"], dict)
            or not 1 <= len(value["agents"]) <= len(SLUGS)
        ):
            raise ValueError("Invalid integration policy revision or agents.")
        agents = {}
        for slug, record in value["agents"].items():
            if slug not in SLUGS or set(record) != {
                "client_id",
                "client_secret",
                "webhook_secret",
                "executor_url",
                "executor_key",
                "delegates",
            }:
                raise ValueError("Unrecognized agent registration.")
            if record["executor_url"] != f"http://{SLUGS[slug]}:8642" or not re.fullmatch(
                r"[a-f0-9]{64}", record["executor_key"]
            ):
                raise ValueError("Use the fixed authenticated executor destination.")
            for name in ("client_secret", "webhook_secret"):
                secret = record[name]
                if (
                    not isinstance(secret, str)
                    or not 16 <= len(secret) <= 1024
                    or any(ord(char) < 33 or ord(char) > 126 for char in secret)
                ):
                    raise ValueError("Invalid application credential.")
            if not isinstance(record["delegates"], list) or any(
                item not in SLUGS or item == slug for item in record["delegates"]
            ):
                raise ValueError("Invalid agent delegation policy.")
            if not isinstance(record["client_id"], str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{8,128}", record["client_id"]
            ):
                raise ValueError("Invalid public OAuth client identifier.")
            agents[slug] = Agent(
                slug,
                record["client_id"],
                record["client_secret"],
                record["webhook_secret"],
                record["executor_url"],
                record["executor_key"],
                tuple(record["delegates"]),
            )
        for name in ("client_id", "webhook_secret", "executor_key"):
            if len({getattr(agent, name) for agent in agents.values()}) != len(agents):
                raise ValueError("Each agent needs its own application and executor credentials.")
        if any(target not in agents for agent in agents.values() for target in agent.delegates):
            raise ValueError("Register each allowed executor explicitly.")
        # One level is enough for coordinator -> specialist; no cycles or task trees.
        if any(agents[target].delegates for agent in agents.values() for target in agent.delegates):
            raise ValueError("Nested delegation is disabled.")
        return cls(
            value["enabled"],
            identifier(value["organization"]),
            identifier(value["owner"]),
            teams,
            value["origin"].rstrip("/"),
            value["revision"],
            agents,
        )

    def callback(self, slug):
        return f"{self.origin}/integrations/linear/{slug}/oauth/callback"


def app_manifest(origin, slug):
    if slug not in SLUGS:
        raise ValueError("Unknown agent.")
    base = origin.rstrip("/") + "/integrations/linear/" + slug
    return {
        "$schema": "https://linear.app/.well-known/oauth-app-manifest.schema.json",
        "schemaVersion": "1.0.0",
        "distribution": "private",
        "display": {"description": "Agent Council assistant for delegated tasks and research."},
        "developer": {"name": "Agent Council"},
        "oauth": {
            "client_name": "Квай-Гон Джинн" if slug == "qui-gon" else "Dexter Jettster",
            "client_uri": origin,
            "redirect_uris": [base + "/oauth/callback"],
            "grant_types": ["authorization_code"],
        },
        "webhook": {
            "enabled": True,
            "url": base + "/webhook",
            "resourceTypes": ["AgentSessionEvent", "OAuthAuthorization", "PermissionChange"],
        },
    }
