import asyncio
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as Object
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from telegram_secretary.public import PUBLIC_CONNECTION, PublicBridge
from telegram_secretary.store import Store


class PublicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tasks, self.requests = [], []
        self.bot = Object(send_message=AsyncMock())

        def schedule(coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task

        def request(_key, payload):
            self.requests.append(payload)
            return "Fixture assistant reply"

        self.store = Store(Path(directory.name) / "public-queue.sqlite3", clock=lambda: 1_000_000)
        self.bridge = PublicBridge(
            Object(bot=self.bot, create_task=schedule),
            12345,
            "a" * 64,
            self.store,
            greeting="Я цифровой помощник владельца. Чем могу помочь?",
            spouse_username="@Lilpersic",
            request=request,
            acknowledge=lambda *_: None,
            clock=lambda: 1_000_000,
        )
        telegram = types.ModuleType("telegram")
        telegram.ReplyParameters = lambda **values: Object(**values)
        patched = patch.dict(sys.modules, {"telegram": telegram})
        patched.start()
        self.addCleanup(patched.stop)

    def update(self, text="Привет", *, chat=67890, username="guest", number=1):
        return Object(
            update_id=number,
            edited_message=None,
            message=Object(
                chat=Object(id=chat, type="private"),
                from_user=Object(id=chat, username=username, is_bot=False),
                date=datetime.fromtimestamp(1_000_000, timezone.utc),
                message_id=number,
                text=text,
            ),
        )

    async def settle(self):
        await asyncio.gather(*self.tasks)

    async def test_foreign_dm_calls_only_tool_free_worker_and_replies_as_bot(self):
        update = self.update("/model")
        await self.bridge.handle(update)
        await self.settle()
        self.assertFalse(self.requests)
        await self.bridge.handle(update)
        await self.settle()
        self.assertEqual(self.bot.send_message.call_count, 1)
        await self.bridge.handle(self.update("Обычный вопрос", number=2))
        await self.settle()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["audience"], "new-users")
        sent = self.bot.send_message.call_args.kwargs
        self.assertNotIn("business_connection_id", sent)
        self.assertEqual(sent["chat_id"], 67890)
        self.assertEqual(sent["reply_parameters"].message_id, 2)

    async def test_spouse_style_uses_sender_metadata_and_owner_dm_is_never_claimed(self):
        await self.bridge.handle(self.update(username="LiLpersic"))
        await self.settle()
        self.assertEqual(self.requests[0]["audience"], "spouse")
        await self.bridge.handle(self.update(chat=12345, number=2))
        await self.settle()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.bot.send_message.call_count, 1)

    async def test_start_help_and_media_use_local_text_and_never_a_private_command(self):
        for number, text in enumerate(("/start", "/help", None), 1):
            await self.bridge.handle(self.update(text, number=number))
        await self.settle()
        self.assertFalse(self.requests)
        self.assertEqual(self.bot.send_message.call_count, 3)
        self.assertIn("текст", self.bot.send_message.call_args.kwargs["text"])

    async def test_ambiguous_public_delivery_is_not_replayed_or_sent_through_owner_account(self):
        self.bot.send_message.side_effect = TimeoutError("ambiguous delivery")
        update = self.update()
        await self.bridge.handle(update)
        await self.settle()
        await self.bridge.handle(update)
        await self.settle()
        self.assertEqual(self.bot.send_message.call_count, 1)
        self.assertFalse(Store(self.store.path, clock=lambda: 1_000_000).chats())

    async def test_one_sender_cannot_fill_the_entire_public_queue(self):
        self.bridge.kick = lambda *_: None
        for number in range(1, 41):
            await self.bridge.handle(self.update(number=number))
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 30)
        await self.bridge.handle(self.update(chat=77777, number=100))
        self.assertIsNotNone(self.store.claim(PUBLIC_CONNECTION, 77777))
