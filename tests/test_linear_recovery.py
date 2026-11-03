"""Outages must delay work, never lose delegation or duplicate paid execution."""

import concurrent.futures
import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

import test_linear_agent as fixtures
from test_linear_agent import uid

from linear_agent.hermes import Hermes
from linear_agent.linear import Linear
from linear_agent.service import Service
from linear_agent.store import Store
from linear_agent.transport import JsonResponse, RemoteError, request_json, retry_deadline


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.LinearAgentTests.setUp
    event = fixtures.LinearAgentTests.event
    ingest = fixtures.LinearAgentTests.ingest
    tick = fixtures.LinearAgentTests.tick
    run_for = fixtures.LinearAgentTests.run_for

    def restart(self):
        self.store = Store(self.home, clock=lambda: self.now)
        self.service = Service(
            self.home,
            store=self.store,
            linear=self.linear,
            hermes=self.hermes,
            clock=lambda: self.now,
        )

    def family(self):
        self.ingest(self.event())
        self.tick()
        parent = self.store.session("qui-gon", uid(10))
        self.service.delegate(
            "qui-gon",
            {
                "run": self.run_for(),
                "session": parent["hermes"],
                "target": "dexter",
                "key": "research",
                "title": "Research",
                "description": "Verify sources.",
            },
        )
        self.hermes.finish(self.run_for(), "Delegated")
        self.tick()
        parent_job = self.store.jobs()[0]
        self.ingest(self.event(slug="dexter", sid=11, actor=0), slug="dexter")
        self.tick()
        return parent_job

    def test_age40_long_outage_restart_then_one_child_handoff(self):
        parent = self.family()
        original = self.linear.session
        self.hermes.finish(self.run_for("dexter"), "Saved research")
        with patch.object(self.linear, "session", side_effect=RemoteError(503)) as remote:
            self.now += 1900
            self.service.step()
            self.assertEqual(self.store.job(parent["key"])["state"], "waiting_children")
            self.assertEqual(self.store.job(parent["key"])["updated"], parent["updated"])
            count = remote.call_count
            self.restart()
            for _ in range(50):
                self.service.step()
            self.assertEqual(remote.call_count, count)
            self.now += 3 * 3600
            self.service.step()
        self.assertFalse(self.hermes.stops)
        self.assertEqual(len(self.hermes.requests), 2)
        self.linear.session = original
        self.now += 400
        self.tick(4)
        turns = [r for r in self.hermes.requests.values() if r["agent"] == "qui-gon"]
        self.assertEqual(len(turns), 2)
        self.assertIn("Saved research", turns[-1]["text"])
        self.hermes.finish(turns[-1]["run_id"], "Reviewed result")
        self.tick(3)
        self.restart()
        self.tick(3)
        self.assertEqual(len(self.hermes.requests), 3)
        self.assertFalse(self.store.jobs())

    def test_background_polling_is_bounded_but_tools_check_current_authorization(self):
        self.ingest(self.event())
        self.tick()
        with patch.object(self.linear, "session", wraps=self.linear.session) as remote:
            for _ in range(29):
                self.now += 1
                self.service.step()
            self.assertEqual(remote.call_count, 0)
            self.now += 1
            self.service.step()
            self.assertEqual(remote.call_count, 1)
            self.linear.sessions[uid(10)]["issue"]["team"]["id"] = uid(999)
            with self.assertRaises(PermissionError):
                self.service.child_tasks(
                    "qui-gon",
                    {
                        "run": self.run_for(),
                        "session": self.store.session("qui-gon", uid(10))["hermes"],
                    },
                )

    def test_owner_stop_and_policy_revocation_bypass_normal_poll_delay(self):
        self.ingest(self.event())
        self.tick()
        self.ingest(self.event(action="prompted", stop=True, text="Stop"))
        self.service.step()
        self.assertTrue(self.hermes.stops)
        self.assertFalse(self.store.jobs())

    def test_disabled_policy_cancels_even_during_recovery(self):
        self.ingest(self.event())
        self.tick()
        job = self.store.jobs()[0]
        self.service.recovery.failure(
            "job:" + job["key"], RemoteError(429, retry_at=self.now + 3600)
        )
        self.config["enabled"] = False
        (self.home / "agents.json").write_text(json.dumps(self.config))
        self.service.step()
        self.assertTrue(self.hermes.stops)
        self.assertFalse(self.store.jobs())

    def test_lost_publication_receipt_survives_outage_without_duplicate_or_new_run(self):
        self.ingest(self.event())
        self.tick()
        self.hermes.finish(self.run_for())
        self.linear.uncertain_post = True
        self.tick()
        job = self.store.jobs()[0]
        self.assertEqual(job["state"], "publishing")
        with patch.object(self.linear, "session", side_effect=RemoteError(503)):
            self.now += 3600
            self.service.step()
        self.restart()
        self.now += 400
        self.tick(3)
        self.assertFalse(self.hermes.stops)
        self.assertEqual(len(self.hermes.requests), 1)
        self.assertEqual(sum(r["kind"] == "response" for r in self.linear.posts.values()), 1)
        self.assertFalse(self.store.jobs())

    def test_child_handoff_outage_is_durable_and_does_not_abort_other_work(self):
        self.family()
        child = next(j for j in self.store.jobs() if j["agent"] == "dexter")
        self.store.update_job(child["key"], state="done", output="Verified result")
        self.store.child_finished("dexter", uid(11), child["key"])
        with patch.object(self.linear, "session", side_effect=RemoteError(429)) as remote:
            self.service._notify_children(self.service.registry())
            count = remote.call_count
            self.restart()
            self.service._notify_children(self.service.registry())
            self.assertEqual(remote.call_count, count)
        self.now += 400
        self.service._notify_children(self.service.registry())
        self.service._notify_children(self.service.registry())
        self.assertEqual(sum(j["action"] == "children" for j in self.store.jobs()), 1)

    def client(self, request):
        return Linear(self.store, request=request, clock=lambda: self.now)

    def test_missing_executor_toolset_defers_job_without_revoking_task(self):
        capabilities = {
            "features": {
                "run_submission": True,
                "runs_idempotency": {"supported": True, "durable": True},
            }
        }
        inventory = {
            "data": [{"name": "web", "enabled": True}, {"name": "clarify", "enabled": True}]
        }
        agent = self.service.registry().agents["qui-gon"]
        with self.assertRaises(RemoteError):
            Hermes(request=Mock(side_effect=[capabilities, inventory])).ready(agent)
        excess = {"data": inventory["data"] + [{"name": "terminal", "enabled": True}]}
        with self.assertRaises(ValueError):
            Hermes(request=Mock(side_effect=[capabilities, excess])).ready(agent)
        self.ingest(self.event())
        with patch.object(self.hermes, "ready", side_effect=RemoteError(503)):
            self.tick()
        self.assertEqual(self.store.jobs()[0]["state"], "queued")
        self.assertFalse(self.store.session("qui-gon", uid(10))["blocked"])
        self.assertFalse(self.hermes.requests)

    def test_missing_issue_and_comment_do_not_block_idempotent_creation(self):
        agent = self.service.registry().agents["qui-gon"]
        for kind, read in (("Issue", "issue"), ("Issue", "issue_details"), ("Comment", "comment")):
            with self.subTest(kind=kind, read=read):
                request = Mock(
                    return_value=JsonResponse(
                        {
                            "data": None,
                            "errors": [
                                {
                                    "message": "Entity not found: " + kind,
                                    "extensions": {"code": "INPUT_ERROR"},
                                }
                            ],
                        },
                        {},
                    )
                )
                client = self.client(request)
                self.assertIsNone(getattr(client, read)(agent, uid(123)))
                self.assertEqual(request.call_count, 1)
                installation = self.store.installation(agent.slug)
                key = "api:" + installation["organization"] + ":" + installation["app_user"]
                self.assertEqual(client.recovery.get(key)["failures"], 0)

    def test_real_child_adapter_creates_only_after_confirmed_absence(self):
        child = {"issue": uid(333), "title": "Research", "description": "Verify"}
        parent, team, delegate = uid(1), uid(2), uid(3)
        missing = JsonResponse(
            {
                "data": None,
                "errors": [
                    {"message": "Entity not found: Issue", "extensions": {"code": "INPUT_ERROR"}}
                ],
            },
            {},
        )
        created = {
            "id": child["issue"],
            "title": child["title"],
            "url": "https://linear.app/fixture",
        }
        existing = {
            **created,
            "parent": {"id": parent},
            "team": {"id": team},
            "delegate": {"id": delegate},
        }
        request = Mock(
            side_effect=[
                missing,
                JsonResponse({"data": {"issueCreate": {"success": True, "issue": created}}}, {}),
                JsonResponse({"data": {"issue": existing}}, {}),
            ]
        )
        client = self.client(request)
        agent = self.service.registry().agents["qui-gon"]
        self.assertEqual(
            client.ensure_child(agent, child, parent_issue=parent, team=team, delegate=delegate),
            created,
        )
        self.assertEqual(
            client.ensure_child(agent, child, parent_issue=parent, team=team, delegate=delegate),
            existing,
        )
        self.assertEqual(request.call_count, 3)
        request = Mock(side_effect=RemoteError(503))
        with self.assertRaises(RemoteError):
            self.client(request).ensure_child(
                agent, child, parent_issue=parent, team=team, delegate=delegate
            )
        self.assertEqual(request.call_count, 1)

    def test_reserved_child_recovers_from_interrupted_creation_without_duplicates(self):
        self.ingest(self.event())
        self.tick()
        parent = self.store.session("qui-gon", uid(10))
        payload = {
            "run": self.run_for(),
            "session": parent["hermes"],
            "target": "dexter",
            "key": "recover-child",
            "title": "Research",
            "description": "Verify sources.",
        }
        with patch.object(self.linear, "ensure_child", side_effect=RemoteError(503)):
            with self.assertRaises(RemoteError):
                self.service.delegate("qui-gon", payload)
        child = self.store.children("qui-gon", parent["id"])[0]
        self.hermes.finish(self.run_for(), "Waiting for research")
        with patch.object(self.linear, "ensure_child", wraps=self.linear.ensure_child) as ensure:
            self.tick()
            self.assertIn(child["issue"], self.linear.issue_records)
            self.restart()
            self.tick(3)
            self.assertEqual(
                len([key for key in self.linear.issue_records if key == child["issue"]]), 1
            )
            self.assertGreaterEqual(ensure.call_count, 1)

    def test_reserved_child_is_not_created_after_owner_stop(self):
        self.ingest(self.event())
        self.tick()
        parent = self.store.session("qui-gon", uid(10))
        payload = {
            "run": self.run_for(),
            "session": parent["hermes"],
            "target": "dexter",
            "key": "stopped-child",
            "title": "Research",
            "description": "Verify sources.",
        }
        with patch.object(self.linear, "ensure_child", side_effect=RemoteError(503)):
            with self.assertRaises(RemoteError):
                self.service.delegate("qui-gon", payload)
        child = self.store.children("qui-gon", parent["id"])[0]
        self.store.cancel("qui-gon", parent["id"])
        self.service._recover_reserved_children(self.service.registry())
        self.assertNotIn(child["issue"], self.linear.issue_records)

    def test_real_activity_transport_unblocks_queued_job_and_delivers_once(self):
        activities = {}

        def request(_url, **kwargs):
            body = kwargs["body"]
            if "CouncilActivityCreate" in body["query"]:
                item = body["variables"]["input"]
                self.assertNotIn(item["id"], activities)
                activities[item["id"]] = item
                return JsonResponse(
                    {
                        "data": {
                            "agentActivityCreate": {
                                "success": True,
                                "agentActivity": {"id": item["id"]},
                            }
                        }
                    },
                    {},
                )
            item = activities.get(body["variables"]["id"])
            if item is None:
                return JsonResponse(
                    {
                        "data": None,
                        "errors": [
                            {
                                "message": "Entity not found: AgentActivity",
                                "extensions": {"code": "INPUT_ERROR"},
                            }
                        ],
                    },
                    {},
                )
            return JsonResponse(
                {
                    "data": {
                        "agentActivity": {
                            "agentSession": {"id": item["agentSessionId"]},
                            "content": item["content"],
                        }
                    }
                },
                {},
            )

        client = self.client(request)
        with patch.object(self.linear, "ensure_activity", side_effect=client.ensure_activity):
            self.ingest(self.event())
            self.tick()
            self.assertEqual(len(self.hermes.requests), 1)
            self.hermes.finish(self.run_for(), "Verified result")
            self.tick(3)
            self.restart()
            self.tick(2)
        self.assertFalse(self.store.jobs())
        self.assertEqual(len(self.hermes.requests), 1)
        self.assertEqual(
            [item["content"]["type"] for item in activities.values()], ["thought", "response"]
        )

    def test_missing_activity_can_be_created_without_triggering_app_cooldown(self):
        missing = JsonResponse(
            {
                "data": None,
                "errors": [
                    {
                        "message": "Entity not found: AgentActivity",
                        "extensions": {"code": "INPUT_ERROR"},
                    }
                ],
            },
            {},
        )
        record = {"id": uid(100), "session": uid(10), "kind": "thought", "body": "Starting"}
        created = JsonResponse(
            {
                "data": {
                    "agentActivityCreate": {"success": True, "agentActivity": {"id": record["id"]}}
                }
            },
            {},
        )
        existing = JsonResponse(
            {
                "data": {
                    "agentActivity": {
                        "agentSession": {"id": record["session"]},
                        "content": {"type": record["kind"], "body": record["body"]},
                    }
                }
            },
            {},
        )
        request = Mock(side_effect=[missing, created, existing])
        client = self.client(request)
        agent = self.service.registry().agents["qui-gon"]
        client.ensure_activity(agent, record)
        client.ensure_activity(agent, record)
        self.assertEqual(request.call_count, 3)

    def test_other_graphql_errors_still_defer_and_do_not_create_activity(self):
        request = Mock(
            return_value=JsonResponse(
                {"errors": [{"message": "Other failure", "extensions": {"code": "INPUT_ERROR"}}]},
                {},
            )
        )
        client = self.client(request)
        agent = self.service.registry().agents["qui-gon"]
        record = {"id": uid(100), "session": uid(10), "kind": "thought", "body": "Starting"}
        with self.assertRaises(RemoteError):
            client.ensure_activity(agent, record)
        self.assertEqual(request.call_count, 1)

    def test_lost_create_receipt_is_verified_before_any_retry(self):
        record = {"id": uid(101), "session": uid(10), "kind": "thought", "body": "Starting"}
        missing = JsonResponse(
            {
                "data": None,
                "errors": [
                    {
                        "message": "Entity not found: AgentActivity",
                        "extensions": {"code": "INPUT_ERROR"},
                    }
                ],
            },
            {},
        )
        existing = JsonResponse(
            {
                "data": {
                    "agentActivity": {
                        "agentSession": {"id": record["session"]},
                        "content": {"type": record["kind"], "body": record["body"]},
                    }
                }
            },
            {},
        )
        request = Mock(side_effect=[missing, RemoteError(503), existing])
        client = self.client(request)
        agent = self.service.registry().agents["qui-gon"]
        with self.assertRaises(RemoteError):
            client.ensure_activity(agent, record)
        with self.assertRaises(RemoteError):
            client.ensure_activity(agent, record)
        self.assertEqual(request.call_count, 2)
        self.now += 400
        client.ensure_activity(agent, record)
        self.assertEqual(request.call_count, 3)
        self.assertIn("CouncilActivityCreate", request.call_args_list[1].kwargs["body"]["query"])
        self.assertIn("CouncilActivity", request.call_args_list[2].kwargs["body"]["query"])

    def test_http_and_graphql_rate_limits_share_persistent_cooldown(self):
        for response in (
            RemoteError(429, headers={"retry-after": "3600"}),
            JsonResponse(
                {"errors": [{"extensions": {"code": "RATELIMITED"}}]},
                {"Retry-After": "3600"},
            ),
        ):
            with self.subTest(response=type(response).__name__):
                request = Mock(side_effect=[response, {"data": {"ok": True}}])
                client = self.client(request)
                agent = self.service.registry().agents["qui-gon"]
                with self.assertRaises(RemoteError) as failure:
                    client.query(agent, "query Fixture{viewer{id}}", {})
                self.assertGreaterEqual(failure.exception.retry_at, self.now + 3600)
                client = self.client(request)
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [
                        pool.submit(client.query, agent, "query Fixture{viewer{id}}", {})
                        for _ in range(20)
                    ]
                    self.assertTrue(all(isinstance(f.exception(), RemoteError) for f in futures))
                self.assertEqual(request.call_count, 1)
                self.now += 3601
                self.assertTrue(client.query(agent, "query Fixture{viewer{id}}", {})["ok"])
                self.assertEqual(request.call_count, 2)

    def test_success_with_exhausted_budget_is_returned_but_next_call_waits(self):
        request = Mock(
            return_value=JsonResponse(
                {"data": {"ok": True}},
                {
                    "X-RateLimit-Requests-Remaining": "0",
                    "X-RateLimit-Requests-Reset": str((self.now + 600) * 1000),
                },
            )
        )
        client = self.client(request)
        agent = self.service.registry().agents["qui-gon"]
        self.assertTrue(client.query(agent, "query Fixture{viewer{id}}", {})["ok"])
        with self.assertRaises(RemoteError):
            self.client(request).query(agent, "query Fixture{viewer{id}}", {})
        self.assertEqual(request.call_count, 1)
        # A separate installed app is unaffected by this app's budget.
        self.assertTrue(
            client.query(self.service.registry().agents["dexter"], "query Fixture{viewer{id}}", {})[
                "ok"
            ]
        )
        self.assertEqual(request.call_count, 2)

    def test_transient_errors_back_off_without_refreshing_progress_or_token_leaks(self):
        request = Mock(side_effect=RemoteError(503))
        client = self.client(request)
        agent = self.service.registry().agents["qui-gon"]
        delays = []
        for _ in range(9):
            with self.assertRaises(RemoteError) as failure:
                client.query(agent, "query Fixture{viewer{id}}", {})
            delays.append(failure.exception.retry_at - self.now)
            self.assertNotIn("fixture-access", str(failure.exception))
            self.now = failure.exception.retry_at + 1
        self.assertEqual(delays, sorted(delays))
        self.assertLessEqual(max(delays), 306)
        self.assertIn("integration_unavailable", self.store.status()["recovery"])


