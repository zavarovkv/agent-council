#!/usr/bin/env python3
"""File explicit Telegram early-access requests through the owner's Notion connection.

Run inside the existing Hermes container, as hermes. No model calls, Telegram
sends, credential copies, dashboard invitations or public tool grants are needed.
"""

import argparse
import fcntl
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


class SyncError(Exception):
    """A safe diagnostic, without source messages or credentials."""


def requested(text):
    if not isinstance(text, str) or not 0 < len(text) <= 6000:
        return False
    text = " ".join(text.casefold().replace("’", "'").split())
    if re.fullmatch(r"/waitlist(?:@[a-z0-9_]+)?", text):
        return True
    if re.search(r"\b(?:не хочу|не добавляйте|не записывайте|don't|do not|not interested)\b", text):
        return False
    intent = re.match(
        r"^(?:(?:привет|здравствуйте|hello|hi)[!,. ]+)?"
        r"(?:хочу\b|добавьте меня\b|запишите меня\b|прошу добавить\b|"
        r"i want\b|i'd like\b|i would like\b|please add me\b|add me\b)",
        text,
    )
    return bool(
        intent and re.search(r"ранн\w* доступ|early access|whitelist|waitlist|вайтлист", text)
    )


def private_file(path):
    if path.resolve() != path or not path.is_file() or path.stat().st_size > 16384:
        raise SyncError("invalid_private_configuration")
    if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise SyncError("configuration_must_be_private")
    return json.loads(path.read_text())


def settings(home):
    value = private_file(home / ".council/waitlist/config.json")
    if set(value) != {"schema", "data_source", "view", "since"} or value["schema"] != 1:
        raise SyncError("invalid_waitlist_configuration")
    for key, prefix in (("data_source", "collection://"), ("view", "view://")):
        if not isinstance(value[key], str) or not value[key].startswith(prefix):
            raise SyncError("invalid_waitlist_destination")
        value[key] = prefix + str(uuid.UUID(value[key][len(prefix) :]))
    if type(value["since"]) is not int or not 0 < value["since"] <= time.time():
        raise SyncError("invalid_waitlist_start")
    return value


