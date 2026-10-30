"""Thin adapter to completed native Kanban tasks and their reviewed artifacts."""

import fcntl
import hashlib
import html
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .notion import Notion, compatible_table, page_id
from .store import FilingError, Store


def home_for(ctx):
    from hermes_constants import get_default_hermes_root, get_hermes_home

    home = get_hermes_home().resolve()
    if ctx.get_config("enabled", False) is not True or home != get_default_hermes_root().resolve():
        raise FilingError("coordinator_only")
    return home


@contextmanager
def locked(store):
    with os.fdopen(
        os.open(store.root / "operations.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a"
    ) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def board_for(ctx):
    board = ctx.get_config("board", "research")
    if not isinstance(board, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", board):
        raise FilingError("invalid_research_board")
    return board


def identifier(board, task):
    if not isinstance(task, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task):
        raise FilingError("invalid_research_task")
    return hashlib.sha256(f"{board}:{task}".encode()).hexdigest()


def destination(home):
    path = home / "council-workspace.json"
    if path.resolve() != path or not path.is_file() or path.stat().st_size > 65536:
        raise FilingError("invalid_workspace_settings")
    value = json.loads(path.read_text())
    knowledge = value.get("knowledge_base", {})
    if knowledge.get("provider") != "notion":
        raise FilingError("notion_destination_not_configured")
    return page_id(knowledge.get("research", {}).get("page_id"))


def notion_for(ctx):
    home_for(ctx)
    from tools.mcp_tool_discovery import discover_mcp_tools

    # Plugin CLI commands do not run the agent's MCP startup. This native,
    # idempotent path retains its trust/OAuth gates and starts only Notion.
    discover_mcp_tools(allowed_mcp_names=["notion"])
    return Notion(lambda name, args: ctx.call_mcp("notion", name, args, timeout=30))


def native_task(ctx, task_id, run_id=None):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    board = board_for(ctx)
    if not kb.board_exists(board):
        raise FilingError("research_board_not_configured")
    with connect_closing(board=board) as db:
        task = kb.get_task(db, task_id)
        if task is None or task.status != "done" or task.assignee != "dexter":
            raise FilingError("complete_and_review_dexter_task_first")
        runs = [
            run
            for run in kb.list_runs(db, task_id, include_active=False)
            if run.profile == "dexter" and run.outcome in {"completed", "review_requested"}
        ]
        run = next((run for run in reversed(runs) if run_id is None or run.id == run_id), None)
        if run is None:
            raise FilingError("completed_research_run_required")
    return task, run


def stage(ctx, store, task_id, relative):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    home = home_for(ctx)
    task, run = native_task(ctx, task_id)
    if (
        not isinstance(relative, str)
        or not relative
        or relative.startswith("/")
        or ".." in relative.split("/")
    ):
        raise FilingError("invalid_report_path")
    path = home / "profiles/dexter/research" / relative
    if (
        path.resolve() != path
        or not path.is_file()
        or not 0 < path.stat().st_size <= 32768
        or path.suffix != ".md"
    ):
        raise FilingError("invalid_report_path_or_size")
    report = path.read_text()
    if re.search(r"<(?:page|database|data-source|content)\b", report, re.I):
        raise FilingError("structural_notion_tags_in_report")
    title = " ".join(task.title.split())[:160]
    date = datetime.fromtimestamp(run.ended_at, UTC).date().isoformat()
    key = identifier(board_for(ctx), task_id)
    body = (
        f"# {html.escape(title)}\n\nДата: {date}\n\nАвтор: Dexter Jettster\n\nПроверил: Qui-Gon Jinn\n\n"
        f"Задача: {task_id} / запуск: {run.id}\n\nCouncil Research ID: {key}\n\n"
        + report.strip()
        + "\n"
    )
    record = store.stage(
        {
            "id": key,
            "task_id": task_id,
            "run_id": run.id,
            "board": board_for(ctx),
            "parent": destination(home),
            "title": title,
            "date": date,
            "body": body,
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
        }
    )
    artifacts = store.root / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    if artifacts.resolve() != artifacts:
        raise FilingError("invalid_artifact_path")
    artifact = artifacts / (record["sha256"] + ".md")
    if artifact.is_symlink():
        raise FilingError("invalid_artifact_path")
    if not artifact.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=artifacts, prefix=".artifact-", delete=False
            ) as output:
                temporary = Path(output.name)
                os.fchmod(output.fileno(), 0o600)
                output.write(body.encode())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, artifact)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != record["sha256"]:
        raise FilingError("artifact_changed")
    with connect_closing(board=record["board"]) as db:
        if not any(item.stored_path == str(artifact) for item in kb.list_attachments(db, task_id)):
            kb.add_attachment(
                db,
                task_id,
                filename=artifact.name,
                stored_path=str(artifact),
                content_type="text/markdown",
                size=artifact.stat().st_size,
                uploaded_by="council-research",
            )
    return record


