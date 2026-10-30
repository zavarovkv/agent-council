"""Real PTB extension contract and isolated model/HTTP surface, with fixture data only."""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from telegram import Chat, Message, Update, User
from telegram.ext import Application, ApplicationHandlerStop

sys.path.insert(0, "/opt/agent-council")
from secretary_worker.model import generate
from secretary_worker.service import Server, Service


class NativeHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_hook_intercepts_only_business_and_stays_closed_when_unconfigured(self):
        root = Path("/opt/hermes/plugins/council-secretary")
        name = "council_secretary_fixture"
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        factories = []
        module.register(SimpleNamespace(register_telegram_handler=factories.append))
        self.assertEqual(len(factories), 1)
        application = Application.builder().token("12345:fixture-token-not-a-real-bot").build()
        with patch.dict(
            os.environ, {"COUNCIL_SECRETARY_KEY": "", "COUNCIL_SECRETARY_OWNER_ID": ""}
        ):
            factories[0](application, None)
            factories[0](application, None)
        self.assertEqual(len(application.handlers[-1000]), 1)
        handler = application.handlers[-1000][0]
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(111, "private"),
            from_user=User(222, "Fixture", False),
            text="/model",
            business_connection_id="fixture-connection",
        )
        ordinary = Update(update_id=1, message=message)
        business = Update(update_id=2, business_message=message)
        self.assertFalse(handler.check_update(ordinary))
        self.assertTrue(handler.check_update(business))
        with self.assertRaises(ApplicationHandlerStop):
            await handler.callback(business, None)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "telegram-owner.json").write_text('{"telegram_id":12345}')
            queue = home / "plugin-data/council-secretary/queue.sqlite3"
            queue.parent.mkdir(parents=True)
            queue.write_bytes(b"corrupt database")
            closed = Application.builder().token("12345:another-fixture-token").build()
            with patch.dict(
                os.environ,
                {
                    "COUNCIL_SECRETARY_ENABLED": "true",
                    "COUNCIL_SECRETARY_KEY": "a" * 64,
                    "COUNCIL_SECRETARY_OWNER_ID": "12345",
                    "HERMES_TELEGRAM_ALLOWED_ID": "12345",
                    "HERMES_HOME": temporary,
                },
            ):
                factories[0](closed, None)
            with self.assertRaises(ApplicationHandlerStop):
                await closed.handlers[-1000][0].callback(business, None)


class WorkerModelTests(unittest.TestCase):
    def test_native_provider_gets_only_messages_and_never_tool_execution_or_reasoning_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "config.yaml").write_text(
                json.dumps({"model": {"provider": "openai-codex", "default": "gpt-6.1-sol"}})
            )
            with patch.dict(os.environ, {"HERMES_HOME": temporary}):
                with patch(
                    "agent.auxiliary_client.call_llm",
                    return_value={"choices": [{"message": {"content": "Final answer"}}]},
                ) as call:
                    self.assertEqual(
                        generate([{"role": "user", "content": "hello"}]), "Final answer"
                    )
                    self.assertIsNone(call.call_args.kwargs["tools"])
                    self.assertEqual(call.call_args.kwargs["provider"], "openai-codex")
                for message in (
                    {"content": "", "reasoning": "private reasoning"},
                    {"content": "text", "tool_calls": [{"name": "terminal"}]},
                ):
                    with patch(
                        "agent.auxiliary_client.call_llm",
                        return_value={"choices": [{"message": message}]},
                    ):
                        with self.assertRaises(ValueError):
                            generate([{"role": "user", "content": "hello"}])

    def test_real_http_requires_machine_auth_and_refuses_caller_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            key = "a" * 64
            service = Service(temporary, key, generate=lambda _messages: "Fixture reply")
            server = Server(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{server.server_port}"
            payload = {"request_id": "b" * 64, "conversation": "c" * 64, "text": "hello"}

            def request(value, *, authorized=True):
                return urllib.request.Request(
                    origin + "/reply",
                    data=json.dumps(value).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + (key if authorized else "invalid"),
                    },
                )

            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(request(payload, authorized=False), timeout=3)
                self.assertEqual(denied.exception.code, 401)
                with self.assertRaises(urllib.error.HTTPError) as invalid:
                    urllib.request.urlopen(request({**payload, "tools": ["terminal"]}), timeout=3)
                self.assertEqual(invalid.exception.code, 400)
                with urllib.request.urlopen(request(payload), timeout=3) as response:
                    self.assertEqual(json.load(response), {"reply": "Fixture reply"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
