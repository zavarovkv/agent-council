"""Deliver only a current, reviewed report whose Notion filing was read back."""

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .evidence import ZONE, daily_window

WORKSPACE = Path("/workspace/monitoring/telegram")


def read_json(path, limit=65536):
    path = Path(path)
    if path.resolve() != path or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Missing or unsafe monitor artifact.")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("An object artifact is required.")
    return value


def report(home, workspace, now):
    config = read_json(home / ".council/telegram-monitor.json")
    if config.get("schema") != 1 or config.get("enabled") is not True:
        raise ValueError("Monitoring is not active.")
    start, end = daily_window(now)
    day = end.astimezone(ZONE).date().isoformat()
    ready = read_json(workspace / "reports" / (day + ".json"))
    if (
        ready.get("schema") != 1
        or ready.get("window_start") != start.isoformat()
        or ready.get("window_end") != end.isoformat()
    ):
        raise ValueError("The report belongs to a different window.")
    task = ready.get("task_id")
    summary = ready.get("summary")
    if (
        not isinstance(task, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task)
        or not isinstance(summary, str)
        or not 1 <= len(summary.strip()) <= 3000
    ):
        raise ValueError("The report has no bounded summary or real task reference.")
    identity = hashlib.sha256(f"research:{task}".encode()).hexdigest()
    database = home / ".council/research-filing/outbox.sqlite3"
    if database.resolve() != database or not database.is_file():
        raise ValueError("The filing ledger is unavailable.")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT state,payload FROM filings WHERE id=?", (identity,)).fetchone()
    if not row or row[0] != "done" or len(row[1]) > 1048576:
        raise ValueError("The Notion filing is not verified.")
    filing = json.loads(row[1])
    destination = read_json(home / "council-workspace.json")["knowledge_base"]["research"][
        "page_id"
    ]
    if (
        filing.get("state") != "done"
        or filing.get("task_id") != task
        or filing.get("date") != day
        or filing.get("created_at", 0) < end.timestamp()
        or uuid.UUID(filing["parent"]) != uuid.UUID(destination)
    ):
        raise ValueError("The filing is not this day's reviewed report in the configured section.")
    url = "https://www.notion.so/" + str(uuid.UUID(filing["page_id"]))
    source_status = (
        "Telegram: источник недоступен; отсутствие данных не означает отсутствие событий."
    )
    try:
        evidence = read_json(workspace / "raw" / (day + ".json"), 2 * 1048576)
        if (
            evidence.get("peer_id") == config["peer_id"]
            and evidence.get("window_end") == end.isoformat()
        ):
            if (
                evidence.get("coverage") == "full_text_window"
                and evidence.get("status") == "complete"
            ):
                source_status = (
                    "Telegram: текст и подписи за период получены; вложения не анализировались."
                )
            elif evidence.get("status") == "incomplete":
                source_status = "Telegram: текстовая выборка неполная; выводы по каналу ограничены."
    except (OSError, ValueError):
        pass
    return f"Обзор конкурентов — {day}\n\n{summary.strip()}\n\n{source_status}\n\n{url}"


def main():
    now = datetime.now(UTC)
    try:
        text = report(Path(os.environ["HERMES_HOME"]), WORKSPACE, now)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        day = now.astimezone(ZONE).date().isoformat()
        text = f"Обзор конкурентов за {day} пока не готов: нет подтверждённой публикации в Notion. Следующий шаг Квай-Гона — проверить текущую задачу на доске research и состояние research_archive, завершить или уточнить блокирующий шаг."
    print(text)


if __name__ == "__main__":
    main()
