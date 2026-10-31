"""Narrow task-bound tools; identity comes from Hermes context, never model arguments."""

import json
import os
import urllib.error
import urllib.request


def call(path, args):
    from gateway.session_context import get_session_env

    context = {
        "run": get_session_env("HERMES_SESSION_KEY", ""),
        "session": get_session_env("HERMES_SESSION_ID", ""),
    }
    slug = os.environ.get("COUNCIL_LINEAR_AGENT", "")
    key = os.environ.get("API_SERVER_KEY", "")
    if slug not in {"qui-gon", "dexter", "marketing"} or not key or not any(context.values()):
        return json.dumps({"error": "This tool requires an active authorized Linear session."})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    request = urllib.request.Request(
        "http://linear-adapter:9122/" + slug + "/" + path,
        data=json.dumps({**args, **context}, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=15) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("Oversized tool response")
            return json.dumps(json.loads(body), ensure_ascii=False)
    except Exception:
        return json.dumps(
            {
                "error": "Sub-issue operation is unavailable. Reuse the same request key when retrying."
            }
        )


def register(ctx):
    for name, description, properties, required, path in (
        (
            "linear_delegate",
            "Create or retrieve one idempotent sub-issue under the current Linear task for an allowed specialist. Reuse key when retrying; the coordinator receives the result automatically.",
            {
                "target": {"type": "string", "enum": ["dexter", "marketing"]},
                "key": {"type": "string", "maxLength": 80},
                "title": {"type": "string", "maxLength": 200},
                "description": {"type": "string", "maxLength": 12000},
            },
            ["target", "key", "title", "description"],
            "delegate",
        ),
        (
            "linear_agents",
            "Read the current registered agents, specializations, installation status and delegation policy before choosing a specialist.",
            {},
            [],
            "agents",
        ),
        (
            "linear_child_tasks",
            "Read the current task's delegated sub-issues and completed results.",
            {},
            [],
            "children",
        ),
    ):

        def handler(args, _path=path, **_kwargs):
            return call(_path, args)

        ctx.register_tool(
            name=name,
            toolset="council-linear",
            description=description,
            schema={
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
            handler=handler,
        )
    from .skills import view

    description = "List reviewed local skills, or read one SKILL.md and its Markdown reference. Read-only; cannot install, edit or execute skills."
    ctx.register_tool(
        name="council_skill",
        toolset="council-linear",
        description=description,
        schema={
            "name": "council_skill",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "path": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        handler=lambda args, **_kwargs: view(args),
    )
