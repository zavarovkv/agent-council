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
    if (
        slug not in {"qui-gon", "dexter", "marketing", "padme"}
        or not key
        or not any(context.values())
    ):
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
    except urllib.error.HTTPError as error:
        if error.code in {400, 403}:
            try:
                return json.dumps(json.loads(error.read(4096)), ensure_ascii=False)
            except (ValueError, OSError):
                pass
        return json.dumps(
            {
                "error": "Linear operation was not confirmed. Reuse the same request key; do not claim success."
            }
        )
    except Exception:
        return json.dumps(
            {"error": "Linear operation is unavailable. Reuse the same request key when retrying."}
        )


def register(ctx):
    from council_workspace.client import register as register_workspace
    from council_workspace.google_client import register as register_google

    register_google(ctx, toolset="council-linear")

    register_workspace(ctx, toolset="council-linear")
    for name, description, properties, required, path in (
        (
            "recurring_result",
            "Register the reviewed result of this scheduled issue. Coordinator only, after review. For outcome=report (default), provide the saved Notion page, exact readback revision and a short Russian summary for independent verification and delivery. If the schedule explicitly enables skip_empty and the completed research found no significant updates, use outcome=no_updates with summary and verification of the source checks; omit page/revision. This records an internal outcome with no Notion report or Telegram message. Failed or incomplete research is a blocker, never no_updates. Does not send messages or mark delivery successful.",
            {
                "outcome": {"type": "string", "enum": ["report", "no_updates"]},
                "page": {"type": "string"},
                "revision": {"type": "string"},
                "summary": {"type": "string", "maxLength": 700},
                "verification": {"type": "string", "maxLength": 2000},
            },
            ["summary"],
            "recurring-result",
        ),
        (
            "linear_issues",
            "Manage Linear tasks: context lists team states/projects/members/labels; list searches titles; get reads an issue and comments; create makes ordinary Backlog tasks without delegating them; update changes task fields; comment posts a note; start moves to In Progress; review submits a verified deliverable to In Review after child work finishes; accept lets only root Qui-Gon close verified low-risk internal tasks using get.acceptance.revision, category, result, verification, evidence HTTPS URL and artifact_version. Important/high-priority, owner-reserved, public, strategic, financial, production and security work remains for owner acceptance. Read artifacts before accepting; specialists cannot self-accept. Use start to return to In Progress before revisions. Legacy complete is a review alias, never Done. Qui-Gon manages allowed teams; all specialists can read, create and update tasks throughout their current project or shared parent task tree. project reads project materials, document reads a linked document, save_document creates or updates one using fields.title/content (document UUID only for updates), comments pages complete comments; use cursor and offset/next_offset for long content. Mutations require a stable key, reused on retries. Never claim a completed action without its receipt.",
            {
                "action": {
                    "type": "string",
                    "enum": [
                        "context",
                        "list",
                        "get",
                        "project",
                        "document",
                        "save_document",
                        "comments",
                        "create",
                        "update",
                        "comment",
                        "start",
                        "review",
                        "accept",
                        "complete",
                    ],
                },
                "issue": {
                    "type": "string",
                    "description": "Issue UUID or identifier; defaults to current task.",
                },
                "team": {
                    "type": "string",
                    "description": "Allowed team UUID; defaults to current task's team.",
                },
                "revision": {
                    "type": "string",
                    "description": "Exact acceptance.revision from get.",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "internal-research",
                        "internal-draft",
                        "documentation",
                        "sandbox-validation",
                        "internal-planning",
                    ],
                },
                "evidence": {"type": "string", "maxLength": 2048},
                "artifact_version": {"type": "string", "maxLength": 160},
                "document": {
                    "type": "string",
                    "description": "Linked document UUID for document action.",
                },
                "offset": {"type": "integer", "minimum": 0},
                "query": {"type": "string", "maxLength": 200},
                "cursor": {"type": "string"},
                "key": {"type": "string", "maxLength": 80},
                "fields": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "maxLength": 200},
                        "description": {"type": "string", "maxLength": 12000},
                        "content": {"type": "string", "maxLength": 12000},
                        "priority": {"type": "integer", "minimum": 0, "maximum": 4},
                        "stateId": {"type": "string"},
                        "projectId": {"type": ["string", "null"]},
                        "parentId": {"type": ["string", "null"]},
                        "assigneeId": {"type": ["string", "null"]},
                        "labelIds": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
                        "dueDate": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
                "body": {"type": "string", "maxLength": 10000},
                "result": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Concise outcome and deliverable link; no work log. Full text belongs in save_document.",
                },
                "verification": {
                    "type": "string",
                    "maxLength": 160,
                    "description": "One short sentence: acceptance check and any material limitation.",
                },
            },
            ["action"],
            "issues",
        ),
        (
            "linear_delegate",
            "Create or retrieve one idempotent sub-issue for an allowed specialist. Defaults to current task; parent_issue selects an active follow-up task when previous work is accepted. No new owner mention is needed. At most four specialist tasks can run concurrently; completed rounds do not consume that capacity. Reuse key when retrying; the current coordinator session receives the result automatically.",
            {
                "target": {"type": "string", "enum": ["dexter", "marketing", "padme"]},
                "key": {"type": "string", "maxLength": 80},
                "title": {"type": "string", "maxLength": 200},
                "description": {"type": "string", "maxLength": 12000},
                "parent_issue": {
                    "type": "string",
                    "description": "Active issue UUID or identifier in an allowed team; defaults to current issue.",
                },
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
            "linear_handoff",
            "Transfer a concise result or blocker with complete private evidence. submit stores one immutable event per run: reuse its key and identical payload on retry, then finish the turn. result requires details or artifacts; blocked requires next_action and wakes the coordinator without waiting for siblings. Summary plus rendered limitations/next_action must fit 700 characters; full deliverables belong in a verified artifact. submit does not change issue workflow: use linear_issues review separately for a verified result. get reads authorized event details in 6000-character pages; if checkpoint_available, view=checkpoint reads failed-run partial work in the same details field. Check revision and content_digest across pages; digest identifies the immutable event, not external truth or acceptance. resume is coordinator-only: after resolving the dependency, resume the latest blocked child event in the same session with a stable key and bounded instructions. Never use clarify for a dependency the coordinator can resolve.",
            {
                "action": {"type": "string", "enum": ["submit", "get", "resume"]},
                "key": {"type": "string", "minLength": 1, "maxLength": 80},
                "kind": {"type": "string", "enum": ["result", "blocked"]},
                "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                "details": {"type": "string", "maxLength": 24000},
                "artifacts": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "maxLength": 2048,
                                "description": "HTTPS artifact URL without embedded credentials.",
                            },
                            "version": {"type": "string", "minLength": 1, "maxLength": 160},
                        },
                        "required": ["url", "version"],
                        "additionalProperties": False,
                    },
                },
                "limitations": {"type": "string", "maxLength": 500},
                "next_action": {"type": "string", "maxLength": 300},
                "event_id": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "view": {
                    "type": "string",
                    "enum": ["details", "checkpoint"],
                    "description": "get only; defaults to details. checkpoint reads partial failed-run work.",
                },
                "message": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            ["action"],
            "handoff",
        ),
        (
            "linear_child_tasks",
            "Read delegated sub-issue states, concise results and handoff event references. Use linear_handoff get for complete evidence; a blocked child is unfinished and requires coordinator action.",
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
