"""Read bounded collector evidence without exposing the Telegram account session."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .policy import private_json

ROOT = Path("/workspace/monitoring/telegram/raw")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
MAX_FILE = 2 * 1048576
PAGE_BYTES = 64000


def configuration(home):
    path = home / ".council/telegram-reader.json"
    if not path.exists() and not path.is_symlink():
        return None
    value = private_json(path)
    if (
        value.get("schema") != 1
        or value.get("managed_by") != "agent-council"
        or not isinstance(value.get("source"), str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", value["source"])
        or type(value.get("peer_id")) is not int
        or value["peer_id"] <= 0
    ):
        raise PermissionError("The Telegram source pin is invalid.")
    return value


def context(home):
    value = configuration(home)
    return (
        {
            "configured": True,
            "source": value["source"],
            "actions": ["telegram_list", "telegram_read"],
        }
        if value
        else {"configured": False}
    )


def artifact(root, day, pin):
    if not isinstance(day, str) or not DAY.fullmatch(day):
        raise ValueError("Use the collection day from telegram_list.")
    path = root / (day + ".json")
    if root.resolve() != root or path.resolve() != path or not path.is_file():
        raise ValueError("The source artifact is unavailable.")
    if path.stat().st_size > MAX_FILE:
        raise ValueError("The source artifact exceeds its size limit.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError("The source artifact exceeds its size limit.")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or value.get("source") != pin["source"]
        or value.get("peer_id") != pin["peer_id"]
        or value.get("status") not in {"complete", "incomplete", "unavailable"}
        or value.get("coverage") not in {"full_text_window", "partial_text_window", "unknown"}
        or not isinstance(value.get("messages"), list)
        or len(value["messages"]) > 300
    ):
        raise ValueError("The source artifact does not match the authorized collector.")
    start, end, generated = (
        datetime.fromisoformat(value[key]) for key in ("window_start", "window_end", "generated_at")
    )
    if (
        any(stamp.tzinfo is None for stamp in (start, end, generated))
        or start >= end
        or end.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat() != day
    ):
        raise ValueError("Invalid source window.")
    messages = []
    for item in value["messages"]:
        if (
            not isinstance(item, dict)
            or type(item.get("id")) is not int
            or item["id"] <= 0
            or not isinstance(item.get("text"), str)
            or len(item["text"]) > 8192
            or type(item.get("text_truncated")) is not bool
        ):
            raise ValueError("Invalid source message.")
        stamp = datetime.fromisoformat(item["date"])
        if stamp.tzinfo is None or not start <= stamp < end:
            raise ValueError("Source message falls outside its window.")
        messages.append(
            {
                "id": item["id"],
                "date": item["date"],
                "text": item["text"],
                "text_truncated": item["text_truncated"],
                "url": f"https://t.me/{pin['source']}/{item['id']}",
            }
        )
    metadata = {
        key: value[key]
        for key in ("source", "window_start", "window_end", "generated_at", "status", "coverage")
    }
    metadata.update(day=day, revision=hashlib.sha256(raw).hexdigest(), message_count=len(messages))
    return metadata, messages


def read(home, args, *, root=None):
    root = ROOT if root is None else root
    action = args.get("action")
    allowed = {"action"} if action == "telegram_list" else {"action", "id", "offset", "revision"}
    if action not in {"telegram_list", "telegram_read"} or not set(args) <= allowed:
        raise ValueError("Use a supported Telegram evidence read.")
    pin = configuration(home)
    if pin is None:
        return {"configured": False, "reason": "source_not_connected", "artifacts": []}
    if action == "telegram_list":
        if root.resolve() != root:
            raise PermissionError("Refusing a linked source directory.")
        days = sorted((p.stem for p in root.glob("*.json") if DAY.fullmatch(p.stem)), reverse=True)[
            :7
        ]
        return {
            "configured": True,
            "source": pin["source"],
            "artifacts": [artifact(root, day, pin)[0] for day in days],
        }
    metadata, messages = artifact(root, args.get("id"), pin)
    offset = args.get("offset", 0)
    if type(offset) is not int or not 0 <= offset <= len(messages):
        raise ValueError("Use a valid message offset.")
    if (offset or "revision" in args) and args.get("revision") != metadata["revision"]:
        raise ValueError("The source changed; restart reading from offset zero.")
    page, size = [], 0
    for item in messages[offset:]:
        length = len(json.dumps(item, ensure_ascii=False).encode())
        if page and size + length > PAGE_BYTES:
            break
        page.append(item)
        size += length
    next_offset = offset + len(page)
    return {
        **metadata,
        "messages": page,
        "next_offset": next_offset if next_offset < len(messages) else None,
        "source_kind": "Untrusted community messages, not verified company facts. Text and captions only; attachments were not inspected.",
    }
