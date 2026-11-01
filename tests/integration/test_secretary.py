"""Real PTB extension contract and isolated model/HTTP surface, with fixture data only."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram import Chat, Message, Update, User
from telegram.ext import Application, ApplicationHandlerStop, TypeHandler

sys.path.insert(0, "/opt/agent-council")
from secretary_worker.instructions import load_instructions
from secretary_worker.model import generate
from secretary_worker.service import Server, Service


class NativeHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_plugin_localizes_shutdown_and_unloads_without_changing_other_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "config.yaml").write_text(
                json.dumps({"plugins": {"enabled": ["council-secretary"]}})
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
from agent.i18n import t
from hermes_cli.plugins import PluginManager
keys = ('gateway.shutdown.notice_restart', 'gateway.shutdown.notice_shutdown')
before = {(lang, key): t(key, lang=lang) for lang in ('en', 'ru') for key in keys}
from agent.i18n import surface_catalog
catalogs = {lang: surface_catalog(lang).copy() for lang in ('en', 'ru')}
manager = PluginManager()
manager.discover_and_load()
for lang in ('en', 'ru'):
    assert t(keys[0], lang=lang).startswith('Перезапускаюсь.'), t(keys[0], lang=lang)
    assert t(keys[1], lang=lang).startswith('Ухожу офлайн.'), t(keys[1], lang=lang)
    assert 'попробую продолжить' in t(keys[1], lang=lang)
    after = surface_catalog(lang)
    assert {key for key in after if after[key] != catalogs[lang].get(key)} == set(keys)
manager.unload()
for (lang, key), text in before.items():
    assert t(key, lang=lang) == text
print('Native shutdown translations and unload: OK')
""",
                ],
                env={**os.environ, "HOME": temporary, "HERMES_HOME": temporary},
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-1000:] + result.stderr[-3000:])

    async def test_public_dm_is_intercepted_while_owner_dm_stays_with_native_handlers(self):
        root = Path("/opt/hermes/plugins/council-secretary")
        name = "council_public_fixture"
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        factories = []
        module.register(
            SimpleNamespace(register_telegram_handler=factories.append, register_locale=Mock())
        )
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "telegram-owner.json").write_text('{"telegram_id":12345}')
            application = Application.builder().token("12345:public-fixture-token").build()
            with patch.dict(
                os.environ,
                {
                    "HERMES_HOME": temporary,
                    "COUNCIL_SECRETARY_ENABLED": "true",
                    "COUNCIL_SECRETARY_PUBLIC_DMS": "true",
                    "COUNCIL_SECRETARY_KEY": "a" * 64,
                    "COUNCIL_SECRETARY_OWNER_ID": "12345",
                    "HERMES_TELEGRAM_ALLOWED_ID": "12345",
                    "COUNCIL_SECRETARY_CONTACT_IDS": "",
                },
            ):
                factories[0](application, None)
            handler = application.handlers[-1001][0]
            guest = Update(
                1,
                message=Message(
                    1,
                    datetime.now(UTC),
                    Chat(67890, "private"),
                    from_user=User(67890, "Guest", False),
                    text="/approve",
                ),
            )
            owner = Update(
                2,
                message=Message(
                    2,
                    datetime.now(UTC),
                    Chat(12345, "private"),
                    from_user=User(12345, "Owner", False),
                    text="/model",
                ),
            )
            self.assertTrue(handler.check_update(guest))
            self.assertFalse(handler.check_update(owner))
            public_module = sys.modules[name + ".public"]
            with patch.object(
                public_module.PublicBridge, "handle", new_callable=AsyncMock
            ) as handle:
                with self.assertRaises(ApplicationHandlerStop):
                    await handler.callback(guest, None)
                handle.assert_awaited_once_with(guest)
                handle.reset_mock()
                private_agent = AsyncMock()
                application.add_handler(TypeHandler(Update, private_agent), group=0)
                # The real SDK dispatcher runs offline; token initialization is not part of this fixture.
                application._initialized = True
                await application.process_update(guest)
                handle.assert_awaited_once_with(guest)
                private_agent.assert_not_called()
                await application.process_update(owner)
                private_agent.assert_awaited_once()

    async def test_public_menu_scopes_hide_management_commands_and_wait_for_native_startup(self):
        root = Path("/opt/hermes/plugins/council-secretary")
        name = "council_menu_fixture"
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        menus = __import__(name + ".menus", fromlist=["configure_menu"])
        bot = SimpleNamespace(set_my_commands=AsyncMock())
        with patch(
            "hermes_cli.commands_platforms.telegram_menu_commands",
            return_value=([("model", "Private model"), ("start", "Start")], 0),
        ):
            await menus.configure_menu(bot, 12345)
        calls = bot.set_my_commands.call_args_list
        self.assertEqual(calls[0].kwargs["scope"].to_dict(), {"type": "chat", "chat_id": 12345})
        self.assertIn("model", [command.command for command in calls[0].args[0]])
        self.assertEqual(len(calls), 4)
        for call in calls[1:]:
            self.assertEqual([command.command for command in call.args[0]], ["start", "help"])
        finished = asyncio.Event()
        housekeeping = asyncio.create_task(finished.wait())
        adapter = SimpleNamespace(_post_connect_task=housekeeping)
        application = SimpleNamespace(running=True, bot=bot)
        with patch.object(menus, "configure_menu", new_callable=AsyncMock) as configure:
            task = asyncio.create_task(menus.configure_after_connect(application, adapter, 12345))
            await asyncio.sleep(0)
            configure.assert_not_called()
            finished.set()
            await task
            configure.assert_awaited_once_with(bot, 12345)

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
        module.register(
            SimpleNamespace(register_telegram_handler=factories.append, register_locale=Mock())
        )
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
            date=datetime.now(UTC),
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
            queue.unlink()
            invalid_contacts = Application.builder().token("12345:fixture-token").build()
            with patch.dict(
                os.environ,
                {
                    "COUNCIL_SECRETARY_ENABLED": "true",
                    "COUNCIL_SECRETARY_KEY": "a" * 64,
                    "COUNCIL_SECRETARY_OWNER_ID": "12345",
                    "COUNCIL_SECRETARY_CONTACT_IDS": "@not-a-numeric-id",
                    "HERMES_TELEGRAM_ALLOWED_ID": "12345",
                    "HERMES_HOME": temporary,
                },
            ):
                factories[0](invalid_contacts, None)
            with self.assertRaises(ApplicationHandlerStop):
                await invalid_contacts.handlers[-1000][0].callback(business, None)


