"""One credential owner and fixed project operations for all trusted agents."""

import json
import threading

from .drive import Files, service_for
from .notion import Pages
from .policy import server_policy
from .store import Store

READS = {"context", "notion_get", "drive_get", "drive_list", "drive_read"}
WRITES = {"notion_create", "notion_update", "drive_create", "drive_update", "drive_folder"}


def native_notion(name, arguments):
    from tools.mcp_tool_discovery import discover_mcp_tools
    from tools.mcp_tool_handlers import _make_tool_handler

    discover_mcp_tools(allowed_mcp_names=["notion"])
    raw = _make_tool_handler("notion", name, 60)(arguments)
    if not isinstance(raw, str) or len(raw) > 65536:
        return {"ok": False, "error": "notion_response_unavailable"}
    try:
        result = json.loads(raw)
    except ValueError:
        result = raw
    if isinstance(result, dict) and "error" in result:
        return {"ok": False, "error": "notion_operation_not_confirmed"}
    return {
        "ok": True,
        "result": result.get("result", result) if isinstance(result, dict) else result,
    }


class Workspace:
    def __init__(self, home, *, notion_call=native_notion, drive_factory=service_for):
        self.home, self.notion_call, self.drive_factory = home, notion_call, drive_factory
        self.lock = threading.Lock()
        self.store = Store(home / ".council/workspace/receipts.sqlite3")

    def execute(self, client, args):
        if not isinstance(args, dict) or not set(args) <= {
            "action",
            "id",
            "parent",
            "title",
            "content",
            "base64",
            "mime_type",
            "revision",
            "key",
            "offset",
            "cursor",
        }:
            raise ValueError("Unsupported workspace arguments.")
        action = args.get("action")
        if action not in READS | WRITES:
            raise ValueError("Unsupported workspace action.")
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError("Workspace is busy; retry with the same key.")
        try:
            policy = server_policy(self.home)
            if client not in policy["clients"]:
                raise PermissionError("Workspace grant was revoked.")
            if action == "context":
                return {
                    "notion_root": policy["notion_root"],
                    "drive_root": policy["drive_root"],
                    "notion": "formatted pages",
                    "drive": "read, list, upload and edit files in the shared folder",
                    "text_delivery": "Save formatted text in Notion; put its verified link in Linear. Submit In Review; only the owner marks Done.",
                }

            def perform():
                provider = (
                    Pages(self.notion_call, policy["notion_root"])
                    if action.startswith("notion_")
                    else Files(self.drive_factory(self.home), policy["drive_root"])
                )
                return provider.prepare_write(args)() if action in WRITES else provider.read(args)

            return self.store.write(client, args, perform) if action in WRITES else perform()
        finally:
            self.lock.release()
