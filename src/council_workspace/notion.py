"""Formatted Notion pages scoped to the selected CouncilBase subtree."""

import hashlib
import json
import re

from research_filing.notion import Notion, canonical, page_id


def revision(content):
    return hashlib.sha256(content.encode()).hexdigest()


class Pages:
    def __init__(self, call, root):
        self.notion, self.root = Notion(call), root

    def fetch(self, identifier):
        identifier = page_id(identifier)
        page = self.notion.fetch(identifier)
        current, seen = page, set()
        for _ in range(32):
            if current["id"] == self.root:
                return page
            if not current.get("parent") or current["id"] in seen:
                break
            seen.add(current["id"])
            current = self.notion.fetch(current["parent"])
        raise PermissionError("The page is outside the configured CouncilBase subtree.")

    def read(self, args):
        page = self.fetch(args.get("id") or self.root)
        offset = args.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= len(page["content"]):
            raise ValueError("Invalid page offset.")
        return {
            "id": page["id"],
            "url": "https://www.notion.so/" + page["id"],
            "revision": revision(page["content"]),
            "content": page["content"][offset : offset + 16000],
            "next_offset": offset + 16000 if offset + 16000 < len(page["content"]) else None,
        }

    def prepare_write(self, args):
        content = args.get("content")
        if (
            not isinstance(content, str)
            or not 1 <= len(content) <= 32000
            or re.search(r"<(?:page|database|data-source|content)\b", content, re.I)
        ):
            raise ValueError("Use bounded Notion Markdown content, without structural page tags.")
        if args["action"] == "notion_update":
            page = self.fetch(args.get("id"))
            if args.get("revision") != revision(page["content"]):
                raise ValueError(
                    "The page changed. Fetch and reconcile the current version before editing."
                )

            def update():
                self.notion.invoke(
                    "notion-update-page",
                    {
                        "page_id": page["id"],
                        "command": "replace_content",
                        "new_str": content,
                        "allow_async": False,
                    },
                )
                return self.verified(page["id"], content)

            return update
        parent = self.fetch(args.get("parent") or self.root)
        title = args.get("title")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise ValueError("A page title is required.")

        def create():
            result = self.notion.invoke(
                "notion-create-pages",
                {
                    "parent": {"page_id": parent["id"]},
                    "allow_async": False,
                    "pages": [{"properties": {"title": title.strip()}, "content": content}],
                },
            )
            if isinstance(result, str):
                result = json.loads(result)
            pages = result.get("pages") if isinstance(result, dict) else None
            if not isinstance(pages, list) or len(pages) != 1:
                raise ValueError(
                    "Notion creation receipt is unrecognized; inspect the parent page before retrying."
                )
            identity = page_id(pages[0].get("id") or pages[0].get("url"))
            return self.verified(identity, content)

        return create

    def verified(self, identity, expected):
        page = self.fetch(identity)
        if canonical(page["content"]) != canonical(expected):
            raise ValueError("Notion readback differs from the submitted text; inspect the page.")
        return {
            "id": identity,
            "url": "https://www.notion.so/" + identity,
            "revision": revision(page["content"]),
            "verified": True,
        }
