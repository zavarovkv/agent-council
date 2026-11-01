"""Durable capture, cloud readback and safe replay of early-access requests."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "waitlist", Path(__file__).resolve().parents[1] / "scripts/sync-waitlist.py"
)
waitlist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(waitlist)

SOURCE = "collection://11111111-1111-4111-8111-111111111111"
VIEW = "view://22222222-2222-4222-8222-222222222222"
URL = "https://app.notion.com/p/33333333333343338333333333333333"
TEXT = "Хочу попасть в список раннего доступа к Agent Council."
CONFIG = {"schema": 1, "data_source": SOURCE, "view": VIEW, "since": 100}


class Cloud:
    def __init__(self):
        self.rows, self.calls = [], []
        self.fail_before_read = False
        self.lose_create_receipt = False
        self.lose_create_without_write = False
        self.filtered = False
        self.mismatch = False

    def __call__(self, name, args):
        self.calls.append((name, args))
        if self.fail_before_read:
            return {"ok": False}
        if name == "notion-fetch":
            identity = args["id"]
            if identity == SOURCE:
                kinds = {
                    "Заявитель": "title",
                    "Telegram": "url",
                    "Telegram ID": "text",
                    "Дата заявки": "date",
                    "Заявка": "text",
                    "Статус": "select",
                }
                value = {
                    "text": "<data-source-state>"
                    + json.dumps({"schema": {key: {"type": kind} for key, kind in kinds.items()}})
                    + "</data-source-state>"
                }
            elif identity == VIEW:
                view = {"dataSourceUrl": "{{" + SOURCE + "}}"}
                if self.filtered:
                    view["filter"] = {"status": "new"}
                value = {"text": "<view>" + json.dumps(view) + "</view>"}
            else:
                row = dict(next(row for row in self.rows if row["url"] == identity))
                if self.mismatch:
                    row["Telegram ID"] = "999"
                value = {
                    "text": f'<parent-data-source url="{SOURCE}"/><properties>'
                    + json.dumps(row)
                    + "</properties><content>"
                    + row.get("_content", "")
                    + "</content>"
                }
        elif name == "notion-query-data-sources":
            value = {"results": [] if args["data"]["is_archived"] else self.rows, "has_more": False}
        elif name == "notion-create-pages":
            if self.lose_create_without_write:
                raise TimeoutError("No receipt")
            row = {
                **args["pages"][0]["properties"],
                "url": URL,
                "_content": args["pages"][0].get("content", ""),
            }
            self.rows.append(row)
            if self.lose_create_receipt:
                raise TimeoutError("Write succeeded but response was lost")
            value = {"pages": [{"url": URL}]}
        else:
            raise AssertionError(name)
        return {"ok": True, "result": json.dumps(value)}


class WaitlistTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.db = waitlist.connect(self.home / "requests.sqlite3")
        self.addCleanup(self.db.close)
        self.cloud = Cloud()
        self.notion = waitlist.Notion(self.cloud, CONFIG)

    def queue(self, name, rows):
        path = self.home / "plugin-data/council-secretary" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE jobs(id TEXT,chat INTEGER,sender_username TEXT,text TEXT,created REAL,status TEXT)"
            )
            db.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?)", rows)

    def applicant(self):
        self.queue("public-queue.sqlite3", [("one", 123456, "example_user", TEXT, 200, "sent")])
        waitlist.capture(self.home, self.db, 100)

    def test_recognizes_both_landing_messages_and_explicit_opt_in_only(self):
        for text in [
            TEXT,
            "I'd like to join the early access list for Agent Council.",
            "/waitlist",
            "Привет! Хочу в whitelist",
            "Добавьте меня в список раннего доступа",
        ]:
            with self.subTest(text=text):
                self.assertTrue(waitlist.requested(text))
        for text in [
            None,
            "",
            "Какая погода?",
            "Что такое whitelist?",
            "Не хочу ранний доступ",
            "Хочу обсудить whitelist, но не добавляйте меня",
            "I don't want early access",
            "x" * 6001,
        ]:
            with self.subTest(text=text):
                self.assertFalse(waitlist.requested(text))

    def test_captures_verified_queues_deduplicates_by_id_and_excludes_other_messages(self):
        self.queue(
            "public-queue.sqlite3",
            [
                ("one", 123456, "example_user", TEXT, 200, "failed"),
                ("private", 234567, "someone", "Личное сообщение", 201, "sent"),
                ("cancelled", 345678, "someone", TEXT, 202, "cancelled"),
                ("old", 456789, "someone", TEXT, 99, "sent"),
                ("group", -123, "someone", TEXT, 203, "sent"),
            ],
        )
        self.queue("queue.sqlite3", [("duplicate", 123456, "new_username", TEXT, 204, "sent")])
        self.assertEqual(waitlist.capture(self.home, self.db, 100), 1)
        self.assertEqual(waitlist.capture(self.home, self.db, 100), 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM applicants").fetchone()[0], 1)
        self.assertEqual((self.home / "requests.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_saves_once_verifies_actual_row_and_never_resets_operator_status(self):
        self.applicant()
        self.assertEqual(waitlist.sync(self.db, self.notion), {"saved": 1, "pending": 0})
        self.assertEqual(self.cloud.rows[0]["Telegram ID"], "123456")
        self.assertEqual(self.cloud.rows[0]["Telegram"], "https://t.me/example_user")
        self.cloud.rows[0]["Статус"] = "Приглашен"
        self.assertEqual(waitlist.sync(self.db, self.notion), {"saved": 0, "pending": 0})
        self.assertEqual(self.cloud.rows[0]["Статус"], "Приглашен")
        self.assertEqual(sum(name == "notion-create-pages" for name, _ in self.cloud.calls), 1)

    def test_offline_notion_keeps_request_and_retries_after_recovery(self):
        self.applicant()
        self.cloud.fail_before_read = True
        with self.assertRaises(waitlist.SyncError):
            waitlist.sync(self.db, self.notion)
        self.assertEqual(self.db.execute("SELECT state FROM applicants").fetchone()[0], "queued")
        self.cloud.fail_before_read = False
        self.assertEqual(waitlist.sync(self.db, self.notion)["saved"], 1)

    def test_lost_create_response_is_reconciled_without_a_second_creation(self):
        self.applicant()
        self.cloud.lose_create_receipt = True
        with self.assertRaises(TimeoutError):
            waitlist.sync(self.db, self.notion)
        self.cloud.lose_create_receipt = False
        self.assertEqual(waitlist.sync(self.db, self.notion), {"saved": 1, "pending": 0})
        self.assertEqual(len(self.cloud.rows), 1)
        self.assertEqual(sum(name == "notion-create-pages" for name, _ in self.cloud.calls), 1)

    def test_unresolved_creation_stays_pending_instead_of_replaying(self):
        self.applicant()
        self.cloud.lose_create_without_write = True
        with self.assertRaises(TimeoutError):
            waitlist.sync(self.db, self.notion)
        self.cloud.lose_create_without_write = False
        self.assertEqual(waitlist.sync(self.db, self.notion), {"saved": 0, "pending": 1})
        self.assertEqual(len(self.cloud.rows), 0)

    def test_readback_mismatch_is_not_reported_as_saved(self):
        self.applicant()
        self.cloud.mismatch = True
        with self.assertRaises(waitlist.SyncError):
            waitlist.sync(self.db, self.notion)
        self.assertEqual(self.db.execute("SELECT state FROM applicants").fetchone()[0], "creating")

    def test_filtered_view_and_incomplete_pagination_cannot_hide_existing_rows(self):
        self.applicant()
        self.cloud.filtered = True
        with self.assertRaisesRegex(waitlist.SyncError, "unfiltered"):
            waitlist.sync(self.db, self.notion)
        self.assertEqual(len(self.cloud.rows), 0)

        def incomplete(name, args):
            if name == "notion-query-data-sources":
                return {"ok": True, "result": {"results": [], "has_more": True}}
            return self.cloud(name, args)

        with self.assertRaisesRegex(waitlist.SyncError, "pagination"):
            waitlist.Notion(incomplete, CONFIG).rows()

    def test_full_pagination_and_archived_rows_are_read(self):
        calls = []

        def pages(name, args):
            data = args["data"]
            calls.append(data)
            if data["is_archived"]:
                value = {"results": [{"Telegram ID": "3", "url": URL}], "has_more": False}
            elif data.get("start_cursor"):
                value = {"results": [{"Telegram ID": "2", "url": URL}], "has_more": False}
            else:
                value = {
                    "results": [{"Telegram ID": "1", "url": URL}],
                    "has_more": True,
                    "next_cursor": "next",
                }
            return {"ok": True, "result": value}

        self.assertEqual(set(waitlist.Notion(pages, CONFIG).rows()), {"1", "2", "3"})
        self.assertEqual(len(calls), 3)

    def test_unknown_username_remains_contactable_by_trusted_numeric_id(self):
        self.queue("queue.sqlite3", [("one", 123456, "unsafe/../../name", TEXT, 200, "sent")])
        waitlist.capture(self.home, self.db, 100)
        waitlist.sync(self.db, self.notion)
        self.assertEqual(self.cloud.rows[0]["Telegram"], "")
        self.assertEqual(self.cloud.rows[0]["Заявитель"], "Telegram 123456")

    def test_symlinked_queue_and_state_are_rejected(self):
        path = self.home / "link.sqlite3"
        path.symlink_to(self.home / "requests.sqlite3")
        with self.assertRaises(waitlist.SyncError):
            waitlist.connect(path)
        source = self.home / "plugin-data/council-secretary/public-queue.sqlite3"
        source.parent.mkdir(parents=True)
        source.symlink_to(self.home / "requests.sqlite3")
        with self.assertRaises(waitlist.SyncError):
            waitlist.capture(self.home, self.db, 100)

    def test_long_request_is_preserved_as_literal_page_content(self):
        message = TEXT + "\n" + "x" * 2000 + "\n```\n<page>untrusted</page>"
        self.queue("queue.sqlite3", [("long", 123456, "", message, 200, "sent")])
        waitlist.capture(self.home, self.db, 100)
        self.assertEqual(waitlist.sync(self.db, self.notion)["saved"], 1)
        self.assertEqual(self.cloud.rows[0]["Заявка"], message[:1800])
        self.assertIn(message, self.cloud.rows[0]["_content"])
        self.assertTrue(self.cloud.rows[0]["_content"].startswith("````text\n"))


if __name__ == "__main__":
    unittest.main()