def process(ctx, store, record):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    native_task(ctx, record["task_id"], record["run_id"])
    if destination(home_for(ctx)) != record["parent"]:
        raise FilingError("destination_changed")
    notion = notion_for(ctx)
    result = notion.file(store, record)
    comment = (
        f"Проверенный отчёт: {result['url']}\nCouncil filing: {record['id']} / {record['sha256']}"
    )
    with connect_closing(board=record["board"]) as db:
        if not any(item.body == comment for item in kb.list_comments(db, record["task_id"])):
            kb.add_comment(db, record["task_id"], "Qui-Gon Jinn", comment)
    store.save({**store.get(record["id"]), "state": "done"})
    return result


def execute(ctx, arguments):
    record = None
    try:
        if not isinstance(arguments, dict) or set(arguments) - {"action", "task_id", "report_path"}:
            raise FilingError("invalid_filing_request")
        action = arguments.get("action")
        task = arguments.get("task_id")
        home = home_for(ctx)
        store = Store(home)
        key = identifier(board_for(ctx), task)
        with locked(store):
            if action == "status":
                record = store.get(key)
                return {
                    "configured": True,
                    "task_id": task,
                    "state": record["state"] if record else "not_queued",
                    "url": "https://www.notion.so/" + record["page_id"]
                    if record and record.get("page_id")
                    else None,
                }
            if action != "file":
                raise FilingError("invalid_filing_request")
            record = (
                stage(ctx, store, task, arguments["report_path"])
                if arguments.get("report_path")
                else store.get(key)
            )
            if record is None:
                raise FilingError("report_path_required_for_first_filing")
            return process(ctx, store, record)
    except FilingError as error:
        return {"filed": False, "error": str(error), "local_artifact_retained": record is not None}
    except BlockingIOError:
        return {"filed": False, "error": "filing_busy"}
    except Exception:
        return {"filed": False, "error": "filing_failed_private_details_omitted"}


def flush(ctx):
    store = Store(home_for(ctx))
    completed = failed = 0
    with locked(store):
        with store.database() as db:
            records = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM filings WHERE state!='done' ORDER BY updated LIMIT 1"
                )
            ]
        for record in records:
            if record["state"] == "done":
                continue
            try:
                process(ctx, store, record)
                completed += 1
            except Exception:
                store.save(
                    {
                        **(store.get(record["id"]) or record),
                        "last_error": "filing_unavailable_or_conflicted",
                    }
                )
                failed += 1
            if completed + failed >= 1:
                break
    with store.database() as db:
        pending = db.execute("SELECT count(*) FROM filings WHERE state!='done'").fetchone()[0]
    return {
        "completed": completed,
        "pending_or_conflicted": pending,
        "checked_at": int(time.time()),
    }


def verify(ctx):
    home = home_for(ctx)
    notion = notion_for(ctx)
    parent = notion.fetch(destination(home))
    return {
        "notion_ready": True,
        "index_available": compatible_table(parent["content"]) is not None,
        "cloud_writes": False,
    }
