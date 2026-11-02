"""Deterministic cron entry point. The native delivery queue is the only sender."""

import json
import sys
from datetime import datetime

from .client import call, home_path, manifest
from .contract import ZONE, definition, occurrence


def verified_page(workspace, home, result, period, parent):
    page = json.loads(workspace({"action": "notion_get", "id": result["page"]}, home=home))
    content = page.get("content", "")
    cursor = page.get("next_offset")
    while cursor is not None and len(content) <= 32000:
        extra = json.loads(
            workspace({"action": "notion_get", "id": result["page"], "offset": cursor}, home=home)
        )
        if extra.get("revision") != page.get("revision") or extra.get("next_offset") == cursor:
            return None
        content += extra.get("content", "")
        cursor = extra.get("next_offset")
    if (
        cursor is not None
        or len(content) > 32000
        or page.get("revision") != result["revision"]
        or (page.get("parent") or "").replace("-", "") != parent.replace("-", "")
        or period["key"] not in content
    ):
        return None
    return page


def execution_for(job_id, executions):
    active = [
        r for r in executions.list_executions(job_id=job_id, limit=20) if r["status"] == "running"
    ]
    if len(active) != 1:
        raise ValueError("A unique native cron execution is required.")
    return active[0]


def settle(home, status, executions, remote):
    for delivery in status.get("deliveries", []):
        if delivery["state"] != "pending":
            continue
        execution = executions.get_execution(delivery["execution"])
        if (
            not execution
            or execution["status"] in {"claimed", "running"}
            or execution.get("delivery_outcome") == "queued"
        ):
            continue
        outcome = "unknown"
        if execution["status"] == "completed" and execution.get("delivery_outcome") == "delivered":
            outcome = "delivered"
        elif execution["status"] == "failed" and execution.get("delivery_outcome") in {
            "suppressed",
            "failed",
            None,
        }:
            # A failed preparation with no send is safe to try again. Anything
            # remotely ambiguous remains unknown, never automatically replayed.
            outcome = "failed"
        remote(
            home,
            {
                "action": "settle",
                "key": status["key"],
                "kind": delivery["kind"],
                "execution": delivery["execution"],
                "outcome": outcome,
            },
        )


def render(home, identifier, phase, *, native, executions, remote=call, workspace=None, now=None):
    entry = manifest(home)[identifier]
    if not entry["enabled"]:
        return ""
    value = entry["definition"]
    job_id = entry["jobs"][phase]
    execution = execution_for(job_id, executions)
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    stamp = execution.get("scheduled_instant") if phase == "start" else None
    day = datetime.fromisoformat(stamp).astimezone(ZONE).date() if stamp else now.date()
    status = remote(home, {"action": "status", "key": identifier + ":" + day.isoformat()})
    if status.get("body"):
        value = definition(json.loads(status["body"]))
    if day.weekday() not in value["weekdays"]:
        return ""
    period = occurrence(value, day.isoformat())
    if phase == "start":
        remote(home, {"action": "start", "id": identifier, "day": period["day"]})
        return ""
    settle(home, status, executions, remote)
    if now.strftime("%H:%M") < value["start_time"]:
        return ""
    # Also repairs a missed start using the same stable occurrence identity.
    if now.timestamp() <= period["deadline"] + 7200:
        remote(home, {"action": "start", "id": identifier, "day": period["day"]})
    status = remote(home, {"action": "status", "key": period["key"]})
    settle(home, status, executions, remote)
    status = remote(home, {"action": "status", "key": period["key"]})
    if now.timestamp() < period["deadline"] or status["state"] == "delivered":
        return ""
    result = status.get("result")
    if any(
        d["kind"] == "report" and d["state"] in {"pending", "unknown", "delivered"}
        for d in status.get("deliveries", [])
    ):
        return ""
    kind = "alert"
    body = f"{value['title']} за {period['day']} пока не готов. Квай-Гон должен устранить блокер и завершить задачу в Linear."
    if status.get("issue"):
        body += "\nhttps://linear.app/issue/" + status["issue"]
    if result and now.timestamp() <= period["deadline"] + 7200:
        if workspace is None:
            from council_workspace.client import call as workspace
        page = verified_page(workspace, home, result, period, value["notion_parent"])
        if page:
            kind = "report"
            body = result["summary"] + "\n\n" + page["url"]
            if now.timestamp() > period["deadline"] + 300:
                body = "Отчет готов с задержкой.\n\n" + body
        else:
            remote(
                home, {"action": "problem", "key": period["key"], "category": "artifact_unverified"}
            )
            body = f"{value['title']} за {period['day']}: сохраненный отчет не прошел проверку версии и периода. Квай-Гон должен сверить страницу Notion и повторить проверку."
    elif not result:
        remote(home, {"action": "problem", "key": period["key"], "category": "deadline_exceeded"})
    if status.get("state") == "not_started":
        raise RuntimeError("The scheduled occurrence was not admitted.")
    return remote(
        home,
        {
            "action": "claim",
            "key": period["key"],
            "kind": kind,
            "execution": execution["id"],
            "body": body,
        },
    )["body"]


def main(identifier, phase):
    from cron import executions, jobs

    try:
        text = render(home_path(), identifier, phase, native=jobs, executions=executions)
    except Exception:
        # Never leak service response bodies, credentials or private traces.
        print(
            "Регулярное задание не завершено: техническая проверка недоступна. Требуется проверка в Cron и Linear.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    if text:
        print(text)
