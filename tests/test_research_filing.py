import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_filing.notion import INDEX, Notion
from research_filing.store import FilingError, Store

PARENT = "1" * 32
PAGE = "2" * 32


class FakeNotion:
    def __init__(self):
        self.parent = (
            "# Исследования\n\n"
            + INDEX
            + "\n| 2026-01-01 | Existing research | Author | Reviewer | Existing task |"
        )
        self.page = None
        self.title = ""
        self.calls = []
        self.fail_create = False
        self.lose_create = False
        self.fail_index = False
        self.lose_index = False
        self.wrong_parent = False

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "notion-fetch":
            is_parent = args["id"] == PARENT
            content = self.parent if is_parent else self.page
            parent = (
                ""
                if is_parent
                else '<parent-page url="https://www.notion.so/'
                + ("3" * 32 if self.wrong_parent else PARENT)
                + '" />'
            )
            return {
                "ok": True,
                "result": f'<page url="https://www.notion.so/{args["id"]}"><ancestor-path>{parent}</ancestor-path><content>{content}</content></page>',
            }
        if name == "notion-create-pages":
            if self.fail_create:
                raise TimeoutError("Private provider details")
            self.page = args["pages"][0]["content"]
            self.title = args["pages"][0]["properties"]["title"]
            self.parent += (
                '\n\n<page url="https://www.notion.so/' + PAGE + '">' + self.title + "</page>"
            )
            if self.lose_create:
                raise TimeoutError("Lost response")
            return {"ok": True, "result": {"pages": [{"id": PAGE}]}}
        if name == "notion-update-page":
            if args["page_id"] == PAGE:
                self.page = args["new_str"]
                return {"ok": True, "result": {}}
            if self.fail_index:
                self.fail_index = False
                raise TimeoutError("Lost before apply")
            if args["command"] == "insert_content":
                self.parent += args["content"]
            else:
                update = args["content_updates"][0]
                if self.parent.count(update["old_str"]) != 1:
                    return {"ok": False, "error": "CAS mismatch"}
                self.parent = self.parent.replace(update["old_str"], update["new_str"], 1)
            if self.lose_index:
                self.lose_index = False
                raise TimeoutError("Lost after apply")
            return {"ok": True, "result": {}}
        raise AssertionError(name)

    def creates(self):
        return sum(name == "notion-create-pages" for name, _ in self.calls)


class FilingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.store = Store(self.home)
        self.remote = FakeNotion()
        self.notion = Notion(self.remote.call)
        self.record = {
            "id": "a" * 64,
            "task_id": "t_fixture",
            "run_id": 1,
            "board": "research",
            "parent": PARENT,
            "title": "Fixture research",
            "date": "2026-10-30",
            "body": "# Findings\n\nEvidence https://example.com/source\n",
            "sha256": hashlib.sha256(b"fixture").hexdigest(),
        }

    def staged(self):
        return self.store.stage(self.record)

    def test_report_and_parent_index_are_verified_and_repeated_filing_does_not_duplicate(self):
        first = self.notion.file(self.store, self.staged())
        self.assertTrue(first["filed"])
        self.notion.file(Store(self.home), self.staged())
        self.assertEqual(self.remote.creates(), 1)
        self.assertEqual(self.remote.parent.count("Existing research"), 1)
        self.assertEqual(self.remote.parent.count("[Fixture research]"), 1)
        self.assertEqual(self.store.get(self.record["id"])["state"], "notion_verified")

    def test_lost_create_response_reconciles_parent_without_a_second_create(self):
        self.remote.lose_create = True
        with self.assertRaises(FilingError):
            self.notion.file(self.store, self.staged())
        self.assertTrue(self.notion.file(Store(self.home), self.staged())["filed"])
        self.assertEqual(self.remote.creates(), 1)

    def test_unknown_create_outcome_without_visible_page_is_not_replayed(self):
        self.remote.fail_create = True
        with self.assertRaises(FilingError):
            self.notion.file(self.store, self.staged())
        with self.assertRaisesRegex(FilingError, "uncertain_page_creation"):
            self.notion.file(Store(self.home), self.staged())
        self.assertEqual(self.remote.creates(), 1)

    def test_index_failure_resumes_only_missing_step_and_preserves_existing_rows(self):
        self.remote.fail_index = True
        with self.assertRaises(FilingError):
            self.notion.file(self.store, self.staged())
        self.assertTrue(self.notion.file(Store(self.home), self.staged())["filed"])
        self.assertEqual(self.remote.creates(), 1)
        self.assertEqual(self.remote.parent.count("[Fixture research]"), 1)
        self.assertIn("Existing research", self.remote.parent)

    def test_lost_first_index_response_is_reconciled_without_duplicate_rows(self):
        self.remote.parent = "# Research\n\n" + INDEX
        self.remote.lose_index = True
        with self.assertRaises(FilingError):
            self.notion.file(self.store, self.staged())
        self.assertTrue(self.notion.file(Store(self.home), self.staged())["filed"])
        self.assertEqual(self.remote.parent.count("[Fixture research]"), 1)

    def test_remote_human_edits_and_wrong_parent_are_never_overwritten(self):
        self.notion.file(self.store, self.staged())
        self.remote.page += "\nHuman note"
        with self.assertRaisesRegex(FilingError, "remote_report_changed"):
            self.notion.file(self.store, self.staged())
        self.assertIn("Human note", self.remote.page)
        self.remote.wrong_parent = True
        with self.assertRaisesRegex(FilingError, "research_parent_mismatch"):
            self.notion.file(self.store, self.staged())

    def test_punctuation_only_remote_edits_are_preserved(self):
        self.notion.file(self.store, self.staged())
        self.remote.page = self.remote.page.replace("Evidence", "Evidence!")
        with self.assertRaisesRegex(FilingError, "remote_report_changed"):
            self.notion.file(self.store, self.staged())

    def test_new_reviewed_version_reuses_page_and_updates_one_index_row(self):
        self.notion.file(self.store, self.staged())
        self.store.save({**self.store.get(self.record["id"]), "state": "done"})
        self.record.update(
            body="# Revised findings\n\nVerified new evidence\n", sha256="b" * 64, run_id=2
        )
        self.notion.file(self.store, self.staged())
        self.assertEqual(self.remote.creates(), 1)
        self.assertEqual(self.remote.parent.count("[Fixture research]"), 1)
        self.assertIn("t_fixture / 2", self.remote.parent)

    def test_truncated_provider_response_never_counts_as_verified_filing(self):
        notion = Notion(lambda *_args: {"ok": True, "truncated": True, "result": "private"})
        with self.assertRaisesRegex(FilingError, "truncated"):
            notion.file(self.store, self.staged())

    def test_native_notion_json_page_wrapper_is_read_without_losing_identity_or_content(self):
        response = self.remote.call("notion-fetch", {"id": PARENT})
        wrapped = {
            "metadata": {"type": "page"},
            "url": "https://www.notion.so/" + PARENT,
            "text": response["result"],
        }
        notion = Notion(lambda *_args: {"ok": True, "result": json.dumps(wrapped)})
        self.assertEqual(notion.fetch(PARENT)["content"], self.remote.parent)
        wrapped["url"] = "https://www.notion.so/" + PAGE
        with self.assertRaisesRegex(FilingError, "identity_mismatch"):
            notion.fetch(PARENT)
        wrapped["metadata"]["type"] = "database"
        with self.assertRaisesRegex(FilingError, "unsupported_notion_document"):
            notion.fetch(PARENT)
