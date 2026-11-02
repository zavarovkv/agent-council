"""Document receipts tolerate known link serialization, not uncertain or changed content."""

import concurrent.futures
import copy
import threading
import unittest

import test_linear_issues as fixtures
from test_linear_agent import uid

from linear_agent.document_content import equivalent_content, matches_document
from linear_agent.linear import Linear
from linear_agent.service import Service
from linear_agent.transport import RemoteError

URL = "https://linear.app/council/document/brief-123"
LINK = f"[Brief]({URL})"
ANGLE_LINK = f"[Brief](<{URL}>)"
DOCUMENT_LINK = f'<document id="{uid(500)}" href="{URL}">Brief</document>'


class DocumentLookupTests(unittest.TestCase):
    def lookup(self, response):
        requests = []

        def request(url, **kwargs):
            requests.append(kwargs["body"])
            if isinstance(response, Exception):
                raise response
            return response

        linear = Linear(None, request=request)
        # These tests isolate document shape and transport classification. App
        # installation/cooldown behavior is exercised with a real store separately.
        linear.query = lambda agent, query, variables: linear.raw(
            "fixture-access", query, variables
        )
        return linear, requests

    def test_complete_filtered_lookup_returns_absence_or_same_document_shape(self):
        document = {"id": uid(500), "title": "Brief", "content": LINK, "issue": {"id": uid(20)}}
        for nodes, expected in (([], None), ([document], document)):
            with self.subTest(nodes=nodes):
                linear, requests = self.lookup(
                    {"data": {"documents": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}}
                )
                self.assertEqual(linear.document(None, uid(500)), expected)
                self.assertEqual(requests[0]["variables"], {"id": uid(500)})
                self.assertIn("$id:ID!", requests[0]["query"])
                self.assertIn("documents(filter:{id:{eq:$id}}", requests[0]["query"])
                self.assertIn("includeArchived:true", requests[0]["query"])

    def test_incomplete_or_malformed_lookup_cannot_become_absence(self):
        for connection in (
            None,
            {},
            {"nodes": [], "pageInfo": {}},
            {"nodes": [], "pageInfo": {"hasNextPage": True}},
            {"nodes": [], "pageInfo": {"hasNextPage": 0}},
            {"nodes": None, "pageInfo": {"hasNextPage": False}},
            {"nodes": [{"id": uid(501)}], "pageInfo": {"hasNextPage": False}},
            {"nodes": [None], "pageInfo": {"hasNextPage": False}},
            {"nodes": [{"id": uid(500)}] * 2, "pageInfo": {"hasNextPage": False}},
        ):
            with self.subTest(connection=connection):
                linear, _ = self.lookup({"data": {"documents": connection}})
                with self.assertRaises(RemoteError):
                    linear.document(None, uid(500))

    def test_graphql_and_transport_failures_never_become_absence(self):
        for response in (
            {"errors": [{"message": "not found"}], "data": {"documents": None}},
            RemoteError(404),
            RemoteError(429),
            RemoteError(503),
        ):
            with self.subTest(response=response):
                linear, _ = self.lookup(response)
                with self.assertRaises(RemoteError):
                    linear.document(None, uid(500))


