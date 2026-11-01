"""Fixed Notion MCP operations with readback, CAS table updates and uncertain creates."""

import html
import json
import re
import uuid

from .store import FilingError

INDEX = (
    "| Дата | Исследование | Автор | Проверил | Задача / запуск |\n| --- | --- | --- | --- | --- |"
)


def page_id(value):
    if not isinstance(value, str):
        raise FilingError("invalid_notion_page")
    matches = re.findall(
        r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}|[a-fA-F0-9]{32}",
        value,
    )
    if not matches:
        raise FilingError("invalid_notion_page")
    return uuid.UUID(matches[-1]).hex


def canonical(value):
    value = html.unescape(value).replace("\\", " ")
    value = re.sub(r"<(https?://[^>]+)>", r"\1", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.findall(r"\w+", value)


def document(value):
    if not isinstance(value, str) or len(value) > 65536:
        raise FilingError("unsupported_notion_response")
    declared_id = None
    try:
        wrapped = json.loads(value)
    except ValueError:
        wrapped = None
    if wrapped is not None:
        if (
            not isinstance(wrapped, dict)
            or not isinstance(wrapped.get("metadata"), dict)
            or wrapped["metadata"].get("type") != "page"
            or not isinstance(wrapped.get("text"), str)
        ):
            raise FilingError("unsupported_notion_document")
        declared_id = page_id(wrapped.get("url"))
        value = wrapped["text"]
    content = re.search(r"<content>\s?(.*?)\s?</content>", value, re.S)
    if content is None:
        raise FilingError("unsupported_notion_document")
    # Page prose (including code examples) is untrusted content, not ancestry.
    # Only the MCP ancestry envelope can establish scope. Properties, like the
    # body, can contain human-authored strings that resemble structural tags.
    envelope = re.split(r"<properties\b", value[: content.start()], maxsplit=1)[0]
    root = re.search(r'<page\b[^>]*\burl="([^"]+)"', envelope)
    ancestry = re.search(r"<ancestor-path>(.*?)</ancestor-path>", envelope, re.S)
    parent = re.search(r'<parent-page\b[^>]*\burl="([^"]+)"', ancestry[1] if ancestry else "")
    if root is None:
        raise FilingError("unsupported_notion_document")
    identifier = page_id(root[1])
    if declared_id is not None and declared_id != identifier:
        raise FilingError("notion_page_identity_mismatch")
    return {
        "id": identifier,
        "parent": page_id(parent[1]) if parent else None,
        "content": content[1],
    }


def cells(line):
    return [part.strip() for part in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def compatible_table(content):
    lines = content.splitlines(keepends=True)
    found = []
    for index in range(len(lines) - 1):
        if not lines[index].lstrip().startswith("|") or not lines[index + 1].lstrip().startswith(
            "|"
        ):
            continue
        header, separator = cells(lines[index]), cells(lines[index + 1])
        if len(header) != len(separator) or not all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in separator
        ):
            continue
        if not any(
            any(
                word in name.lower()
                for word in ("исслед", "тема", "назван", "ссыл", "отчёт", "отчет")
            )
            for name in header
        ):
            continue
        end = index + 2
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            if len(cells(lines[end])) != len(header):
                raise FilingError("invalid_research_index")
            end += 1
        found.append(
            (
                "".join(lines[index:end]).rstrip("\n"),
                header,
                [line.rstrip("\n") for line in lines[index + 2 : end]],
            )
        )
    if len(found) > 1:
        raise FilingError("ambiguous_research_index")
    return found[0] if found else None


def index_row(header, record, url):
    title = (
        html.escape(record["title"])
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    values = []
    for name in header:
        name = name.lower()
        if "дата" in name:
            value = record["date"]
        elif any(word in name for word in ("исслед", "тема", "назван")):
            value = f"[{title}]({url})"
        elif any(word in name for word in ("ссыл", "отчёт", "отчет", "результат")):
            value = f"[Открыть]({url})"
        elif any(word in name for word in ("провер", "реценз")):
            value = "Квай-Гон Джинн"
        elif any(word in name for word in ("автор", "исполн")):
            value = "Декстер Джеттстер"
        elif any(word in name for word in ("задач", "запуск", "task", "run")):
            value = f"{record['task_id']} / {record['run_id']}"
        elif "статус" in name:
            value = "Проверено"
        else:
            value = "—"
        values.append(value)
    return "| " + " | ".join(values) + " |"


class Notion:
    def __init__(self, call):
        self.call = call

    def invoke(self, name, arguments):
        try:
            response = self.call(name, arguments)
        except Exception:
            raise FilingError("notion_unavailable") from None
        if (
            not isinstance(response, dict)
            or response.get("ok") is not True
            or response.get("truncated")
        ):
            raise FilingError("notion_unavailable_or_truncated")
        value = response.get("result")
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict) and "async_task" in parsed:
                    raise FilingError("notion_write_pending")
            except ValueError:
                pass
        elif isinstance(value, dict) and (
            "async_task" in value or value.get("type") == "async_task"
        ):
            raise FilingError("notion_write_pending")
        return value

    def fetch(self, identifier):
        result = document(self.invoke("notion-fetch", {"id": identifier}))
        if result["id"] != identifier:
            raise FilingError("notion_page_identity_mismatch")
        return result

    def find_page(self, parent, marker):
        ids = set()
        for url, title in re.findall(
            r'<page\b[^>]*\burl="([^"]+)"[^>]*>([^<]*)</page>', parent["content"]
        ):
            if marker in html.unescape(title):
                ids.add(page_id(url))
        if len(ids) > 1:
            raise FilingError("duplicate_research_pages_require_review")
        return next(iter(ids), None)

    def file(self, store, record):
        marker = "Council " + record["id"][:16]
        parent = self.fetch(record["parent"])
        identifier = record.get("page_id") or self.find_page(parent, marker)
        if identifier is None:
            if record["state"] != "queued":
                raise FilingError("uncertain_page_creation_requires_reconciliation")
            record = store.save({**record, "state": "creating"})
            self.invoke(
                "notion-create-pages",
                {
                    "parent": {"page_id": record["parent"]},
                    "allow_async": False,
                    "pages": [
                        {
                            "properties": {
                                "title": record["date"] + " — " + record["title"] + " · " + marker
                            },
                            "content": record["body"],
                        }
                    ],
                },
            )
            parent = self.fetch(record["parent"])
            identifier = self.find_page(parent, marker)
            if identifier is None:
                raise FilingError("uncertain_page_creation_requires_reconciliation")
        record = store.save({**record, "page_id": identifier, "state": "writing"})
        page = self.fetch(identifier)
        if page["parent"] != record["parent"]:
            raise FilingError("research_parent_mismatch")
        if (
            record.get("remote_snapshot") is not None
            and page["content"] != record["remote_snapshot"]
        ):
            raise FilingError("remote_report_changed")
        if canonical(page["content"]) != canonical(record["body"]):
            if (
                record.get("previous_body") is None
                or canonical(page["content"]) != canonical(record["previous_body"])
                or page["content"] != record.get("previous_remote_snapshot")
            ):
                raise FilingError("remote_report_changed")
            self.invoke(
                "notion-update-page",
                {
                    "page_id": identifier,
                    "command": "replace_content",
                    "new_str": record["body"],
                    "allow_async": False,
                },
            )
            page = self.fetch(identifier)
            if page["parent"] != record["parent"] or canonical(page["content"]) != canonical(
                record["body"]
            ):
                raise FilingError("report_readback_failed")
        record = store.save({**record, "state": "indexing", "remote_snapshot": page["content"]})
        parent = self.fetch(record["parent"])
        table = compatible_table(parent["content"])
        if table is None:
            if record.get("index_bootstrap_attempted"):
                raise FilingError("uncertain_index_creation_requires_reconciliation")
            record = store.save({**record, "index_bootstrap_attempted": True})
            self.invoke(
                "notion-update-page",
                {
                    "page_id": record["parent"],
                    "command": "insert_content",
                    "content": "\n\n" + INDEX,
                    "allow_async": False,
                },
            )
            table = compatible_table(self.fetch(record["parent"])["content"])
            if table is None:
                raise FilingError("index_readback_failed")
        old, header, rows = table
        url = "https://www.notion.so/" + identifier
        row = index_row(header, record, url)
        matching = [value for value in rows if identifier in value.replace("-", "")]
        if len(matching) > 1:
            raise FilingError("duplicate_research_rows_require_review")
        if not matching or canonical(matching[0]) != canonical(row):
            if (
                matching
                and record.get("index_snapshot") is not None
                and matching[0] != record["index_snapshot"]
            ):
                raise FilingError("remote_index_row_changed")
            if not rows and record.get("index_attempted"):
                raise FilingError("uncertain_first_index_row_requires_reconciliation")
            # Prepend new rows before existing rows. The old complete table is
            # then no longer a substring, making repeated CAS updates safe.
            new = "\n".join(
                [*old.splitlines()[:2], row, *[value for value in rows if value not in matching]]
            )
            record = store.save({**record, "index_attempted": True})
            self.invoke(
                "notion-update-page",
                {
                    "page_id": record["parent"],
                    "command": "update_content",
                    "content_updates": [{"old_str": old, "new_str": new}],
                    "allow_async": False,
                },
            )
        verified = compatible_table(self.fetch(record["parent"])["content"])
        if (
            verified is None
            or sum(identifier in value.replace("-", "") for value in verified[2]) != 1
        ):
            raise FilingError("index_readback_failed")
        verified_row = next(value for value in verified[2] if identifier in value.replace("-", ""))
        if canonical(verified_row) != canonical(row):
            raise FilingError("index_readback_failed")
        record = store.save(
            {
                **record,
                "state": "notion_verified",
                "previous_body": None,
                "previous_remote_snapshot": None,
                "index_snapshot": verified_row,
            }
        )
        return {
            "filed": True,
            "task_id": record["task_id"],
            "run_id": record["run_id"],
            "url": url,
            "sha256": record["sha256"],
        }
