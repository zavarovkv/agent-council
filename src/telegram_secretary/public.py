"""Serve untrusted private bot messages without admitting them to the owner's agent."""

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path

from .bridge import BUSINESS_FIELDS, Bridge, contact_ids, normalize_username
from .store import Store

logger = logging.getLogger(__name__)
PUBLIC_CONNECTION = "public-bot-dm"


def pinned_owner(home):
    try:
        pin = Path(home) / "telegram-owner.json"
        owner = json.loads(pin.read_text())["telegram_id"] if not pin.is_symlink() else None
        expected = os.environ.get("COUNCIL_SECRETARY_OWNER_ID", "")
        explicit = os.environ.get("HERMES_TELEGRAM_ALLOWED_ID", "")
        if (
            type(owner) is int
            and 0 < owner < 2**63
            and (not expected or expected == str(owner))
            and (not explicit or explicit == str(owner))
        ):
            return owner
    except (TypeError, OSError, KeyError, ValueError):
        pass
    return None


class PublicBridge(Bridge):
    def __init__(self, *args, greeting, **kwargs):
        super().__init__(*args, **kwargs)
        self.greeting = greeting
        self.store.connection(PUBLIC_CONNECTION, True)

    async def refresh(self, identifier):
        return identifier == PUBLIC_CONNECTION

    async def handle(self, update):
        edited = getattr(update, "edited_message", None)
        message = edited or getattr(update, "message", None)
        if message is None or getattr(message.chat, "type", None) != "private":
            return
        sender = getattr(message, "from_user", None)
        chat = message.chat.id
        if (
            sender is None
            or sender.id != chat
            or sender.id == self.owner
            or getattr(sender, "is_bot", False)
            or getattr(message, "via_bot", None)
        ):
            return
        if edited is not None:
            self.store.cancel(PUBLIC_CONNECTION, chat, [message.message_id])
        created = message.date.timestamp()
        if not self.clock() - 23 * 3600 < created <= self.clock() + 60:
            return
        text = getattr(message, "text", None)
        if text is None:
            text = "/unsupported"
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            return
        identity = hashlib.sha256(
            f"{PUBLIC_CONNECTION}:{chat}:{message.message_id}:{update.update_id}".encode()
        ).hexdigest()
        if self.store.submit(
            identity,
            PUBLIC_CONNECTION,
            chat,
            message.message_id,
            text,
            created,
            sender_username=normalize_username(getattr(sender, "username", None) or ""),
            per_chat_limit=30,
        ):
            self.kick(PUBLIC_CONNECTION, chat)

    async def generate_reply(self, job):
        if job["text"].startswith("/"):
            command = job["text"].split()[0].split("@", 1)[0].lower()
            if command in {"/start", "/help"}:
                return self.greeting
            if command == "/unsupported":
                return "Пока я отвечаю только на текстовые сообщения. Напишите вопрос текстом."
            return "Здесь можно общаться с помощником. Напишите вопрос или используйте /help."
        return await super().generate_reply(job)

    async def send_reply(self, job, reply):
        from telegram import ReplyParameters

        await self.application.bot.send_message(
            chat_id=job["chat"],
            text=reply,
            parse_mode=None,
            reply_parameters=ReplyParameters(
                message_id=job["message"], allow_sending_without_reply=False
            ),
        )


def attach_public(application, adapter, *, enabled, key, capacity):
    from telegram import Update
    from telegram.ext import ApplicationHandlerStop, TypeHandler

    home = os.environ.get("HERMES_HOME")
    owner = pinned_owner(home)
    bridge = None
    if enabled and owner is not None and os.environ.get("COUNCIL_SECRETARY_PUBLIC_DMS") == "true":
        try:
            bridge = PublicBridge(
                application,
                owner,
                key,
                Store(Path(home) / "plugin-data/council-secretary/public-queue.sqlite3"),
                contacts=contact_ids(os.environ.get("COUNCIL_SECRETARY_CONTACT_IDS", "")),
                spouse_username=os.environ.get("COUNCIL_SECRETARY_SPOUSE_USERNAME", ""),
                capacity=capacity,
                greeting=(
                    f"Я {os.environ.get('COUNCIL_SECRETARY_ASSISTANT_NAME', 'Ассистент')}, "
                    f"цифровой помощник {os.environ.get('COUNCIL_SECRETARY_OWNER_NAME', 'владельца аккаунта')}. "
                    "Помогу с общими вопросами и уточню ваш запрос. "
                    "Личные договорённости подтверждает сам владелец. Чем могу помочь?"
                ),
            )
            asyncio.get_running_loop().create_task(bridge.recover())
            if adapter is not None:
                from .menus import configure_after_connect

                asyncio.get_running_loop().create_task(
                    configure_after_connect(application, adapter, owner)
                )
        except Exception:
            bridge = None
            logger.error("Public secretary unavailable; foreign private updates remain closed.")

    class PublicHandler(TypeHandler):
        def check_update(self, update):
            if not isinstance(update, Update) or any(
                getattr(update, field, None) is not None for field in BUSINESS_FIELDS
            ):
                return False
            callback = getattr(update, "callback_query", None)
            inline = getattr(update, "inline_query", None)
            if callback is not None or inline is not None:
                return (callback or inline).from_user.id != owner
            message = getattr(update, "message", None) or getattr(update, "edited_message", None)
            return (
                message is not None
                and getattr(message.chat, "type", None) == "private"
                and getattr(getattr(message, "from_user", None), "id", None) != owner
            )

    async def dispatch(update, _context):
        try:
            if getattr(update, "callback_query", None) is not None:
                await update.callback_query.answer("Используйте /help для общения с помощником.")
            elif getattr(update, "inline_query", None) is not None:
                await update.inline_query.answer([], cache_time=0, is_personal=True)
            elif bridge is not None:
                await bridge.handle(update)
        except Exception:
            logger.warning("Public secretary update failed; private details omitted.")
        finally:
            raise ApplicationHandlerStop

    application.add_handler(PublicHandler(Update, dispatch), group=-1001)
