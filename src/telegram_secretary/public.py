"""Receive public bot DMs; commands never enter the private owner agent."""

import hashlib

from .delivery import Delivery, Reply
from .policy import sender_username

PUBLIC_CONNECTION = "public-bot-dm"


class PublicBridge(Delivery):
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
            sender_username=sender_username(sender),
            per_chat_limit=30,
        ):
            self.kick(PUBLIC_CONNECTION, chat)

    async def generate_reply(self, job):
        if job["text"].startswith("/"):
            command = job["text"].split()[0].split("@", 1)[0].lower()
            if command in {"/start", "/help"}:
                return Reply(self.greeting)
            if command == "/unsupported":
                return Reply(
                    "Пока я отвечаю только на текстовые сообщения. Напишите вопрос текстом."
                )
            return Reply(
                "Здесь можно общаться с помощником. Напишите вопрос или используйте /help."
            )
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