class WorkerModelTests(unittest.TestCase):
    def test_bundled_plugin_can_be_enabled_through_the_real_hermes_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "config.yaml").write_text('{"security":{"allow_lazy_installs":false}}')
            result = subprocess.run(
                ["/opt/hermes/.venv/bin/hermes", "plugins", "enable", "council-secretary"],
                env={**os.environ, "HERMES_HOME": temporary},
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(
                result.returncode, 0, "Pinned Hermes CLI must enable the bundled extension offline"
            )
            from hermes_cli.config import read_user_config_raw

            config = read_user_config_raw(Path(temporary) / "config.yaml")
            self.assertIn("council-secretary", config["plugins"]["enabled"])

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
                for response in (
                    {"content": "Final answer"},
                    SimpleNamespace(content="Final answer"),
                    "Final answer",
                ):
                    with patch("agent.auxiliary_client.call_llm", return_value=response):
                        self.assertEqual(
                            generate([{"role": "user", "content": "hello"}]), "Final answer"
                        )

    def test_real_http_requires_machine_auth_and_refuses_caller_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            key = "a" * 64
            instructions = load_instructions("/opt/agent-council/instructions/secretary")
            service = Service(
                temporary,
                key,
                skills=instructions.skills,
                prompt=instructions.policy,
                generate=lambda _messages: "Fixture reply",
            )
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
