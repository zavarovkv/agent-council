"""Fixed Google operations behind existing per-agent workspace grants."""

import json
import threading

from .google_analytics import Analytics
from .google_auth import SCOPES, service_for
from .google_calendar import Calendar
from .google_mail import Mail
from .policy import private_json, server_policy
from .store import Store

READS = {
    "calendar": {"calendars", "list", "get", "availability"},
    "gmail": {"profile", "search", "get", "drafts", "draft_get"},
    "analytics": {"properties", "metadata", "report"},
}
WRITES = {
    "calendar": {"create", "update", "cancel"},
    "gmail": {"draft", "send", "reply", "send_draft"},
    "analytics": set(),
}


class Google:
    def __init__(self, home, factory=service_for):
        self.home, self.factory = home, factory
        self.lock = threading.Lock()
        self.store = Store(home / ".council/workspace/google-receipts.sqlite3")

    def execute(self, client, args):
        policy = server_policy(self.home)
        if client not in policy["clients"]:
            raise PermissionError("Google access was revoked.")
        if not isinstance(args, dict) or not set(args) <= {
            "service",
            "action",
            "calendar",
            "id",
            "property",
            "start",
            "end",
            "query",
            "cursor",
            "body",
            "key",
            "revision",
        }:
            raise ValueError("Unsupported Google arguments.")
        name, action = args.get("service"), args.get("action")
        if name not in READS:
            raise ValueError("Choose calendar, gmail or analytics.")
        if name != "analytics" and client != "native-qui-gon":
            raise PermissionError(
                "Personal email and calendar are available only to the owner's native assistant."
            )
        if action == "status":
            try:
                token = private_json(self.home / "google_token.json")
                return {
                    "service": name,
                    "authorized": set(SCOPES[name]) <= set(token.get("scopes", [])),
                    "live_verified": False,
                }
            except (OSError, PermissionError, ValueError):
                return {"service": name, "authorized": False, "live_verified": False}
        if action not in READS[name] | WRITES[name]:
            raise ValueError("Unsupported Google action.")
        for key in ("id", "calendar", "property", "cursor", "query", "revision"):
            if key in args and (
                not isinstance(args[key], str)
                or not 1 <= len(args[key]) <= 2000
                or any(ord(c) < 32 for c in args[key])
            ):
                raise ValueError("Use bounded single-line Google identifiers and queries.")
        if action in {
            "get",
            "draft_get",
            "update",
            "cancel",
            "reply",
            "send_draft",
        } and not args.get("id"):
            raise ValueError("Read and select an existing event, message or draft id.")
        if len(json.dumps(args)) > 64000:
            raise ValueError("Google request exceeds the size limit.")
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError("Google operation is active; retry with the same key.")
        try:

            def provider():
                if name == "analytics":
                    return Analytics(lambda api: self.factory(self.home, api))
                return (Calendar if name == "calendar" else Mail)(self.factory(self.home, name))

            if action in WRITES[name]:
                return self.store.write(client, args, lambda: provider().prepare_write(args))
            return provider().read(args)
        finally:
            self.lock.release()
