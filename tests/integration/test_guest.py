"""Real PTB Guest Mode decoding, dispatch and answerGuestQuery serialization offline."""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram import Bot, Update
from telegram.ext import Application, TypeHandler
from telegram.request import BaseRequest


def plugin(name):
    root = Path("/opt/hermes/plugins/council-secretary")
    spec = importlib.util.spec_from_file_location(
        name, root / "__init__.py", submodule_search_locations=[str(root)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def event(actor=12345, number=1):
    return Update.de_json(
        {
            "update_id": number,
            "guest_message": {
                "message_id": number,
                "date": 1_000_000,
                "guest_query_id": f"fixture-guest-{number}",
                "chat": {"id": -100222, "type": "supergroup", "title": "Fixture"},
                "from": {"id": actor, "first_name": "Fixture caller", "is_bot": False},
                "text": "@fixture_bot Explain this",
                "reply_to_message": {
                    "message_id": 20,
                    "date": 999_999,
                    "chat": {"id": -100222, "type": "supergroup", "title": "Fixture"},
                    "from": {"id": 77777, "first_name": "Other", "is_bot": False},
                    "text": "Quoted fixture text",
                },
            },
        },
        None,
    )


class Request(BaseRequest):
    def __init__(self):
        self.answers = []
        self.edits = []
        self.fail_send = False

    @property
    def read_timeout(self):
        return 10

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint == "getMe":
            result = {
                "id": 12345,
                "is_bot": True,
                "first_name": "Fixture bot",
                "username": "fixture_bot",
                "supports_guest_queries": True,
            }
        elif endpoint == "answerGuestQuery":
            self.answers.append(request_data.parameters)
            if self.fail_send:
                raise TimeoutError("Fixture ambiguous delivery")
            result = {"inline_message_id": "fixture-inline-message"}
        elif endpoint == "editMessageText":
            self.edits.append(request_data.parameters)
            result = True
        else:
            raise AssertionError(
                "Guest transport must not use sendMessage or start another poller."
            )
        return 200, json.dumps({"ok": True, "result": result}).encode()


class GuestSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_dispatcher_stops_owner_spouse_and_foreign_guests_before_private_handlers(
        self,
    ):
        module = plugin("council_guest_dispatch_fixture")
        factories = []
        module.register(
            SimpleNamespace(register_telegram_handler=factories.append, register_locale=Mock())
        )
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "telegram-owner.json").write_text('{"telegram_id":12345}')
            app = Application.builder().token("12345:fixture-token").build()
            with patch.dict(
                os.environ,
                {
                    "HERMES_HOME": temporary,
                    "COUNCIL_SECRETARY_ENABLED": "true",
                    "COUNCIL_SECRETARY_KEY": "a" * 64,
                    "COUNCIL_SECRETARY_OWNER_ID": "12345",
                    "HERMES_TELEGRAM_ALLOWED_ID": "12345",
                    "COUNCIL_SECRETARY_PUBLIC_DMS": "true",
                },
            ):
                factories[0](app, None)
                factories[0](app, None)
            self.assertEqual(len(app.handlers[-1100]), 1)
            private = AsyncMock()
            app.add_handler(TypeHandler(Update, private), group=0)
            app._initialized = True
            # Missing policy is off by default, but every guest update is still intercepted.
            for actor in (12345, 67890, 77777):
                update = event(actor)
                self.assertTrue(app.handlers[-1100][0].check_update(update))
                self.assertFalse(app.handlers[-1000][0].check_update(update))
                self.assertFalse(app.handlers[-1001][0].check_update(update))
                await app.process_update(update)
            await app.process_update(Update(9, api_kwargs={"guest_message": {}}))
            private.assert_not_called()
            self.assertIn("guest_message", Update.ALL_TYPES)

    async def test_actual_sdk_formats_markdown_and_sends_only_once_without_chat_destination(self):
        module = plugin("council_guest_wire_fixture")
        guest = __import__(module.__name__ + ".guest", fromlist=["GuestBridge"])
        store_module = __import__(module.__name__ + ".guest_store", fromlist=["GuestStore"])
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            policy = home / "plugin-data/council-secretary/guest.json"
            policy.parent.mkdir(parents=True)
            policy.write_text(json.dumps({"schema": 1, "enabled": True, "revision": "a" * 32}))
            policy.chmod(0o600)
            (home / "telegram-owner.json").write_text('{"telegram_id":12345}')
            request = Request()
            async with Bot(
                "12345:fixture-token", request=request, get_updates_request=request
            ) as bot:
                tasks = []

                def schedule(coroutine):
                    task = asyncio.create_task(coroutine)
                    tasks.append(task)
                    return task

                calls = []

                def generate(_key, payload):
                    calls.append(payload)
                    return "**Логика**: 2+2=4. <tag>"

                from plugins.platforms.telegram.adapter import TelegramAdapter

                bridge = guest.GuestBridge(
                    SimpleNamespace(bot=bot, create_task=schedule),
                    12345,
                    "a" * 64,
                    store_module.GuestStore(home / "guest.sqlite3", clock=lambda: 1_000_000),
                    home=home,
                    formatter=lambda text: TelegramAdapter.format_message(None, text),
                    request=generate,
                    clock=lambda: 1_000_000,
                )
                for actor in (67890, 77777):
                    await bridge.handle(event(actor=actor, number=50))
                self.assertFalse(calls)
                self.assertFalse(request.answers)
                update = event()
                await bridge.handle(update)
                await asyncio.gather(*tasks)
                await bridge.handle(update)
                await asyncio.gather(*tasks)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["mode"], "guest")
                self.assertEqual(calls[0]["reply_context"], "Quoted fixture text")
                self.assertEqual(len(request.answers), 1)
                answer = request.answers[0]
                self.assertEqual(set(answer), {"guest_query_id", "result"})
                self.assertEqual(answer["guest_query_id"], "fixture-guest-1")
                result = answer["result"]
                if isinstance(result, str):
                    result = json.loads(result)
                self.assertEqual(result["type"], "article")
                content = result["input_message_content"]
                self.assertIn("*Логика*", content["message_text"])
                self.assertNotIn("**Логика**", content["message_text"])
                self.assertIn(r"2\+2\=4\.", content["message_text"])
                self.assertIn(r"<tag\>", content["message_text"])
                self.assertEqual(content["parse_mode"], "MarkdownV2")
                self.assertTrue(content["link_preview_options"]["is_disabled"])
                request.fail_send = True
                await bridge.handle(event(number=2))
                await asyncio.gather(*tasks)
                await bridge.handle(event(number=2))
                await asyncio.gather(*tasks)
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(request.answers), 2)

    async def test_owner_agent_sends_only_final_answer_through_real_sdk(self):
        module = plugin("council_owner_wire_fixture")
        guest = __import__(module.__name__ + ".guest", fromlist=["GuestBridge"])
        stores = __import__(module.__name__ + ".guest_store", fromlist=["GuestStore"])
        owner = __import__(module.__name__ + ".owner_guest", fromlist=["OwnerGuestBridge"])
        owner_stores = __import__(module.__name__ + ".owner_store", fromlist=["OwnerStore"])
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            policy = home / "plugin-data/council-secretary/guest.json"
            policy.parent.mkdir(parents=True)
            policy.write_text(
                json.dumps(
                    {"schema": 4, "enabled": True, "revision": "b" * 32, "owner_agent": True}
                )
            )
            policy.chmod(0o600)
            (home / "telegram-owner.json").write_text('{"telegram_id":12345}')
            request = Request()
            async with Bot(
                "12345:fixture-token", request=request, get_updates_request=request
            ) as bot:
                tasks = []

                def schedule(coro):
                    task = asyncio.create_task(coro)
                    tasks.append(task)
                    return task

                bridge = guest.GuestBridge(
                    SimpleNamespace(bot=bot, create_task=schedule),
                    12345,
                    "a" * 64,
                    stores.GuestStore(home / "queue.sqlite3"),
                    home=home,
                    clock=lambda: 1_000_000,
                )

                async def run(job, authorized, remember):
                    self.assertEqual(request.answers, [])
                    self.assertEqual(request.edits, [])
                    return "Ваша команда: Квай-Гон, Декстер, Лэндо, Падме."

                execute = AsyncMock(side_effect=run)
                bridge.owner_bridge = owner.OwnerGuestBridge(
                    bridge, owner_stores.OwnerStore(home / "owner.sqlite3"), execute=execute
                )
                await bridge.handle(event(actor=77777))
                execute.assert_not_called()
                await bridge.handle(event())
                await asyncio.gather(*tasks)
                self.assertEqual(len(request.answers), 1)
                self.assertEqual(request.edits, [])
                answer = request.answers[0]
                self.assertEqual(set(answer), {"guest_query_id", "result"})
                result = answer["result"]
                if isinstance(result, str):
                    result = json.loads(result)
                text = result["input_message_content"]["message_text"]
                self.assertIn("Падме", text)
                self.assertNotIn("Выполняю", text)

    async def test_actual_sdk_rejects_unknown_anonymous_bot_and_expired_callers(self):
        module = plugin("council_guest_identity_fixture")
        guest = __import__(module.__name__ + ".guest", fromlist=["incoming"])
        original = event().to_dict()
        for changes in (
            {"from": None},
            {"sender_chat": {"id": -100222, "type": "supergroup", "title": "Anonymous"}},
            {
                "from": {"id": 999, "is_bot": True, "first_name": "Bot"},
                "guest_bot_caller_user": {"id": 12345, "is_bot": False, "first_name": "Owner"},
            },
            {"date": 999_879},
        ):
            message = {**original["guest_message"], **changes}
            update = Update.de_json({**original, "guest_message": message}, None)
            with self.subTest(fields=set(changes)):
                self.assertIsNone(guest.incoming(update, 1_000_000))
