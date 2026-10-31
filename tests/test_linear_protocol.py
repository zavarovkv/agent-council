"""OAuth replay/rotation, HTTP separation, admission races and private preparation."""

import concurrent.futures
import copy
import http.client
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from test_linear_agent import NOW, FakeHermes, FakeLinear, configuration, uid

from council_ops.linear import prepare_files
from linear_agent.config import Registry
from linear_agent.events import Event
from linear_agent.http import Server
from linear_agent.linear import API, TOKEN, Linear
from linear_agent.service import Service
from linear_agent.store import Store


class LinearProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.value = configuration(self.home)
        self.registry = Registry.load(self.home)
        self.agent = self.registry.agents["qui-gon"]
        self.store = Store(self.home, clock=lambda: NOW)

    def test_oauth_pkce_browser_binding_wrong_workspace_and_single_use(self):
        requests = []

        def request(url, **kwargs):
            requests.append((url, kwargs))
            if url == TOKEN:
                return {
                    "access_token": "fixture-access",
                    "refresh_token": "fixture-refresh",
                    "expires_in": 86400,
                    "token_type": "Bearer",
                    "scope": "read,write,app:assignable,app:mentionable",
                }
            if url == API:
                return {"data": {"viewer": {"id": uid(1000)}, "organization": {"id": uid(1)}}}
            raise AssertionError("Unexpected destination")

        linear = Linear(self.store, request=request, clock=lambda: NOW)
        query = parse_qs(
            urlsplit(linear.authorization(self.registry, self.agent, "browser-a")).query
        )
        self.assertEqual(query["actor"], ["app"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn("client_secret", query)
        with self.assertRaises(PermissionError):
            linear.callback(
                self.registry,
                self.agent,
                state=query["state"][0],
                browser="browser-b",
                code="fixture-code",
            )
        self.assertFalse(requests)
        linear.callback(
            self.registry,
            self.agent,
            state=query["state"][0],
            browser="browser-a",
            code="fixture-code",
        )
        self.assertEqual(self.store.installation("qui-gon")["app_user"], uid(1000))
        with self.assertRaises(PermissionError):
            linear.callback(
                self.registry,
                self.agent,
                state=query["state"][0],
                browser="browser-a",
                code="fixture-code",
            )
        self.assertEqual(len(requests), 2)

    def test_refresh_is_serialized_and_persists_rotated_grant(self):
        self.store.install("qui-gon", uid(1000), uid(1), "expired", "original-refresh", NOW)
        calls = []

        def request(url, **kwargs):
            self.assertEqual(url, TOKEN)
            calls.append(parse_qs(kwargs["body"].decode()))
            time.sleep(0.02)
            return {
                "access_token": "new-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 86400,
                "token_type": "Bearer",
            }

        linear = Linear(self.store, request=request, clock=lambda: NOW)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: linear.access(self.agent), range(8)))
        self.assertEqual(results, ["new-access"] * 8)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.installation("qui-gon")["refresh"], "rotated-refresh")

    def test_concurrent_replay_admits_exactly_one_job_and_corrupt_state_fails_closed(self):
        event = Event("a" * 64, "qui-gon", uid(10), "created", "", uid(2), "Fixture", NOW)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            admitted = list(pool.map(lambda _: self.store.admit(event, "a" * 32), range(16)))
        self.assertEqual(sum(admitted), 1)
        self.assertEqual(len(self.store.jobs()), 1)
        (self.home / "agents.json").write_text('{"enabled": true}')
        with self.assertRaises(ValueError):
            Registry.load(self.home)

    def test_public_http_cannot_reach_internal_tools_or_leak_callback_details(self):
        service = Service(self.home, store=self.store, linear=FakeLinear(), hermes=FakeHermes())
        for internal in (False, True):
            server = Server(("127.0.0.1", 0), service, internal=internal)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                path = "/qui-gon/delegate" if internal else "/integrations/linear/qui-gon/delegate"
                connection.request(
                    "POST", path, body="{}", headers={"Content-Type": "application/json"}
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 401 if internal else 404)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertNotIn("client-secret", response.read().decode())
                connection.close()
                if not internal:
                    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                    connection.request(
                        "GET",
                        "/integrations/linear/qui-gon/oauth/callback?state=fixture&code=private-code",
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    self.assertNotIn("private-code", response.read().decode())
                    connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_prepare_never_copies_owner_grants_and_preserves_executor_state(self):
        home = self.home / "profile"
        home.mkdir()
        (home / "auth.json").write_text("PRIVATE OWNER GRANT")
        (home / "SOUL.md").write_text("PRIVATE OWNER INSTRUCTIONS")
        plan = {
            "schema": 1,
            "owner": uid(2),
            "organization": uid(1),
            "teams": [uid(3)],
            "origin": "https://fixture.example.test",
            "agents": ["qui-gon", "dexter"],
        }
        model = {"provider": "fixture", "default": "fixture"}
        repository = Path(__file__).resolve().parents[1]
        prepare_files(home, plan, model, repository, os.getuid(), os.getgid())
        base = home / ".council/linear"
        before = (base / "bridge/qui-gon.env").read_bytes()
        (base / "qui-gon/auth.json").write_text("EXECUTOR OWN GRANT")
        prepare_files(home, copy.deepcopy(plan), model, repository, os.getuid(), os.getgid())
        self.assertEqual((base / "bridge/qui-gon.env").read_bytes(), before)
        self.assertEqual((base / "qui-gon/auth.json").read_text(), "EXECUTOR OWN GRANT")
        for path in base.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"PRIVATE OWNER", path.read_bytes())
