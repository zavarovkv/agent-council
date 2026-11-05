"""Native owner intake; Linear app credentials remain in the isolated adapter."""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from council_team.communication import BRIEF_GUIDANCE, BRIEF_LIMIT, TextLimitError


def settings(home):
    path = Path(home) / ".council/tasks-client.json"
    if (
        path.resolve() != path
        or not path.is_file()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 4096
    ):
        raise PermissionError("Invalid native task client grant.")
    value = json.loads(path.read_text())
    if (
        set(value) != {"schema", "key"}
        or value["schema"] != 1
        or not re.fullmatch(r"[a-f0-9]{64}", value["key"])
    ):
        raise ValueError("Invalid native task client settings.")
    return value


def call(args, *, home=None):
    if home is None:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    request = urllib.request.Request(
        "http://linear-adapter:9122/coordinator/tasks",
        data=json.dumps(args, ensure_ascii=False).encode(),
        headers={
            "Authorization": "Bearer " + settings(home)["key"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(65537)
    except urllib.error.HTTPError as error:
        value = None
        if error.code == 400:
            try:
                rejected = error.read(4097)
                if len(rejected) <= 4096:
                    value = json.loads(rejected)
            except (ValueError, OSError):
                pass
        if (
            isinstance(value, dict)
            and value.get("code") == "text_too_long"
            and isinstance(value.get("error"), str)
        ):
            raise TextLimitError(value["error"]) from None
        raise
    if len(body) > 65536:
        raise ValueError("Oversized native task response.")
    return json.loads(body)


def register(ctx):
    def available():
        from hermes_constants import get_hermes_home

        try:
            settings(get_hermes_home())
            return True
        except (OSError, ValueError, PermissionError):
            return False

    def handler(args, **_kwargs):
        try:
            return json.dumps(call(args), ensure_ascii=False)
        except TextLimitError as error:
            return json.dumps({"error": str(error), "code": "text_too_long"}, ensure_ascii=False)
        except Exception:
            return json.dumps(
                {
                    "error": "Task routing was not confirmed. Use list/get and the SAME key before retrying. Do not create a duplicate, use personal Linear credentials or claim delegation succeeded."
                }
            )

    description = "Route owner work to the Council as Qui-Gon, never as the owner's personal Linear account. create queues one durable parent task delegated to Qui-Gon with no human assignee; its Linear coordinator assigns specialists and reviews their work. Use list before creating to avoid duplicates, then get to verify actual admission/execution/results. A queued receipt is not proof that work started. reply relays an explicit owner clarification using a stable message_key. Do not do specialist research in parallel or use personal MCP writes to manage team work. Return the verified task link and check status for the owner."
    ctx.register_tool(
        name="council_tasks",
        toolset="council_workspace",
        description=description,
        schema={
            "name": "council_tasks",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "get", "list", "reply"]},
                    "key": {"type": "string", "maxLength": 80},
                    "title": {"type": "string", "maxLength": 200},
                    "description": {
                        "type": "string",
                        "maxLength": BRIEF_LIMIT,
                        "description": BRIEF_GUIDANCE,
                    },
                    "message_key": {"type": "string", "maxLength": 80},
                    "text": {"type": "string", "maxLength": 8000},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
        handler=handler,
        check_fn=available,
    )
