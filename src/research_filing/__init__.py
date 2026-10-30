"""Coordinator-only research filing through native Kanban and MCP contracts."""

import json

from .native import execute, flush, home_for
from .store import FilingError


def register(ctx):
    def available():
        try:
            home_for(ctx)
            return True
        except FilingError:
            return False

    def handle(arguments, **_kwargs):
        return json.dumps(execute(ctx, arguments), ensure_ascii=False)

    ctx.register_tool(
        name="research_archive",
        toolset="council_research",
        handler=handle,
        check_fn=available,
        schema={
            "name": "research_archive",
            "description": "File a reviewed, completed Dexter Kanban research task to the configured Notion research section and verify its index row. Retry a queued filing without report_path; never repeat research for a filing failure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["file", "status"]},
                    "task_id": {"type": "string"},
                    "report_path": {
                        "type": "string",
                        "description": "Relative Markdown path inside Dexter's research directory. Required for a new/revised report.",
                    },
                },
                "required": ["action", "task_id"],
                "additionalProperties": False,
            },
        },
    )

    def setup(parser):
        parser.add_argument("action", choices=["flush"])

    def cli(_args):
        print(json.dumps(flush(ctx)))

    ctx.register_cli_command(
        name="research-filing",
        help="Resume queued research filing without model calls",
        setup_fn=setup,
        handler_fn=cli,
    )
