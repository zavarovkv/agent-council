"""Handoff transport identity, private routing and retained-state compatibility."""

import http.client
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import linear_tools
from council_ops.config import OperationError
from council_ops.linear_state import has_handoffs, require_capability
from linear_agent.http import Server


class HandoffToolTests(unittest.TestCase):
    def test_tool_schema_and_handler_exclude_model_supplied_identity(self):
        registered = {}

        def collect(**fields):
            registered[fields["name"]] = fields

        with (
            patch("council_workspace.client.register"),
            patch("council_workspace.google_client.register"),
        ):
            linear_tools.register(SimpleNamespace(register_tool=collect))
        handoff = registered["linear_handoff"]
        self.assertEqual(handoff["toolset"], "council-linear")
        parameters = handoff["schema"]["parameters"]
        self.assertFalse(parameters["additionalProperties"])
        self.assertEqual(parameters["properties"]["action"]["enum"], ["submit", "get", "resume"])
        self.assertTrue(
            {"run", "session", "sender", "recipient", "issue"}.isdisjoint(parameters["properties"])
        )
        self.assertEqual(parameters["properties"]["details"]["maxLength"], 24000)
        self.assertEqual(parameters["properties"]["artifacts"]["maxItems"], 8)
        self.assertEqual(parameters["properties"]["view"]["enum"], ["details", "checkpoint"])
        with patch("linear_tools.call", return_value="receipt") as call:
            self.assertEqual(handoff["handler"]({"action": "get", "event_id": "event"}), "receipt")
            call.assert_called_once_with("handoff", {"action": "get", "event_id": "event"})

    def test_transport_overwrites_untrusted_identity_with_gateway_context(self):
        gateway = ModuleType("gateway")
        context = ModuleType("gateway.session_context")
        context.get_session_env = lambda name, default: {
            "HERMES_SESSION_KEY": "trusted-run",
            "HERMES_SESSION_ID": "trusted-session",
        }.get(name, default)
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"event_id":"saved-event"}')
        with (
            patch.dict(sys.modules, {"gateway": gateway, "gateway.session_context": context}),
            patch.dict(os.environ, {"COUNCIL_LINEAR_AGENT": "dexter", "API_SERVER_KEY": "key"}),
            patch("urllib.request.build_opener", return_value=opener),
        ):
            result = linear_tools.call(
                "handoff", {"action": "submit", "run": "forged", "session": "another-agent"}
            )
        self.assertEqual(json.loads(result), {"event_id": "saved-event"})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://linear-adapter:9122/dexter/handoff")
        self.assertEqual(
            json.loads(request.data),
            {"action": "submit", "run": "trusted-run", "session": "trusted-session"},
        )


class HandoffHTTPTests(unittest.TestCase):
    def serve(self, internal):
        handoff = Mock(return_value={"event_id": "receipt"})
        service = SimpleNamespace(
            registry=lambda: SimpleNamespace(
                agents={"dexter": SimpleNamespace(executor_key="test-key")}
            ),
            handoff=handoff,
        )
        server = Server(("127.0.0.1", 0), service, internal=internal)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.addCleanup(close)
        return server, handoff

    def request(self, server, path, authorization=None, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        headers = {"Content-Type": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        connection.request("POST", path, json.dumps(body or {"action": "get"}), headers)
        response = connection.getresponse()
        status, value = response.status, json.loads(response.read())
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        connection.close()
        return status, value

    def test_handoff_requires_private_listener_and_exact_executor_key(self):
        for internal in (False, True):
            server, handoff = self.serve(internal)
            path = "/dexter/handoff" if internal else "/integrations/linear/dexter/handoff"
            for key in (None, "Bearer wrong", "Bearer test-key"):
                status, _value = self.request(server, path, key)
                self.assertEqual(
                    status, (200 if key == "Bearer test-key" else 401) if internal else 404
                )
            self.assertEqual(handoff.call_count, 1 if internal else 0)

    def test_handoff_preserves_actionable_validation_errors_without_success(self):
        server, handoff = self.serve(True)
        for error, expected in (
            (ValueError("Visible message exceeds 700 characters."), 400),
            (PermissionError("Only the coordinator may resume this child."), 403),
        ):
            handoff.side_effect = error
            status, value = self.request(server, "/dexter/handoff", "Bearer test-key")
            self.assertEqual(status, expected)
            self.assertEqual(value, {"error": str(error)})

    def test_duplicate_authorization_is_rejected_before_handoff(self):
        server, handoff = self.serve(True)
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        connection.putrequest("POST", "/dexter/handoff")
        connection.putheader("Authorization", "Bearer test-key")
        connection.putheader("Authorization", "Bearer wrong")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "2")
        connection.endheaders(b"{}")
        response = connection.getresponse()
        self.assertEqual(response.status, 401)
        response.read()
        connection.close()
        handoff.assert_not_called()


class HandoffCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.path = self.home / ".council/linear/bridge/linear.sqlite3"

    def test_old_or_empty_store_does_not_require_capability_or_migrate(self):
        self.assertFalse(has_handoffs(self.home))
        self.assertFalse(self.path.parent.exists())
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE jobs (key TEXT)")
        before = self.path.read_bytes()
        self.assertFalse(has_handoffs(self.home))
        self.assertEqual(self.path.read_bytes(), before)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE handoffs (event_id TEXT)")
        self.assertFalse(has_handoffs(self.home))

    def test_retained_state_requires_capability_for_restore_and_rollback(self):
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE handoffs (event_id TEXT)")
            db.execute("INSERT INTO handoffs VALUES ('durable-event')")
        before = self.path.read_bytes()
        runtime = object()
        with patch("council_ops.instructions.verify_capabilities") as verify:
            require_capability(runtime, self.home, image="candidate-image")
            verify.assert_called_once_with(runtime, ["linear-handoff-v1"], image="candidate-image")
        self.assertEqual(self.path.read_bytes(), before)
        with patch(
            "council_ops.instructions.verify_capabilities",
            side_effect=OperationError("incompatible"),
        ):
            with self.assertRaisesRegex(OperationError, "incompatible"):
                require_capability(runtime, self.home, image="old-image")

    def test_unreadable_or_symlinked_state_fails_closed(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"not a database")
        with self.assertRaisesRegex(OperationError, "Cannot verify"):
            has_handoffs(self.home)
        self.path.unlink()
        self.path.symlink_to(self.home / "missing.sqlite3")
        with self.assertRaisesRegex(OperationError, "symlinked"):
            has_handoffs(self.home)


if __name__ == "__main__":
    unittest.main()
