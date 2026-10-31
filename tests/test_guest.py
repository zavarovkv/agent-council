import asyncio
import hashlib
import json
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as Object
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from secretary_worker.instructions import guest_instructions
from secretary_worker.service import Service
from telegram_secretary.guest import GuestBridge, incoming, is_guest
from telegram_secretary.guest_policy import RELATIVE_POLICY, load_state
from telegram_secretary.guest_store import GuestStore

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 32


def policy(home, enabled=True, revision=REVISION):
    path = home / RELATIVE_POLICY
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "enabled": enabled, "revision": revision}))
    path.chmod(0o600)
    return path


def update(*, actor=67890, chat=-100123, number=1, text="@bot Question", query=None, now=1_000_000):
    return Object(
        guest_message=Object(
            guest_query_id=query or f"guest-query-{number}",
            message_id=number,
            message_thread_id=None,
            date=datetime.fromtimestamp(now, UTC),
            chat=Object(id=chat, type="supergroup" if chat < 0 else "private"),
            from_user=Object(id=actor, is_bot=False, username="guest"),
            text=text,
            reply_to_message=None,
            sender_chat=None,
            via_bot=None,
        )
    )


class GuestPolicyTests(unittest.TestCase):
    def test_missing_corrupt_public_and_symlinked_policies_stay_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            self.assertFalse(load_state(home).enabled)
            path = policy(home)
            self.assertTrue(load_state(home).enabled)
            path.chmod(0o644)
            self.assertTrue(load_state(home).error)
            path.chmod(0o600)
            path.write_text('{"schema":1,"enabled":true}')
            self.assertTrue(load_state(home).error)
            path.unlink()
            path.symlink_to(home / "missing")
            self.assertTrue(load_state(home).error)

    def test_input_uses_verified_sender_and_only_one_bounded_reply(self):
        event = update(actor=12345)
        event.guest_message.reply_to_message = Object(
            text="quoted " * 500, reply_to_message=Object(text="NEVER FORWARD NESTED HISTORY")
        )
        job = incoming(event, 1_000_000)
        self.assertEqual(job["actor"], 12345)
        self.assertEqual(len(job["reply_context"]), 2000)
        self.assertNotIn("NESTED", json.dumps(job))
        for change in ("bot", "anonymous", "unknown", "stale"):
            event = update()
            if change == "bot":
                event.guest_message.from_user.is_bot = True
                event.guest_message.guest_bot_caller_user = Object(id=12345)
            elif change == "anonymous":
                event.guest_message.sender_chat = Object(id=-100123)
            elif change == "unknown":
                event.guest_message.from_user = None
            else:
                event.guest_message.date = datetime.fromtimestamp(999_879, UTC)
            with self.subTest(change=change):
                self.assertIsNone(incoming(event, 1_000_000))
        self.assertTrue(is_guest(Object(api_kwargs={"guest_message": {}})))
        self.assertTrue(is_guest(Object(message=Object(guest_query_id="unexpected-field"))))


class GuestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.now = [1_000_000]
        self.tasks, self.requests = [], []
        self.bot = Object(
            get_me=AsyncMock(return_value=Object(supports_guest_queries=True)),
            answer_guest_query=AsyncMock(),
            send_message=AsyncMock(),
        )
        telegram = types.ModuleType("telegram")
        for name in ("InlineQueryResultArticle", "InputTextMessageContent", "LinkPreviewOptions"):
            setattr(telegram, name, lambda *args, **kwargs: Object(args=args, **kwargs))
        patched = patch.dict(sys.modules, {"telegram": telegram})
        patched.start()
        self.addCleanup(patched.stop)
        policy(self.home)

        def schedule(coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task

        def request(_key, payload):
            self.requests.append(payload)
            return "Public guest answer"

        self.store = GuestStore(self.home / "guest-queue.sqlite3", clock=lambda: self.now[0])
        self.bridge = GuestBridge(
            Object(bot=self.bot, create_task=schedule),
            12345,
            "a" * 64,
            self.store,
            home=self.home,
            request=request,
            clock=lambda: self.now[0],
        )

    async def settle(self):
        await asyncio.gather(*self.tasks)

    async def test_owner_spouse_and_foreign_calls_all_use_public_guest_mode(self):
        conversations = []
        for number, actor in enumerate((12345, 67890, 99999), 1):
            event = update(actor=actor, chat=12345, number=number)
            event.guest_message.from_user.username = "Lilpersic"
            await self.bridge.handle(event)
            await self.settle()
            payload = self.requests[-1]
            self.assertEqual(
                (payload["route"], payload["mode"], payload["audience"]),
                ("public", "guest", "new-users"),
            )
            conversations.append(payload["conversation"])
        self.assertEqual(len(set(conversations)), 3)
        self.assertEqual(self.bot.answer_guest_query.call_count, 3)
        self.bot.send_message.assert_not_called()
        call = self.bot.answer_guest_query.call_args.kwargs
        self.assertEqual(call["guest_query_id"], "guest-query-3")
        self.assertFalse(hasattr(call["result"], "chat_id"))
        with self.store.database() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM jobs WHERE text!='' OR query_id!=''").fetchone()[
                    0
                ],
                0,
            )

    async def test_duplicate_and_ambiguous_sends_never_repeat_generation_or_delivery(self):
        self.bot.answer_guest_query.side_effect = TimeoutError("private transport details")
        event = update()
        for _ in range(2):
            await self.bridge.handle(event)
            await self.settle()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.bot.answer_guest_query.call_count, 1)
        self.bot.send_message.assert_not_called()
        reopened = GuestStore(self.store.path, clock=lambda: self.now[0])
        self.assertFalse(reopened.chats())
        with reopened.database() as db:
            self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0], "uncertain")

    async def test_policy_epoch_and_expiry_cancel_waiting_work_before_provider_spend(self):
        self.bridge.capacity = asyncio.Semaphore(0)
        await self.bridge.handle(update())
        await asyncio.sleep(0)
        policy(self.home, False, "b" * 32)
        policy(self.home, True, "c" * 32)
        self.bridge.capacity.release()
        await self.settle()
        self.assertFalse(self.requests)
        self.bot.answer_guest_query.assert_not_called()
        self.bridge.capacity = asyncio.Semaphore(0)
        await self.bridge.handle(update(number=2))
        await asyncio.sleep(0)
        self.now[0] += 121
        self.store.prune("c" * 32)
        self.bridge.capacity.release()
        await self.settle()
        self.assertFalse(self.requests)
        with self.store.database() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM jobs WHERE text!=''").fetchone()[0], 0
            )

    async def test_disabled_or_unsupported_mode_does_not_generate(self):
        policy(self.home, False)
        await self.bridge.handle(update())
        await self.settle()
        self.assertFalse(self.requests)
        policy(self.home)
        self.bot.get_me.return_value = Object(supports_guest_queries=False)
        await self.bridge.handle(update(number=2))
        await self.settle()
        self.assertFalse(self.requests)
        self.bot.answer_guest_query.assert_not_called()

    async def test_revocation_during_generation_discards_the_reply(self):
        def generate(_key, payload):
            policy(self.home, False, "b" * 32)
            return "Must not be sent after revocation"

        self.bridge.request = generate
        await self.bridge.handle(update())
        await self.settle()
        self.bot.answer_guest_query.assert_not_called()
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0], "cancelled")

    async def test_pending_limit_is_global_and_actor_limit_spans_chats(self):
        first = incoming(update(), self.now[0])
        self.assertTrue(self.store.submit(first, REVISION))
        self.assertFalse(
            self.store.submit(incoming(update(chat=-999, number=2), self.now[0]), REVISION)
        )
        for number in range(3, 18):
            self.assertTrue(
                self.store.submit(
                    incoming(
                        update(actor=90000 + number, chat=-100000 - number, number=number),
                        self.now[0],
                    ),
                    REVISION,
                )
            )
        self.assertFalse(
            self.store.submit(incoming(update(actor=99999, number=18), self.now[0]), REVISION)
        )

    async def test_provider_failure_has_one_local_answer_and_media_never_calls_model(self):
        def unavailable(*_args):
            raise RuntimeError("private provider response")

        self.bridge.request = unavailable
        await self.bridge.handle(update())
        await self.settle()
        self.assertEqual(self.bot.answer_guest_query.call_count, 1)
        text = self.bot.answer_guest_query.call_args.kwargs["result"].input_message_content.args[0]
        self.assertNotIn("private", text)
        await self.bridge.handle(update(number=2, text=None))
        await self.settle()
        self.assertEqual(self.bot.answer_guest_query.call_count, 2)


