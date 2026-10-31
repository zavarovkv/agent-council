import copy
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from linear_agent.config import Registry
from linear_agent.events import verified_payload
from linear_agent.service import Service
from linear_agent.store import Store
from linear_agent.transport import RemoteError

NOW = 1_800_000_000


def uid(number):
    return str(uuid.UUID(int=number))


def configuration(home):
    agents = {}
    for index, slug in enumerate(("qui-gon", "dexter"), 1):
        agents[slug] = {
            "client_id": uid(index + 100),
            "client_secret": "client-secret-" + slug * 8,
            "webhook_secret": "webhook-" + slug * 8,
            "executor_url": "http://"
            + ("linear-executor" if index == 1 else "dexter-executor")
            + ":8642",
            "executor_key": str(index) * 64,
            "delegates": ["dexter"] if index == 1 else [],
        }
    value = {
        "schema": 1,
        "enabled": True,
        "organization": uid(1),
        "owner": uid(2),
        "teams": [uid(3)],
        "origin": "https://council.example.test",
        "revision": "a" * 32,
        "admission_since": NOW - 1,
        "agents": agents,
    }
    path = home / "agents.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return value


def session(slug="qui-gon", sid=10, issue=20, owner=2, parent=None, creator=None):
    return {
        "id": uid(sid),
        "appUser": {"id": uid({"qui-gon": 1000, "dexter": 1001, "marketing": 1002}[slug])},
        "creator": {"id": uid(owner)} if owner else None,
        "dismissedAt": None,
        "archivedAt": None,
        "status": "active",
        "issue": {
            "id": uid(issue),
            "title": "Fixture issue",
            "description": "Fixture task",
            "url": "https://linear.app/fixture/issue/F-1",
            "team": {"id": uid(3)},
            "parent": {"id": parent} if parent else None,
            "delegate": {"id": uid({"qui-gon": 1000, "dexter": 1001, "marketing": 1002}[slug])},
            "creator": {"id": uid(creator or owner or 2)},
        },
        "comment": {"body": "Fixture directive", "user": {"id": uid(2)}},
    }


class FakeLinear:
    def __init__(self):
        self.sessions = {uid(10): session()}
        self.activities = {}
        self.posts = {}
        self.issues = {}
        self.uncertain_post = False
        self.uncertain_child = False
        self.owner_url = "https://linear.app/fixture/profiles/owner"
        self.owner_mention_calls = 0
        self.issue_writes = []
        self.comments = {}

    def workflow(self, agent, team):
        return {
            "id": team,
            "name": "Fixture",
            "states": {
                "nodes": [
                    {"id": uid(200 + i), "name": name, "type": category, "position": i}
                    for i, (name, category) in enumerate(
                        (
                            ("Backlog", "backlog"),
                            ("Todo", "unstarted"),
                            ("In Progress", "started"),
                            ("Done", "completed"),
                            ("Canceled", "canceled"),
                        )
                    )
                ]
            },
            "projects": {
                "nodes": [{"id": uid(300), "name": "Fixture"}],
                "pageInfo": {"hasNextPage": False},
            },
            "members": {
                "nodes": [{"id": uid(2), "name": "Owner", "active": True, "app": False}],
                "pageInfo": {"hasNextPage": False},
            },
            "labels": {
                "nodes": [{"id": uid(400), "name": "Fixture"}],
                "pageInfo": {"hasNextPage": False},
            },
        }

    def issue_details(self, agent, issue):
        value = self.issues.get(issue) or next(
            (s["issue"] for s in self.sessions.values() if s["issue"]["id"] == issue), None
        )
        if value is None:
            return None
        value.setdefault("state", self.workflow(agent, uid(3))["states"]["nodes"][0])
        value.setdefault("children", {"nodes": [], "pageInfo": {"hasNextPage": False}})
        value.setdefault("comments", {"nodes": []})
        value.setdefault("labels", {"nodes": []})
        return copy.deepcopy(value)

    def update_issue(self, agent, issue, fields):
        self.issue_writes.append((issue, fields))
        value = self.issues.get(issue) or next(
            s["issue"] for s in self.sessions.values() if s["issue"]["id"] == issue
        )
        for name, field in fields.items():
            if name == "stateId":
                value["state"] = next(
                    s for s in self.workflow(agent, uid(3))["states"]["nodes"] if s["id"] == field
                )
            elif name in {"projectId", "parentId", "assigneeId"}:
                value[name[:-2]] = {"id": field} if field else None
            elif name == "labelIds":
                value["labels"] = {"nodes": [{"id": label} for label in field]}
            else:
                value[name] = field

    def create_issue(self, agent, fields):
        identity = fields["id"]
        if identity in self.issues:
            raise RemoteError()
        self.issues[identity] = {
            "id": identity,
            "identifier": "F-3",
            "url": "https://linear.app/fixture/issue/F-3",
            "team": {"id": fields["teamId"]},
            "creator": {"id": uid(1000)},
        }
        self.update_issue(
            agent, identity, {k: v for k, v in fields.items() if k not in {"id", "teamId"}}
        )

    def comment(self, agent, identity):
        return copy.deepcopy(self.comments.get(identity))

    def create_comment(self, agent, identity, issue, body):
        self.comments.setdefault(identity, {"id": identity, "issue": {"id": issue}, "body": body})

    def owner_mention(self, agent, registry):
        self.owner_mention_calls += 1
        return self.owner_url

    def session(self, agent, sid):
        return copy.deepcopy(self.sessions[sid])

    def activity(self, agent, aid):
        return copy.deepcopy(self.activities[aid])

    def ensure_activity(self, agent, record):
        self.posts.setdefault(record["id"], {**record})
        if self.uncertain_post:
            self.uncertain_post = False
            raise RemoteError()

    def ensure_child(self, agent, child, *, parent_issue, team, delegate):
        if child["issue"] not in self.issues:
            self.issues[child["issue"]] = {
                "id": child["issue"],
                "url": "https://linear.app/fixture/issue/F-2",
            }
            target = "marketing" if delegate == uid(1002) else "dexter"
            value = session(target, sid=11, owner=0, parent=parent_issue, creator=1000)
            value["issue"]["id"] = child["issue"]
            self.sessions[uid(11)] = value
            self.issues[child["issue"]] = value["issue"]
        if self.uncertain_child:
            self.uncertain_child = False
            raise RemoteError()
        return self.issues[child["issue"]]


