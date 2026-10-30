"""Receive only verified owner business connections; always send through that connection."""

import hashlib

from .delivery import Delivery
from .policy import sender_username

BUSINESS_FIELDS = (
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
)


class Bridge(Delivery):
    def verified(self, connection):
        rights = getattr(connection, "rights", None)
        return (
            getattr(getattr(connection, "user", None), "id", None) == self.owner
            and getattr(connection, "is_enabled", False) is True
            and getattr(rights, "can_reply", False) is True
        )

    async def refresh(self, identifier):
        if not isinstance(identifier, str) or not 0 < len(identifier) <= 256:
            return False
        connection = await self.application.bot.get_business_connection(identifier)
        enabled = self.verified(connection)
        self.store.connection(identifier, enabled)
        return enabled

    async def handle(self, update):
        connection = getattr(update, "business_connection", None)
        if connection is not None:
            self.store.connection(connection.id, self.verified(connection))
            return
        deleted = getattr(update, "deleted_business_messages", None)
        if deleted is not None:
            self.store.cancel(deleted.business_connection_id, deleted.chat.id, deleted.message_ids)
            return
        edited = getattr(update, "edited_business_message", None)
        message = edited or getattr(update, "business_message", None)
        if message is None or getattr(message.chat, "type", None) != "private":
            return
        identifier, chat = message.business_connection_id, message.chat.id
        if edited is not None:
            self.store.cancel(identifier, chat, [message.message_id])
        sender = getattr(message, "from_user", None)
        if getattr(message, "sender_business_bot", None) or getattr(
            message, "via_business_bot", None
        ):
            return
        if getattr(sender, "id", None) == self.owner:
            self.store.pause(identifier, chat)
            return
        if (
            sender is None
            or getattr(sender, "is_bot", False)
            or getattr(message, "via_business_bot", None)
            or getattr(message, "via_bot", None)
        ):
            return
        text = getattr(message, "text", None)
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            return
        created = message.date.timestamp()
        if not self.clock() - 23 * 3600 < created <= self.clock() + 60:
            return
        if not await self.refresh(identifier):
            return
        if not self.store.allowed(identifier, chat):
            return
        identity = hashlib.sha256(
            f"{identifier}:{chat}:{message.message_id}:{update.update_id}".encode()
        ).hexdigest()
        if self.store.submit(
            identity,
            identifier,
            chat,
            message.message_id,
            text,
            created,
            sender_username=sender_username(sender),
        ):
            self.kick(identifier, chat)

    async def send_reply(self, job, reply):
        from telegram import ReplyParameters

        await self.application.bot.send_message(
            chat_id=job["chat"],
            business_connection_id=job["connection"],
            text=reply,
            parse_mode=None,
            reply_parameters=ReplyParameters(
                message_id=job["message"], allow_sending_without_reply=False
            ),
        )
