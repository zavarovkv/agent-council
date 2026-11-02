import base64
import json
import sys
import tempfile
import unittest
from email import message_from_bytes
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from council_workspace.google import Google
from council_workspace.google_analytics import Analytics
from council_workspace.google_auth import private_write
from council_workspace.google_calendar import Calendar, event_body
from council_workspace.google_mail import Mail, compose

EVENT = {
    "summary": "Review",
    "start": "2026-11-03T10:00:00+03:00",
    "end": "2026-11-03T10:30:00+03:00",
    "attendees": ["person@example.com"],
}


class GoogleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.policy_path = self.home / ".council/workspace/access.json"
        self.policy_path.parent.mkdir(parents=True)
        self.policy = {
            "schema": 1,
            "enabled": True,
            "notion_root": "1" * 32,
            "drive_root": "folder-fixture",
            "clients": {
                "native-qui-gon": {"role": "qui-gon", "key": "1" * 64},
                "linear-lando": {"role": "lando", "key": "2" * 64},
            },
        }
        private_write(self.policy_path, self.policy)
        self.api = MagicMock()
        self.factory = MagicMock(return_value=self.api)
        self.google = Google(self.home, self.factory)

    def test_personal_services_are_not_shared_and_revocation_takes_effect(self):
        for service in ("gmail", "calendar"):
            with self.subTest(service=service), self.assertRaises(PermissionError):
                self.google.execute("linear-lando", {"service": service, "action": "status"})
        self.api.accountSummaries().list().execute.return_value = {"accountSummaries": []}
        self.assertEqual(
            self.google.execute("linear-lando", {"service": "analytics", "action": "properties"}),
            {"accountSummaries": []},
        )
        self.factory.reset_mock()
        self.policy["clients"].pop("linear-lando")
        private_write(self.policy_path, self.policy)
        with self.assertRaises(PermissionError):
            self.google.execute("linear-lando", {"service": "analytics", "action": "properties"})
        self.factory.assert_not_called()

    def test_invitation_is_explicit_and_retry_never_sends_twice(self):
        saved = {
            **event_body({"body": EVENT}),
            "id": "event",
            "htmlLink": "https://calendar.google.com/event",
            "etag": "revision",
        }
        self.api.events().get().execute.return_value = saved
        args = {"service": "calendar", "action": "create", "body": EVENT, "key": "meeting-1"}
        first = self.google.execute("native-qui-gon", args)
        self.assertTrue(first["verified"])
        self.assertEqual(self.api.events().insert.call_args.kwargs["sendUpdates"], "all")
        identity = self.api.events().insert.call_args.kwargs["body"]["id"]
        self.assertRegex(identity, r"^[0-9a-v]{5,1024}$")
        self.assertEqual(first, self.google.execute("native-qui-gon", args))
        self.assertEqual(self.api.events().insert().execute.call_count, 1)
        with self.assertRaises(ValueError):
            self.google.execute("native-qui-gon", {**args, "body": {**EVENT, "summary": "Changed"}})

    def test_uncertain_send_is_not_replayed_and_invalid_request_does_not_reserve_key(self):
        args = {
            "service": "gmail",
            "action": "send",
            "body": {"to": ["bad"], "subject": "Hi", "text": "Test"},
            "key": "email-1",
        }
        with self.assertRaises(ValueError):
            self.google.execute("native-qui-gon", args)
        args["body"]["to"] = ["person@example.com"]
        self.api.users().messages().send().execute.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            self.google.execute("native-qui-gon", args)
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.google.execute("native-qui-gon", args)
        self.assertEqual(self.api.users().messages().send().execute.call_count, 1)

    def test_calendar_rejects_ambiguous_times_and_checks_provider_etag(self):
        for change in (
            {"start": "2026-11-03T10:00:00"},
            {"end": EVENT["start"]},
            {"attendees": ["a@example.com\r\nBcc: other@example.com"]},
            {"timezone": "not/a/zone"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                event_body({"body": {**EVENT, **change}})
        self.api.events().get().execute.return_value = {"etag": "current"}
        args = {"action": "cancel", "id": "event", "revision": "stale"}
        with self.assertRaises(ValueError):
            Calendar(self.api).prepare_write(args)
        self.api.events().delete.assert_not_called()
        Calendar(self.api).prepare_write({**args, "revision": "current"})()
        self.api.events().delete.assert_called_once_with(
            calendarId="primary", eventId="event", sendUpdates="all"
        )
        self.api.events().delete().headers.__setitem__.assert_called_with("If-Match", "current")

    def test_ga4_reports_preserve_caveats_and_page_without_modification(self):
        payload = {
            "rowCount": 3,
            "rows": [{"dimensionValues": [{"value": "RU"}]}],
            "metadata": {"subjectToThresholding": True},
        }
        self.api.properties().runReport().execute.return_value = payload
        args = {
            "action": "report",
            "property": "properties/123",
            "body": {
                "dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}],
                "metrics": [{"name": "activeUsers"}],
                "limit": 1,
            },
        }
        result = Analytics(lambda _: self.api).read(args)
        self.assertEqual(result["next_offset"], 1)
        self.assertTrue(result["metadata"]["subjectToThresholding"])
        for change in ({"limit": 100000}, {"offset": -1}, {"dateRanges": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Analytics(lambda _: self.api).read({**args, "body": {**args["body"], **change}})
        with self.assertRaises(ValueError):
            self.google.execute("linear-lando", {"service": "analytics", "action": "create"})

    def test_ga4_metadata_is_searchable_and_paginated_without_omission(self):
        self.api.properties().getMetadata().execute.return_value = {
            "dimensions": [{"apiName": f"page{i}"} for i in range(26)],
            "metrics": [{"apiName": "activeUsers"}],
        }
        service = Analytics(lambda _: self.api)
        args = {"action": "metadata", "property": "properties/123"}
        first = service.read(args)
        second = service.read({**args, "cursor": first["next_cursor"]})
        self.assertEqual(len(first["dimensions"]) + len(second["dimensions"]), 26)
        self.assertEqual(second["metrics"], [{"apiName": "activeUsers"}])
        self.assertIsNone(second["next_cursor"])
        selected = service.read({**args, "query": "active"})
        self.assertFalse(selected["dimensions"])
        self.assertEqual(selected["metrics"], second["metrics"])

    def test_mail_read_keeps_unread_and_bounds_nested_mime(self):
        text = base64.urlsafe_b64encode(b"x" * 25000).decode()
        item = {
            "id": "1",
            "labelIds": ["UNREAD"],
            "payload": {
                "parts": [
                    {"parts": [{"mimeType": "text/plain", "body": {"data": text}}]},
                    {"mimeType": "text/html", "body": {"data": text}},
                ]
            },
        }
        self.api.users().messages().get().execute.return_value = item
        result = Mail(self.api).read({"action": "get", "id": "1"})
        self.assertEqual(result["labels"], ["UNREAD"])
        self.assertLessEqual(sum(len(p["text"]) for p in result["body"]), 32000)
        self.assertTrue(any(p["truncated"] for p in result["body"]))
        self.api.users().messages().modify.assert_not_called()

    def test_reply_has_explicit_recipients_and_thread_headers(self):
        args = {
            "key": "reply-1",
            "body": {"to": ["person@example.com"], "subject": "Re: Plan", "text": "Agreed"},
        }
        original = {
            "id": "1",
            "threadId": "thread",
            "payload": {"headers": [{"name": "Message-ID", "value": "<original@example.com>"}]},
        }
        result = compose(args, original)
        message = message_from_bytes(base64.urlsafe_b64decode(result["raw"]))
        self.assertEqual(result["threadId"], "thread")
        self.assertEqual(message["In-Reply-To"], "<original@example.com>")
        self.assertEqual(message["To"], "person@example.com")
        with self.assertRaises(ValueError):
            compose({**args, "body": {**args["body"], "subject": "Hi\nBcc: other@example.com"}})

    def test_saved_draft_sends_only_reviewed_snapshot_and_checks_sent_receipt(self):
        self.api.users().drafts().get().execute.return_value = {
            "id": "draft",
            "message": {"id": "v1", "raw": "cmV2aWV3ZWQ=", "threadId": "thread"},
        }
        self.api.users().drafts().send().execute.return_value = {"id": "sent"}
        self.api.users().messages().get().execute.return_value = {
            "id": "sent",
            "labelIds": ["SENT"],
        }
        mail = Mail(self.api)
        with self.assertRaises(ValueError):
            mail.prepare_write({"action": "send_draft", "id": "draft", "revision": "old"})
        result = mail.prepare_write({"action": "send_draft", "id": "draft", "revision": "v1"})()
        self.assertEqual(result["status"], "sent")
        self.assertEqual(
            self.api.users().drafts().send.call_args.kwargs["body"]["message"]["raw"],
            "cmV2aWV3ZWQ=",
        )

    def test_credentials_remain_private_and_symlinks_are_refused(self):
        path = self.home / "token.json"
        private_write(path, {"fixture": True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        link = self.home / "link.json"
        link.symlink_to(path)
        with self.assertRaises(PermissionError):
            private_write(link, {})
        self.assertEqual(json.loads(path.read_text()), {"fixture": True})


if __name__ == "__main__":
    unittest.main()
