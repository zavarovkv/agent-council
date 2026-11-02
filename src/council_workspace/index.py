"""Rebuildable navigation from provider trees, with no document-body replication."""

import base64
import hashlib
import html
import json
import re
import sqlite3
import threading
import time
from contextlib import closing

from research_filing.notion import page_id

from .drive import Files
from .notion import Pages
from .policy import private_json, server_policy

MARKER = "Council navigation index v1"
TITLE = "Навигация"
MAX_ITEMS = 500


def label(value):
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()[:240]
    return re.sub(r"([\\`*_{}\[\]()<>|])", r"\\\1", value)


class State:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS navigation_state (key TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )

    def read(self):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT body FROM navigation_state WHERE key='index'").fetchone()
        return json.loads(row[0]) if row else {}

    def update(self, **fields):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM navigation_state WHERE key='index'").fetchone()
            value = json.loads(row[0]) if row else {}
            value.update(fields)
            db.execute(
                "INSERT OR REPLACE INTO navigation_state VALUES('index',?)", (json.dumps(value),)
            )
        return value


class Navigation:
    def __init__(self, workspace):
        self.workspace = workspace
        self.state = State(workspace.store.path)
        self.lock = threading.Lock()
        self.running = False

    def status(self):
        config = self.workspace.home / ".council/workspace/index-config.json"
        return {"configured": config.exists(), "running": self.running, **self.state.read()}

    def dirty(self):
        self.state.update(dirty=True, dirty_at=time.time())

    def start(self):
        with self.lock:
            config = private_json(self.workspace.home / ".council/workspace/index-config.json")
            if config.get("enabled") is not True:
                raise PermissionError("Workspace indexing is not enabled.")
            if not self.running:
                self.running = True
                self.state.update(started=time.time())
                threading.Thread(target=self.run, name="council-navigation", daemon=True).start()
        return self.status()

    def operation(self, perform):
        # Yield between cloud operations so intake and editing can continue.
        with self.workspace.lock:
            server_policy(self.workspace.home)
            return perform()

    def drive_tree(self, files, excluded):
        pending, seen, rows = [(files.root, "")], set(), []
        while pending:
            identity, prefix = pending.pop(0)
            if identity in seen:
                raise ValueError("Drive folder cycle or alias requires review.")
            seen.add(identity)
            cursor = None
            while True:
                result = self.operation(
                    lambda identity=identity, cursor=cursor: files.read(
                        {"action": "drive_list", "id": identity, "cursor": cursor}
                    )
                )
                if result.get("incompleteSearch"):
                    raise ValueError("Drive returned an incomplete listing.")
                for item in result.get("files", []):
                    if item["id"] in excluded:
                        continue
                    name = prefix + item["name"]
                    folder = item["mimeType"] == "application/vnd.google-apps.folder"
                    url = (
                        item.get("webViewLink")
                        or (
                            "https://drive.google.com/drive/folders/"
                            if folder
                            else "https://drive.google.com/file/d/"
                        )
                        + item["id"]
                    )
                    if not url.startswith(
                        ("https://drive.google.com/", "https://docs.google.com/")
                    ):
                        raise ValueError("Unexpected Drive link.")
                    rows.append((name, "Папка" if folder else item["mimeType"], url))
                    if len(rows) > MAX_ITEMS:
                        raise ValueError("Navigation exceeds the supported tree size.")
                    if folder:
                        pending.append((item["id"], name + " / "))
                following = result.get("nextPageToken")
                if not following:
                    break
                if following == cursor:
                    raise ValueError("Drive pagination did not advance.")
                cursor = following
        return sorted(rows)

    def notion_tree(self, pages, excluded):
        pending, seen, rows = [(pages.root, "", None)], set(), []
        while pending:
            identity, prefix, parent = pending.pop(0)
            if identity in seen:
                continue
            seen.add(identity)
            page = self.operation(lambda identity=identity: pages.fetch(identity))
            if parent is not None and page.get("parent") != parent:
                # Embedded references are not proof of hierarchy membership.
                continue
            if prefix:
                rows.append((prefix, "Страница", "https://www.notion.so/" + identity))
            content = re.sub(r"```.*?```", "", page["content"], flags=re.S)
            for kind, attributes, title in re.findall(
                r"<(page|database)\b([^>]*)>([^<]*)</\1>", content, re.S
            ):
                target = re.search(r'\burl="([^"]+)"', attributes)
                if not target:
                    raise ValueError("A Notion child is missing its identity.")
                child = page_id(target[1])
                if child in excluded:
                    continue
                name = (prefix + " / " if prefix else "") + html.unescape(title).strip()
                if kind == "page":
                    pending.append((child, name, identity))
                else:
                    # Database rows remain in the native database; no contact or
                    # request records are copied into a navigation document.
                    rows.append((name, "База данных", "https://www.notion.so/" + child))
            if len(rows) + len(pending) > MAX_ITEMS:
                raise ValueError("Navigation exceeds the supported tree size.")
        return sorted(rows)

    @staticmethod
    def content(notion, drive, stamp, *, native=False):
        parts = [
            MARKER,
            "",
            f"Автоматический индекс. Сверка: {stamp}. Обновляется раз в сутки; вручную править не нужно.",
            "",
            "Базы данных перечислены целиком; их записи и вложения внутри документов открываются в самом источнике. Индекс не подтверждает содержание материалов.",
            "",
            "## Notion",
            "",
        ]
        for rows in (notion, drive):
            if rows is drive:
                parts.extend(["", "## Google Drive", ""])
            for name, kind, url in rows:
                if native and rows is notion:
                    # Notion turns page hyperlinks into mentions and drops their
                    # custom labels. Preserve the full path outside the mention.
                    reference = "https://app.notion.com/p/" + page_id(url)
                    parts.append(
                        f'- {label(name)} – <mention url="{reference}" /> – {label(kind)}'
                    )
                else:
                    parts.append(f"- [{label(name)}]({url}) – {label(kind)}")
            if not rows:
                parts.append("Нет вложенных материалов.")
        result = "\n".join(parts)
        if len(result) > 30000:
            raise ValueError("Split the navigation before exceeding the page limit.")
        return result

    def write(self, provider, args):
        args["key"] = (
            "navigation-"
            + hashlib.sha256(
                json.dumps(args, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
        )
        return self.operation(
            lambda: self.workspace.store.write(
                "system-navigation", args, lambda: provider.prepare_write(args)
            )
        )

    def refresh(self):
        started = time.time()
        policy = server_policy(self.workspace.home)
        config = private_json(self.workspace.home / ".council/workspace/index-config.json")
        if (
            config.get("notion_root") != policy["notion_root"]
            or config.get("drive_root") != policy["drive_root"]
        ):
            raise PermissionError("Reconcile changed index destinations.")
        pages = Pages(self.workspace.notion_call, policy["notion_root"])
        files = Files(
            self.operation(lambda: self.workspace.drive_factory(self.workspace.home)),
            policy["drive_root"],
        )
        previous = self.state.read()
        root = self.operation(lambda: pages.fetch(pages.root))
        # Recover identities from the actual roots after an interrupted create.
        candidates = [
            page_id(url)
            for url, title in re.findall(
                r'<page\b[^>]*\burl="([^"]+)"[^>]*>([^<]*)</page>', root["content"], re.S
            )
            if html.unescape(title).strip() == TITLE
        ]
        if len(candidates) > 1:
            raise ValueError("Duplicate navigation pages require review.")
        notion_id = candidates[0] if candidates else None
        if previous.get("notion_id") and notion_id != previous["notion_id"]:
            raise ValueError("Navigation page was moved or removed; reconcile its destination.")
        existing_notion = self.operation(lambda: pages.fetch(notion_id)) if notion_id else None
        if existing_notion and MARKER not in existing_notion["content"]:
            raise ValueError("An existing user navigation page must not be overwritten.")
        root_files, cursor = [], None
        while True:
            result = self.operation(
                lambda cursor=cursor: files.read({"action": "drive_list", "cursor": cursor})
            )
            root_files.extend(result.get("files", []))
            following = result.get("nextPageToken")
            if not following:
                break
            if following == cursor or len(root_files) > MAX_ITEMS:
                raise ValueError("Drive root pagination is incomplete.")
            cursor = following
        matches = [f for f in root_files if f["name"] == "INDEX.md"]
        if len(matches) > 1:
            raise ValueError("Duplicate Drive indices require review.")
        drive_id = matches[0]["id"] if matches else None
        if previous.get("drive_id") and drive_id != previous["drive_id"]:
            raise ValueError("Drive index was moved or removed; reconcile its destination.")
        if drive_id:
            sample = self.operation(lambda: files.read({"action": "drive_read", "id": drive_id}))
            if MARKER.encode() not in base64.b64decode(sample["base64"]):
                raise ValueError("An existing user INDEX.md must not be overwritten.")
        notion = self.notion_tree(pages, {notion_id})
        drive = self.drive_tree(files, {drive_id})
        from datetime import datetime
        from zoneinfo import ZoneInfo

        stamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M МСК")
        body = self.content(notion, drive, stamp)
        drive_args = {
            "action": "drive_update" if drive_id else "drive_create",
            "title": "INDEX.md",
            "content": body,
            "mime_type": "text/markdown",
        }
        if drive_id:
            drive_args.update(id=drive_id, revision=matches[0]["version"])
        saved_drive = self.write(files, drive_args)
        self.state.update(drive_id=saved_drive["id"], drive_url=saved_drive.get("webViewLink"))
        notion_args = {
            "action": "notion_update" if notion_id else "notion_create",
            "title": TITLE,
            "content": self.content(notion, drive, stamp, native=True),
        }
        if notion_id:
            from .notion import revision

            notion_args.update(id=notion_id, revision=revision(existing_notion["content"]))
        saved_notion = self.write(pages, notion_args)
        self.state.update(
            notion_id=saved_notion["id"],
            notion_url=saved_notion["url"],
            last_success=time.time(),
            dirty=self.state.read().get("dirty_at", 0) > started,
            error="",
            notion_count=len(notion),
            drive_count=len(drive),
        )

    def run(self):
        try:
            self.refresh()
        except Exception:
            self.state.update(
                error="Index refresh was not confirmed. Source materials were not modified. Inspect indexing and cloud access.",
                failed=time.time(),
            )
        finally:
            with self.lock:
                self.running = False


def main():
    from .client import call

    reply = json.loads(call({"action": "index_refresh"}))
    if reply.get("error") and not reply.get("running"):
        raise SystemExit("Workspace index could not start.")
    started = reply.get("started", time.time())
    for _ in range(120):
        if not reply.get("running"):
            if reply.get("error") or reply.get("last_success", 0) < started:
                raise SystemExit(
                    "Workspace index refresh was not confirmed; previous index retained."
                )
            return
        time.sleep(5)
        reply = json.loads(call({"action": "index_status"}))
    raise SystemExit("Workspace index refresh deadline exceeded; inspect index_status.")


if __name__ == "__main__":
    main()