class GuestWorkerTests(unittest.TestCase):
    def test_guest_and_public_dm_share_spend_budget_but_business_is_reserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = Service(
                temporary,
                "a" * 64,
                skills={name: name for name in ("spouse", "contacts", "new-users")},
                prompt="Private policy",
                guest_prompt="Public guest policy",
                generate=lambda _: "Answer",
                clock=lambda: 1_000_000,
            )
            for number in range(40):
                service.reply(
                    {
                        "request_id": hashlib.sha256(str(number).encode()).hexdigest(),
                        "conversation": hashlib.sha256(str(number).encode()).hexdigest(),
                        "text": "Question",
                        "route": "public",
                        "mode": "guest" if number % 2 else "chat",
                    }
                )
            base = {"request_id": "e" * 64, "conversation": "f" * 64, "text": "Question"}
            for mode in ("chat", "guest"):
                with self.assertRaises(BlockingIOError):
                    service.reply({**base, "mode": mode, "route": "public"})
            self.assertEqual(service.reply({**base, "route": "business"}), "Answer")

    def test_no_private_instructions_history_or_plaintext_storage_reach_guest_mode(self):
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            service = Service(
                temporary,
                "a" * 64,
                skills={
                    name: "PRIVATE SKILL " + name for name in ("spouse", "contacts", "new-users")
                },
                prompt="PRIVATE POLICY",
                guest_prompt=guest_instructions(ROOT / "instructions/guest"),
                generate=lambda messages: calls.append(messages) or "Guest output marker",
                clock=lambda: 1_000_000,
            )
            payload = {
                "request_id": "a" * 64,
                "conversation": "b" * 64,
                "text": "Invocation marker",
                "reply_context": "Quoted marker",
                "route": "public",
                "mode": "guest",
            }
            service.reply(payload)
            service.acknowledge({"request_id": payload["request_id"]})
            with self.assertRaises(BlockingIOError):
                service.reply(payload)
            service.reply(
                {
                    **payload,
                    "request_id": "c" * 64,
                    "text": "Second invocation",
                    "reply_context": "",
                }
            )
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(calls[-1]), 2)
            self.assertNotIn("PRIVATE", json.dumps(calls))
            self.assertNotIn("Invocation marker", json.dumps(calls[-1]))
            with service.history.database() as db:
                rows = db.execute("SELECT question,answer FROM turns").fetchall()
            self.assertTrue(
                all(len(question) == 64 and answer is None for question, answer in rows)
            )
            for change in ({"route": "business"}, {"audience": "spouse"}, {"mode": "chat"}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    service.reply(
                        {
                            **payload,
                            **change,
                            "request_id": hashlib.sha256(str(change).encode()).hexdigest(),
                        }
                    )

    def test_rate_limits_and_crash_recovery_are_durable(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = GuestStore(Path(temporary).resolve() / "queue.sqlite3", clock=lambda: 1_000_000)
            for number in range(1, 12):
                job = incoming(update(number=number), 1_000_000)
                accepted = store.submit(job, REVISION)
                if number <= 10:
                    self.assertTrue(accepted)
                    store.claim(REVISION, job["chat"])
                    store.finish(job["id"], "failed")
                else:
                    self.assertFalse(accepted)
            fresh = incoming(update(actor=77777, number=50), 1_000_000)
            self.assertTrue(store.submit(fresh, REVISION))
            store.claim(REVISION, fresh["chat"])
            reopened = GuestStore(store.path, clock=lambda: 1_000_000)
            self.assertFalse(reopened.chats())
            with reopened.database() as db:
                self.assertEqual(
                    db.execute(
                        "SELECT status,text FROM jobs WHERE id=?", (fresh["id"],)
                    ).fetchone()[0],
                    "failed",
                )