class FakeHermes:
    def __init__(self):
        self.runs = {}
        self.requests = {}
        self.answers = []
        self.stops = []
        self.lost_start = False

    def ready(self, agent):
        pass

    def run_id(self, value):
        from linear_agent.hermes import Hermes

        return Hermes.run_id(value)

    def start(self, agent, sid, text, identity):
        if identity not in self.requests:
            run = "run_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
            self.requests[identity] = {
                "run_id": run,
                "session": sid,
                "text": text,
                "agent": agent.slug,
            }
            self.runs[run] = {
                "run_id": run,
                "session_id": sid,
                "status": "running",
                "pending_controls": [],
                "execution_generation": 1,
            }
        if self.lost_start:
            self.lost_start = False
            raise RemoteError()
        return {"run_id": self.requests[identity]["run_id"]}

    def run(self, agent, run):
        return copy.deepcopy(self.runs[run])

    def stop(self, agent, run):
        self.stops.append(run)
        self.runs[run]["status"] = "cancelled"
        return {"status": "stopping"}

    def clarify(self, agent, run, control, answer):
        self.answers.append((run, control, answer))
        self.runs[run]["pending_controls"] = []
        return {"answered": True}

    def finish(self, run, text="Fixture result"):
        self.runs[run].update(status="completed", output=text, pending_controls=[])


class LinearAgentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.config = configuration(self.home)
        self.store = Store(self.home, clock=lambda: NOW)
        for index, slug in enumerate(("qui-gon", "dexter")):
            self.store.install(
                slug, uid(1000 + index), uid(1), "fixture-access", "fixture-refresh", NOW + 86400
            )
        self.linear, self.hermes = FakeLinear(), FakeHermes()
        self.service = Service(
            self.home, store=self.store, linear=self.linear, hermes=self.hermes, clock=lambda: NOW
        )

    def event(
        self,
        *,
        slug="qui-gon",
        sid=10,
        action="created",
        actor=2,
        aid=30,
        text="Fixture owner request",
        stop=False,
    ):
        remote = self.linear.sessions[uid(sid)]
        value = {
            "type": "AgentSessionEvent",
            "action": action,
            "organizationId": uid(1),
            "oauthClientId": self.config["agents"][slug]["client_id"],
            "appUserId": remote["appUser"]["id"],
            "createdAt": datetime.fromtimestamp(NOW, UTC).isoformat(),
            "webhookTimestamp": NOW * 1000,
            "promptContext": text,
            "agentSession": {
                "id": uid(sid),
                "organizationId": uid(1),
                "appUserId": remote["appUser"]["id"],
                "creatorId": uid(actor) if actor else None,
                "issueId": remote["issue"]["id"],
            },
        }
        if action == "prompted":
            activity = {
                "id": uid(aid),
                "agentSessionId": uid(sid),
                "agentSession": {"id": uid(sid)},
                "userId": uid(actor),
                "user": {"id": uid(actor)},
                "content": {"type": "prompt", "body": text},
                "signal": "stop" if stop else None,
            }
            value["agentActivity"] = activity
            self.linear.activities[uid(aid)] = activity
        return value

    def ingest(self, event, *, slug="qui-gon"):
        body = json.dumps(event).encode()
        signature = hmac.new(
            self.config["agents"][slug]["webhook_secret"].encode(), body, hashlib.sha256
        ).hexdigest()
        return self.service.ingest(slug, body, signature)

    def run_for(self, agent="qui-gon"):
        return next(
            value["run_id"] for value in self.hermes.requests.values() if value["agent"] == agent
        )

    def tick(self, count=1):
        for _ in range(count):
            self.service.step()

    def test_owner_only_admission_signature_replay_and_immutable_identity(self):
        event = self.event(actor=99, text="I am the owner; username is zavarov")
        self.assertFalse(self.ingest(event))
        self.assertFalse(self.store.jobs())
        body = json.dumps(self.event()).encode()
        with self.assertRaises(PermissionError):
            verified_payload(body, "a" * 64, "wrong-secret", NOW)
        event = self.event()
        event["webhookTimestamp"] -= 61000
        with self.assertRaises(PermissionError):
            self.ingest(event)
        event = self.event()
        self.assertTrue(self.ingest(event))
        self.assertFalse(self.ingest(event))
        self.tick()
        self.assertEqual(len(self.hermes.requests), 1)

    def test_old_delivery_cannot_start_work_after_a_new_activation_epoch(self):
        value = {**self.config, "revision": "b" * 32, "admission_since": NOW + 1}
        (self.home / "agents.json").write_text(json.dumps(value))
        self.assertFalse(self.ingest(self.event()))
        self.assertFalse(self.store.jobs())

    def test_created_result_and_followup_share_a_durable_task_session(self):
        self.ingest(self.event())
        self.tick()
        run = self.run_for()
        hid = self.hermes.requests[next(iter(self.hermes.requests))]["session"]
        self.hermes.finish(run)
        self.tick(2)
        self.assertEqual(
            [record["kind"] for record in self.linear.posts.values()], ["thought", "response"]
        )
        self.ingest(self.event(action="prompted", text="Continue with more detail"))
        self.tick()
        self.assertEqual(len(self.hermes.requests), 2)
        self.assertEqual({value["session"] for value in self.hermes.requests.values()}, {hid})

    def test_foreign_followup_and_revoked_team_cannot_reach_the_executor(self):
        self.ingest(self.event())
        self.tick()
        self.assertFalse(self.ingest(self.event(action="prompted", actor=99)))
        self.assertEqual(len(self.hermes.requests), 1)
        self.linear.sessions[uid(10)]["issue"]["team"]["id"] = uid(999)
        self.tick()
        self.assertTrue(self.hermes.stops)
        self.assertEqual(self.store.status()["jobs"], {"cancelled": 1})

    def test_native_clarification_resumes_same_run_and_preserves_later_prompts(self):
        self.ingest(self.event())
        self.tick()
        run = self.run_for()
        self.hermes.runs[run]["pending_controls"] = [
            {"kind": "clarify", "prompt_id": "fixture-question", "question": "Which competitor?"}
        ]
        self.tick()
        self.assertIn("elicitation", [record["kind"] for record in self.linear.posts.values()])
        question = next(
            record for record in self.linear.posts.values() if record["kind"] == "elicitation"
        )
        self.assertIn(self.linear.owner_url, question["body"])
        self.assertIn("Which competitor?", question["body"])
        self.assertIn("Ответьте в этой ветке", question["body"])
        self.tick(3)
        self.assertEqual(self.linear.owner_mention_calls, 1)
        self.ingest(self.event(action="prompted", text="Competitor A", aid=31))
        self.ingest(self.event(action="prompted", text="Also include B", aid=32))
        self.tick()
        self.assertEqual(len(self.hermes.answers), 1)
        self.assertEqual(self.hermes.answers[0][2], "Competitor A")
        self.assertEqual(len(self.hermes.requests), 1)
        self.hermes.finish(run)
        self.tick(3)
        self.assertEqual(len(self.hermes.requests), 2)
        self.assertEqual(list(self.hermes.requests.values())[-1]["text"], "Also include B")

    def test_question_mention_survives_restart_lost_receipt_and_profile_rename(self):
        self.ingest(self.event())
        self.tick()
        run = self.run_for()
        self.hermes.runs[run]["pending_controls"] = [
            {"kind": "clarify", "prompt_id": "fixture-question", "question": "Which competitor?"}
        ]
        self.linear.uncertain_post = True
        self.tick()
        self.assertEqual(self.linear.owner_mention_calls, 1)
        self.linear.owner_url = "https://linear.app/fixture/profiles/renamed-owner"
        self.service = Service(
            self.home,
            store=Store(self.home, clock=lambda: NOW),
            linear=self.linear,
            hermes=self.hermes,
            clock=lambda: NOW,
        )
        self.tick(3)
        questions = [
            record for record in self.linear.posts.values() if record["kind"] == "elicitation"
        ]
        self.assertEqual(len(questions), 1)
        self.assertEqual(self.linear.owner_mention_calls, 1)
        self.assertEqual(len(self.hermes.requests), 1)
        self.ingest(self.event(action="prompted", text="Competitor A", aid=31))
        self.tick()
        self.assertEqual(self.hermes.answers[0][2], "Competitor A")
        self.assertEqual(len(self.hermes.requests), 1)

    def test_lost_start_and_publication_responses_do_not_repeat_model_or_activity(self):
        self.ingest(self.event())
        self.hermes.lost_start = True
        self.tick()
        self.service = Service(
            self.home,
            store=Store(self.home, clock=lambda: NOW),
            linear=self.linear,
            hermes=self.hermes,
            clock=lambda: NOW,
        )
        self.tick()
        self.assertEqual(len(self.hermes.requests), 1)
        self.hermes.finish(self.run_for())
        self.linear.uncertain_post = True
        self.tick(3)
        self.assertEqual([row["kind"] for row in self.linear.posts.values()].count("response"), 1)

    def test_stop_is_owner_only_and_cancels_queued_continuations(self):
        self.ingest(self.event())
        self.tick()
        self.ingest(self.event(action="prompted", aid=31, text="Next question"))
        self.assertFalse(self.ingest(self.event(action="prompted", actor=99, aid=32, stop=True)))
        self.ingest(self.event(action="prompted", aid=33, stop=True))
        self.tick(2)
        self.assertEqual(len(self.hermes.requests), 1)
        self.assertTrue(self.hermes.stops)

    def test_sub_issue_delegation_is_idempotent_and_returns_results_to_parent(self):
        self.assert_specialist_roundtrip("dexter")

    def test_marketing_sub_issue_returns_to_the_same_coordinator_session(self):
        self.config["agents"]["marketing"] = {
            **self.config["agents"]["dexter"],
            "client_id": uid(103),
            "client_secret": "marketing-client-fixture",
            "webhook_secret": "marketing-webhook-fixture",
            "executor_key": "3" * 64,
            "executor_url": "http://marketing-executor:8642",
        }
        self.config["agents"]["qui-gon"]["delegates"].append("marketing")
        (self.home / "agents.json").write_text(json.dumps(self.config))
        self.store.install("marketing", uid(1002), uid(1), "fixture", "fixture", NOW + 86400)
        self.assert_specialist_roundtrip("marketing")

    def assert_specialist_roundtrip(self, target):
        self.ingest(self.event())
        self.tick()
        root = self.store.session("qui-gon", uid(10))
        payload = {
            "run": self.run_for(),
            "session": root["hermes"],
            "target": target,
            "key": "competitor-a",
            "title": "Research competitor A",
            "description": "Find sources and compare features.",
        }
        self.linear.uncertain_child = True
        with self.assertRaises(RemoteError):
            self.service.delegate("qui-gon", payload)
        result = self.service.delegate("qui-gon", payload)
        self.assertEqual(result, self.service.delegate("qui-gon", payload))
        self.assertEqual(len(self.linear.issues), 1)
        self.hermes.finish(self.run_for(), "Delegated the research")
        self.tick()
        self.assertEqual(self.store.jobs()[0]["state"], "waiting_children")
        self.ingest(self.event(slug=target, sid=11, actor=0), slug=target)
        self.tick()
        child_run = self.run_for(target)
        child = self.store.session(target, uid(11))
        with self.assertRaises(PermissionError):
            self.service.delegate(
                target,
                {**payload, "run": child_run, "session": child["hermes"], "target": "qui-gon"},
            )
        self.hermes.finish(child_run, "Verified competitor findings")
        self.tick(4)
        parent_turns = [
            value for value in self.hermes.requests.values() if value["agent"] == "qui-gon"
        ]
        self.assertEqual(len(parent_turns), 2)
        self.assertIn("Verified competitor findings", parent_turns[-1]["text"])
        self.assertEqual(parent_turns[-1]["session"], root["hermes"])
        self.hermes.finish(parent_turns[-1]["run_id"], "Combined research")
        self.tick(3)
        self.assertFalse(self.store.jobs())
        self.assertTrue(
            any(item["body"] == "Combined research" for item in self.linear.posts.values())
        )

    def test_manually_delegated_owner_sub_issue_joins_the_known_coordinator(self):
        self.ingest(self.event())
        self.tick()
        child = session("dexter", sid=11, issue=21, parent=uid(20))
        self.linear.sessions[uid(11)] = child
        self.ingest(self.event(slug="dexter", sid=11), slug="dexter")
        self.tick()
        self.assertEqual(self.store.session("dexter", uid(11))["depth"], 1)
        self.hermes.finish(self.run_for("dexter"), "Manual child research")
        self.hermes.finish(self.run_for(), "Waiting for research")
        self.tick(5)
        parents = [item for item in self.hermes.requests.values() if item["agent"] == "qui-gon"]
        self.assertEqual(len(parents), 2)
        self.assertIn("Manual child research", parents[-1]["text"])

    def test_coordinator_can_receive_an_owner_sub_issue_under_an_unmanaged_parent(self):
        self.linear.sessions[uid(10)]["issue"]["parent"] = {"id": uid(999)}
        self.ingest(self.event())
        self.tick()
        self.assertEqual(len(self.hermes.requests), 1)
        self.assertEqual(self.store.session("qui-gon", uid(10))["depth"], 0)

    def test_child_cannot_continue_after_owner_stops_parent(self):
        self.ingest(self.event())
        self.tick()
        self.linear.sessions[uid(11)] = session("dexter", sid=11, issue=21, parent=uid(20))
        self.ingest(self.event(slug="dexter", sid=11), slug="dexter")
        self.tick()
        self.ingest(self.event(action="prompted", aid=33, stop=True))
        self.tick(3)
        self.assertIn(self.run_for("dexter"), self.hermes.stops)
        self.assertFalse(self.store.jobs())

    def test_policy_reload_cancels_old_work_and_unsent_output(self):
        self.ingest(self.event())
        self.tick()
        value = self.config | {"enabled": False, "revision": "b" * 32}
        (self.home / "agents.json").write_text(json.dumps(value))
        self.tick()
        self.assertTrue(self.hermes.stops)
        self.assertFalse(self.store.jobs())
        self.assertEqual([row["kind"] for row in self.linear.posts.values()], ["thought"])

    def test_profile_and_endpoint_overrides_are_rejected(self):
        for change in ({"executor_url": "http://hermes:9119"}, {"delegates": ["qui-gon"]}):
            candidate = copy.deepcopy(self.config)
            candidate["agents"]["qui-gon"].update(change)
            (self.home / "agents.json").write_text(json.dumps(candidate))
            with self.subTest(change=change), self.assertRaises(ValueError):
                Registry.load(self.home)