class DocumentContentTests(unittest.TestCase):
    def test_known_link_forms_preserve_labels_destinations_and_surrounding_text(self):
        for original, serialized in (
            (LINK, ANGLE_LINK),
            (LINK, DOCUMENT_LINK),
            (ANGLE_LINK, DOCUMENT_LINK),
            (
                f"See {LINK}.\nBudget: 1250; due 2026-11-03.\n",
                f"See {DOCUMENT_LINK}.\nBudget: 1250; due 2026-11-03.\n",
            ),
            (f"{LINK}\n{LINK}", f"{DOCUMENT_LINK}\n{ANGLE_LINK}"),
            (
                "[Source](https://example.com/a?x=1&y=2#section)",
                "[Source](<https://example.com/a?x=1&y=2#section>)",
            ),
        ):
            with self.subTest(original=original, serialized=serialized):
                self.assertTrue(equivalent_content(original, serialized))
                self.assertTrue(equivalent_content(serialized, original))

    def test_meaningful_changes_never_produce_equivalence(self):
        expected = f"See {LINK}. Budget: 1250; due 2026-11-03.\n"
        for changed in (
            expected.replace("1250", ""),
            expected.replace("1250", "1200"),
            expected.replace("2026-11-03", "2026-11-04"),
            expected.replace("Brief", "Different"),
            expected.replace(URL, URL + "-other"),
            expected.replace("See ", ""),
            expected.rstrip(),
            expected.replace("Budget:", "Budget: "),
            expected + "Unreviewed addition",
            None,
            {"content": expected},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(equivalent_content(expected, changed))
        self.assertFalse(
            equivalent_content(DOCUMENT_LINK, DOCUMENT_LINK.replace(uid(500), uid(501)))
        )
        self.assertFalse(
            matches_document(
                {"title": "Edited", "content": LINK}, {"title": "Brief", "content": ANGLE_LINK}
            )
        )

    def test_code_and_ambiguous_markup_are_exact(self):
        for template in (
            "`{}`",
            "```markdown\n{}\n```",
            "~~~markdown\n{}\n~~~",
            "    {}",
            "\t{}",
            "   \t{}",
            ">     {}",
            "> ~~~markdown\n> {}\n> ~~~",
            "<code>{}</code>",
            "<pre>\n{}\n</pre>",
            '<span data-example="{}">example</span>',
            "\\{}",
            "!{}",
            "[outer {}]",
        ):
            with self.subTest(template=template):
                original = template.format(LINK)
                self.assertTrue(equivalent_content(original, original))
                self.assertFalse(equivalent_content(original, template.format(ANGLE_LINK)))

    def test_no_general_url_or_html_canonicalization(self):
        for changed in (
            DOCUMENT_LINK.replace('id="', "id='", 1),
            DOCUMENT_LINK.replace("Brief", "Br&#105;ef"),
            DOCUMENT_LINK.replace("https://", "http://"),
            DOCUMENT_LINK.replace("linear.app", "LINEAR.APP"),
            DOCUMENT_LINK.replace("href=", "data-href="),
            DOCUMENT_LINK.replace(">Brief", ' extra="value">Brief'),
            DOCUMENT_LINK.replace("/brief-123", "/brief%2D123"),
        ):
            with self.subTest(changed=changed):
                self.assertFalse(equivalent_content(LINK, changed))
        for label in ("*Brief*", "_Brief_", "~~Brief~~", "Brief &amp; plan", "[Brief]"):
            with self.subTest(label=label):
                self.assertFalse(
                    equivalent_content(f"[{label}]({URL})", DOCUMENT_LINK.replace("Brief", label))
                )


class DocumentWriteTests(unittest.TestCase):
    event = fixtures.IssueManagementTests.event
    ingest = fixtures.IssueManagementTests.ingest
    tick = fixtures.IssueManagementTests.tick
    run_for = fixtures.IssueManagementTests.run_for
    start = fixtures.IssueManagementTests.start
    call = fixtures.IssueManagementTests.call

    def setUp(self):
        fixtures.IssueManagementTests.setUp(self)
        self.context = self.start()
        self.documents, self.writes = {}, []
        self.linear.document = self.read_document
        self.linear.save_document = self.write_document
        self.fields = {"title": "Deliverable", "content": f"See {LINK}. Budget: 1250."}

    def read_document(self, agent, identity):
        return copy.deepcopy(self.documents.get(identity))

    def write_document(self, agent, identity, fields, *, create=False):
        self.writes.append((identity, copy.deepcopy(fields), create))
        self.documents.setdefault(
            identity,
            {
                "id": identity,
                "url": "https://linear.app/document/fixture",
                "issue": {"id": fields.get("issueId", uid(20))},
            },
        ).update({key: value for key, value in fields.items() if key in {"title", "content"}})

    def save(self, key="document", **kwargs):
        return self.call(self.context, "save_document", key=key, fields=self.fields, **kwargs)

    def test_failed_lookup_is_not_absence_and_retry_can_create_once(self):
        for status in (0, 404, 429, 500):
            with self.subTest(status=status):

                def fail(*args, status=status):
                    raise RemoteError(status)

                self.linear.document = fail
                with self.assertRaises(RemoteError) as raised:
                    self.save(key=f"failed-{status}")
                self.assertEqual(raised.exception.status, status)
                self.assertEqual(len(self.writes), 0)
        self.linear.document = self.read_document
        for status in (0, 404, 429, 500):
            result = self.save(key=f"failed-{status}")
            self.assertEqual(result, self.save(key=f"failed-{status}"))
        self.assertEqual(len(self.writes), 4)

    def test_forbidden_or_malformed_lookup_does_not_authorize_creation(self):
        def forbidden(*args):
            raise PermissionError("Denied")

        self.linear.document = forbidden
        with self.assertRaises(PermissionError):
            self.save()
        self.linear.document = lambda *args: {}
        with self.assertRaises(RemoteError):
            self.save()
        self.assertEqual(self.writes, [])

    def test_confirmed_missing_update_does_not_create_replacement(self):
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            self.save(document=uid(500))
        self.assertEqual(self.writes, [])

    def test_normalized_readback_verifies_create_and_changed_key_payload_is_rejected(self):
        def normalized(*args, **kwargs):
            self.write_document(*args, **kwargs)
            self.documents[args[1]]["content"] = self.fields["content"].replace(LINK, DOCUMENT_LINK)

        self.linear.save_document = normalized
        result = self.save()
        self.assertEqual(result, self.save())
        self.assertEqual(len(self.writes), 1)
        self.fields["content"] += " Different request."
        with self.assertRaisesRegex(ValueError, "different issue operation"):
            self.save()
        self.assertEqual(len(self.writes), 1)

    def test_lost_create_response_recovers_normalized_content_after_adapter_restart(self):
        def lost(*args, **kwargs):
            self.write_document(*args, **kwargs)
            self.documents[args[1]]["content"] = self.fields["content"].replace(LINK, ANGLE_LINK)
            raise RemoteError()

        self.linear.save_document = lost
        with self.assertRaises(RemoteError):
            self.save()
        self.service = Service(self.home, store=self.store, linear=self.linear, hermes=self.hermes)
        self.linear.save_document = self.write_document
        result = self.save()
        self.assertEqual(result["id"], self.writes[0][0])
        self.assertEqual(len(self.writes), 1)

    def test_uncertain_create_never_resurrects_deleted_document(self):
        def lost(*args, **kwargs):
            self.write_document(*args, **kwargs)
            raise RemoteError()

        self.linear.save_document = lost
        with self.assertRaises(RemoteError):
            self.save()
        self.documents.clear()
        self.linear.save_document = self.write_document
        with self.assertRaises(RemoteError):
            self.save()
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.documents, {})

    def test_failed_readback_retries_read_only_and_does_not_claim_success_early(self):
        def unavailable_after_write(agent, identity):
            if self.writes:
                raise RemoteError(503)
            return None

        self.linear.document = unavailable_after_write
        with self.assertRaises(RemoteError):
            self.save()
        with self.assertRaises(RemoteError):
            self.save()
        self.assertEqual(len(self.writes), 1)
        self.linear.document = self.read_document
        self.assertEqual(self.save()["id"], self.writes[0][0])
        self.assertEqual(len(self.writes), 1)

    def test_changed_readback_and_later_human_edit_never_receive_success_or_overwrite(self):
        def corrupted(*args, **kwargs):
            self.write_document(*args, **kwargs)
            self.documents[args[1]]["content"] = self.fields["content"].replace("1250", "")

        self.linear.save_document = corrupted
        with self.assertRaises(RemoteError):
            self.save()
        identity = self.writes[0][0]
        self.documents[identity]["content"] = "Later human edit"
        self.linear.save_document = self.write_document
        with self.assertRaises(RemoteError):
            self.save()
        self.assertEqual(self.documents[identity]["content"], "Later human edit")
        self.assertEqual(len(self.writes), 1)

    def test_uncertain_update_recovers_normalization_without_repeating_mutation(self):
        identity = self.save()["id"]
        self.fields["content"] += " Revised."

        def lost(*args, **kwargs):
            self.write_document(*args, **kwargs)
            self.documents[identity]["content"] = self.fields["content"].replace(
                LINK, DOCUMENT_LINK
            )
            raise RemoteError()

        self.linear.save_document = lost
        with self.assertRaises(RemoteError):
            self.save(key="update", document=identity)
        self.linear.save_document = self.write_document
        self.assertEqual(self.save(key="update", document=identity)["id"], identity)
        self.assertEqual(len(self.writes), 2)

    def test_concurrent_create_is_claimed_once_before_mutation(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release document writer")
            self.write_document(*args, **kwargs)

        self.linear.save_document = blocked
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.save)
            try:
                self.assertTrue(entered.wait(5))
                second = pool.submit(self.save)
                with self.assertRaises(RemoteError):
                    second.result(timeout=5)
            finally:
                release.set()
            result = first.result(timeout=5)
        self.assertEqual(result, self.save())
        self.assertEqual(len(self.writes), 1)


if __name__ == "__main__":
    unittest.main()