class TransportRecoveryTests(unittest.TestCase):
    def test_reset_units_multiple_budgets_retry_after_and_malformed_headers(self):
        self.assertEqual(retry_deadline({"retry-after": "12"}, 100), 112)
        self.assertEqual(retry_deadline({"retry-after": "Thu, 01 Jan 1970 00:03:00 GMT"}, 100), 180)
        self.assertEqual(
            retry_deadline(
                {
                    "x-ratelimit-requests-remaining": "8",
                    "x-ratelimit-requests-reset": "999000",
                    "x-ratelimit-complexity-remaining": "0",
                    "x-ratelimit-complexity-reset": "200000",
                    "x-ratelimit-endpoint-requests-remaining": "0",
                    "x-ratelimit-endpoint-requests-reset": "300000",
                },
                100,
            ),
            301,
        )
        self.assertEqual(
            retry_deadline({"retry-after": "nan", "x-ratelimit-requests-remaining": "broken"}, 100),
            0,
        )

    def test_http_400_graphql_limit_preserves_only_safe_metadata(self):
        error = urllib.error.HTTPError(
            "https://api.linear.app/graphql",
            400,
            "private error",
            {"Retry-After": "60", "Set-Cookie": "private"},
            io.BytesIO(
                json.dumps(
                    {
                        "errors": [
                            {"message": "private content", "extensions": {"code": "RATELIMITED"}}
                        ]
                    }
                ).encode()
            ),
        )
        opener = Mock()
        opener.open.side_effect = error
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(RemoteError) as failure:
                request_json("https://api.linear.app/graphql")
        self.assertEqual(failure.exception.status, 429)
        self.assertEqual(failure.exception.headers, {"retry-after": "60"})
        self.assertNotIn("private", repr(failure.exception.headers))
