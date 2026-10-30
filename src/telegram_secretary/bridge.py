"""Verify Telegram connection ownership, then call only the private secretary API."""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path

from .store import Store

logger = logging.getLogger(__name__)
BUSINESS_FIELDS = (
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request_service(key, path, payload):
    request = urllib.request.Request(
        "http://secretary:9120" + path,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    # Ignore caller proxy configuration and never forward this key through a redirect.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=55) as response:
        data = response.read(20000)
    return json.loads(data)


def acknowledge_reply(key, identifier):
    request_service(key, "/ack", {"request_id": identifier})


def request_reply(key, payload):
    value = request_service(key, "/reply", payload)
    text = value.get("reply") if isinstance(value, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError("Invalid secretary response.")
    return text


class Bridge:
    def __init__(
        self,
        application,
        owner,
        key,
        store,
        *,
        request=request_reply,
        acknowledge=acknowledge_reply,
        clock=time.time,
    ):
        self.application, self.owner, self.key, self.store = application, owner, key, store
        self.request, self.clock = request, clock
        self.acknowledge = acknowledge
        self.tasks = {}
        self.capacity = asyncio.Semaphore(2)

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
        if self.store.submit(identity, identifier, chat, message.message_id, text, created):
            self.kick(identifier, chat)

    def kick(self, connection, chat):
        identity = (connection, chat)
        previous = self.tasks.get(identity)
        if previous is not None and not previous.done():
            return
        self.tasks[identity] = self.application.create_task(self.drain(connection, chat))

    async def recover(self):
        for _ in range(300):
            if self.application.running:
                for connection, chat in self.store.chats():
                    self.kick(connection, chat)
                return
            await asyncio.sleep(0.2)

    async def drain(self, connection, chat):
        while job := self.store.claim(connection, chat):
            sent = False
            try:
                async with self.capacity:
                    if not await self.refresh(connection):
                        self.store.finish(job["id"], "cancelled")
                        continue
                    payload = {
                        "request_id": job["id"],
                        "conversation": hashlib.sha256(f"{connection}:{chat}".encode()).hexdigest(),
                        "text": job["text"],
                    }
                    reply = await asyncio.to_thread(self.request, self.key, payload)
                    # Rights can be revoked, or a human may take over while the model is running.
                    if not await self.refresh(connection) or not self.store.reserve(job["id"]):
                        self.store.finish(job["id"], "cancelled")
                        continue
                    from telegram import ReplyParameters

                    sent = True  # Delivery is ambiguous if the HTTP response is lost; do not retry.
                    await self.application.bot.send_message(
                        chat_id=chat,
                        business_connection_id=connection,
                        text=reply,
                        parse_mode=None,
                        reply_parameters=ReplyParameters(
                            message_id=job["message"], allow_sending_without_reply=False
                        ),
                    )
                    self.store.finish(job["id"], "sent")
                    try:
                        await asyncio.to_thread(self.acknowledge, self.key, job["id"])
                    except Exception:
                        logger.warning(
                            "Secretary history acknowledgement failed; delivery was confirmed."
                        )
            except asyncio.CancelledError:
                if sent:
                    self.store.finish(job["id"], "uncertain")
                raise
            except Exception:
                self.store.finish(job["id"], "uncertain" if sent else "failed")
                logger.warning("Secretary request failed; private provider/update details omitted.")
        self.tasks.pop((connection, chat), None)


def attach(application, _adapter):
    from telegram import Update
    from telegram.ext import ApplicationHandlerStop, TypeHandler

    if application.bot_data.get("council-secretary-registered"):
        return

    owner = os.environ.get("COUNCIL_SECRETARY_OWNER_ID", "")
    key = os.environ.get("COUNCIL_SECRETARY_KEY", "")
    enabled = os.environ.get("COUNCIL_SECRETARY_ENABLED") == "true" and bool(
        re.fullmatch(r"[1-9][0-9]{0,18}", owner) and re.fullmatch(r"[a-f0-9]{64}", key)
    )
    if enabled:
        try:
            pin = Path(os.environ["HERMES_HOME"]) / "telegram-owner.json"
            pinned = json.loads(pin.read_text())["telegram_id"] if not pin.is_symlink() else None
            explicit = os.environ.get("HERMES_TELEGRAM_ALLOWED_ID", "")
            enabled = (
                type(pinned) is int and pinned == int(owner) and (not explicit or explicit == owner)
            )
        except (OSError, KeyError, ValueError):
            enabled = False
    bridge = None
    if enabled:
        try:
            store = Store(
                Path(os.environ["HERMES_HOME"]) / "plugin-data/council-secretary/queue.sqlite3"
            )
            bridge = Bridge(application, int(owner), key, store)
            asyncio.get_running_loop().create_task(bridge.recover())
        except Exception:
            bridge = None
            logger.error("Secretary state unavailable; business updates remain closed.")

    class BusinessHandler(TypeHandler):
        def check_update(self, update):
            # Admission sees only business updates, leaving normal owner-message retries unchanged.
            return isinstance(update, Update) and any(
                getattr(update, field, None) is not None for field in BUSINESS_FIELDS
            )

    async def dispatch(update, _context):
        try:
            if bridge is not None:
                await bridge.handle(update)
        except Exception:
            logger.warning("Secretary update rejected; private update details omitted.")
        finally:
            raise ApplicationHandlerStop

    application.add_handler(BusinessHandler(Update, dispatch), group=-1000)
    application.bot_data["council-secretary-registered"] = True