def connect(path):
    if path.resolve() != path:
        raise SyncError("symlinked_waitlist_state")
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS applicants (
        telegram_id TEXT PRIMARY KEY, username TEXT NOT NULL, text TEXT NOT NULL,
        created REAL NOT NULL, state TEXT NOT NULL DEFAULT 'queued', notion_url TEXT
    )""")
    db.commit()
    path.chmod(0o600)
    return db


def capture(home, db, since):
    """Read only the verified public/business queues, never model or owner history."""
    captured = 0
    for filename in ("public-queue.sqlite3", "queue.sqlite3"):
        path = home / "plugin-data/council-secretary" / filename
        if not path.exists():
            continue
        if path.resolve() != path:
            raise SyncError("symlinked_source_queue")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
            source.row_factory = sqlite3.Row
            for row in source.execute(
                "SELECT chat,sender_username,text,created FROM jobs "
                "WHERE created>=? AND status!='cancelled' ORDER BY created,id",
                (since,),
            ):
                if (
                    not requested(row["text"])
                    or type(row["chat"]) is not int
                    or not 0 < row["chat"] < 2**52
                ):
                    continue
                username = row["sender_username"]
                if not isinstance(username, str) or not re.fullmatch(
                    r"[a-zA-Z0-9_]{0,32}", username
                ):
                    username = ""
                with db:
                    captured += db.execute(
                        "INSERT OR IGNORE INTO applicants(telegram_id,username,text,created) VALUES(?,?,?,?)",
                        (str(row["chat"]), username, row["text"], row["created"]),
                    ).rowcount
    return captured


class Notion:
    def __init__(self, call, config):
        self.call, self.config = call, config

    def invoke(self, name, args):
        response = self.call(name, args)
        if (
            not isinstance(response, dict)
            or response.get("ok") is not True
            or response.get("truncated")
        ):
            raise SyncError("notion_unavailable")
        value = response.get("result")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = {"text": value}
        if not isinstance(value, dict) or "error" in value or "async_task" in value:
            raise SyncError("notion_response_not_confirmed")
        return value

    def validate(self):
        source = self.invoke("notion-fetch", {"id": self.config["data_source"]})
        match = re.search(
            r"<data-source-state>\s*(.*?)\s*</data-source-state>", source.get("text", ""), re.S
        )
        if not match:
            raise SyncError("notion_schema_unrecognized")
        state = json.loads(match[1])
        expected = {
            "Заявитель": "title",
            "Telegram": "url",
            "Telegram ID": "text",
            "Дата заявки": "date",
            "Заявка": "text",
            "Статус": "select",
        }
        if any(
            state.get("schema", {}).get(key, {}).get("type") != kind
            for key, kind in expected.items()
        ):
            raise SyncError("notion_schema_changed")
        view = self.invoke("notion-fetch", {"id": self.config["view"]})
        match = re.search(r"<view\b[^>]*>\s*(.*?)\s*</view>", view.get("text", ""), re.S)
        if not match:
            raise SyncError("notion_view_unrecognized")
        config = json.loads(match[1])
        if config.get("dataSourceUrl", "").strip("{}") != self.config["data_source"] or any(
            "filter" in key.casefold() and value for key, value in config.items()
        ):
            raise SyncError("use_an_unfiltered_waitlist_view")

    def rows(self):
        result = {}
        for archived in (False, True):
            cursor, seen = None, set()
            for _ in range(100):
                data = {
                    "mode": "view",
                    "view_url": self.config["view"],
                    "page_size": 100,
                    "is_archived": archived,
                }
                if cursor:
                    data["start_cursor"] = cursor
                page = self.invoke("notion-query-data-sources", {"data": data})
                if (
                    not isinstance(page.get("results"), list)
                    or type(page.get("has_more")) is not bool
                ):
                    raise SyncError("notion_pagination_unrecognized")
                for row in page["results"]:
                    identity = row.get("Telegram ID")
                    if not isinstance(identity, str) or not identity.isdecimal():
                        continue
                    if identity in result:
                        raise SyncError("duplicate_remote_telegram_id")
                    result[identity] = row
                if not page["has_more"]:
                    break
                cursor = page.get("next_cursor")
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    raise SyncError("incomplete_notion_pagination")
                seen.add(cursor)
            else:
                raise SyncError("waitlist_view_too_large")
        return result

    def properties(self, record):
        username = record["username"]
        return {
            "Заявитель": "@" + username if username else "Telegram " + record["telegram_id"],
            "Telegram": "https://t.me/" + username if username else "",
            "Telegram ID": record["telegram_id"],
            "date:Дата заявки:start": datetime.fromtimestamp(record["created"], UTC).isoformat(),
            "date:Дата заявки:is_datetime": 1,
            "Статус": "Новая",
            "Заявка": record["text"][:1800],
        }

    def verify(self, url, record):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "app.notion.com",
            "www.notion.so",
            "notion.so",
        }:
            raise SyncError("invalid_notion_receipt")
        page = self.invoke("notion-fetch", {"id": url})
        text = page.get("text", "")
        parent = re.search(r'<parent-data-source\b[^>]*url="([^"]+)"', text)
        match = re.search(r"<properties>\s*(.*?)\s*</properties>", text, re.S)
        if not parent or parent[1].strip("{}") != self.config["data_source"] or not match:
            raise SyncError("notion_row_parent_mismatch")
        properties = json.loads(match[1])
        if (
            properties.get("Telegram ID") != record["telegram_id"]
            or properties.get("Заявка") != record["text"][:1800]
        ):
            raise SyncError("notion_row_readback_mismatch")
        if len(record["text"]) > 1800:
            content = re.search(r"<content>\s*(.*?)\s*</content>", text, re.S)
            if not content or record["text"].replace("\r\n", "\n") not in content[1].replace(
                "\r\n", "\n"
            ):
                raise SyncError("notion_full_request_readback_mismatch")
        return url

    def create(self, record):
        page = {"properties": self.properties(record)}
        if len(record["text"]) > 1800:
            # Literal request data cannot introduce Notion blocks or instructions.
            fence = "`" * max(
                3, 1 + max((len(run) for run in re.findall(r"`+", record["text"])), default=0)
            )
            page["content"] = fence + "text\n" + record["text"] + "\n" + fence
        result = self.invoke(
            "notion-create-pages",
            {
                "parent": {"data_source_id": self.config["data_source"]},
                "allow_async": False,
                "pages": [page],
            },
        )
        pages = result.get("pages")
        if (
            not isinstance(pages, list)
            or len(pages) != 1
            or not isinstance(pages[0].get("url"), str)
        ):
            raise SyncError("notion_create_receipt_unrecognized")
        return pages[0]["url"]


def sync(db, notion):
    pending = db.execute(
        "SELECT * FROM applicants WHERE state!='saved' ORDER BY created LIMIT 50"
    ).fetchall()
    if not pending:
        return {"saved": 0, "pending": 0}
    notion.validate()
    remote, saved = notion.rows(), 0
    for record in pending:
        row = remote.get(record["telegram_id"])
        if row is not None:
            url = notion.verify(row["url"], record)
        elif record["notion_url"]:
            url = notion.verify(record["notion_url"], record)
        elif record["state"] == "creating":
            # No Notion idempotency key: reconcile an uncertain create, never replay it.
            continue
        else:
            with db:
                db.execute(
                    "UPDATE applicants SET state='creating' WHERE telegram_id=?",
                    (record["telegram_id"],),
                )
            url = notion.create(record)
            with db:
                db.execute(
                    "UPDATE applicants SET notion_url=? WHERE telegram_id=?",
                    (url, record["telegram_id"]),
                )
            notion.verify(url, record)
        with db:
            db.execute(
                "UPDATE applicants SET state='saved',notion_url=? WHERE telegram_id=?",
                (url, record["telegram_id"]),
            )
        saved += 1
    remaining = db.execute("SELECT count(*) FROM applicants WHERE state!='saved'").fetchone()[0]
    return {"saved": saved, "pending": remaining}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify the destination without saving messages"
    )
    args = parser.parse_args()
    os.umask(0o077)
    home = Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()
    config = settings(home)
    root = home / ".council/waitlist"
    with os.fdopen(
        os.open(root / "sync.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w"
    ) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"busy": True}))
            return
        # Keep credentials and MCP discovery in the already authorized main profile.
        sys.path.insert(0, "/opt/hermes")
        from council_workspace.service import native_notion

        notion = Notion(native_notion, config)
        if args.check:
            notion.validate()
            notion.rows()
            print(json.dumps({"destination_verified": True}))
            return
        with closing(connect(root / "requests.sqlite3")) as db:
            captured = capture(home, db, config["since"])
            result = sync(db, notion)
        print(json.dumps({"captured": captured, **result}))
        if result["pending"]:
            raise SyncError("unconfirmed_requests_require_reconciliation")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        reason = str(error) if isinstance(error, SyncError) else "waitlist_sync_not_confirmed"
        print(json.dumps({"error": reason}), file=sys.stderr)
        raise SystemExit(1) from None
