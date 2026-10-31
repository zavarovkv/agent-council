"""Guest queries always use public, stateless, tool-free inference and their reply token."""

import asyncio
import hashlib
import logging

from .delivery import Delivery, Reply
from .guest_policy import load_state
from .policy import pinned_owner

logger = logging.getLogger(__name__)
NOTICES = {
    "unsupported": "В гостевом режиме пока работаю с текстом. Напишите вопрос текстом.",
    "too_long": "Сократите вопрос до 4000 символов и вызовите меня снова.",
    "busy": "Предыдущее задание ещё выполняется. Его статус обновляется в исходном сообщении; повторите новый запрос после завершения.",
}


def is_guest(update):
    return (
        getattr(update, "guest_message", None) is not None
        or "guest_message" in (getattr(update, "api_kwargs", None) or {})
        or any(
            getattr(getattr(update, name, None), "guest_query_id", None)
            for name in ("message", "edited_message", "business_message", "edited_business_message")
        )
    )


def text_only(value):
    return "".join(char for char in value if ord(char) >= 32 or char in "\n\t")


def incoming(update, now):
    message = getattr(update, "guest_message", None)
    if message is None:
        return None
    sender = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    actor = getattr(sender, "id", None)
    query = getattr(message, "guest_query_id", None)
    chat_id = getattr(chat, "id", None)
    number = getattr(message, "message_id", None)
    thread = getattr(message, "message_thread_id", None) or 0
    if (
        type(actor) is not int
        or not 0 < actor < 2**52
        or getattr(sender, "is_bot", True)
        or getattr(message, "sender_chat", None) is not None
        or getattr(message, "via_bot", None) is not None
        or type(chat_id) is not int
        or not 0 < abs(chat_id) < 2**52
        or getattr(chat, "type", None) not in {"private", "group", "supergroup"}
        or type(number) is not int
        or not 0 < number < 2**32
        or type(thread) is not int
        or not 0 <= thread < 2**32
        or not isinstance(query, str)
        or not 0 < len(query) <= 256
        or any(ord(char) < 32 for char in query)
    ):
        return None
    try:
        created = message.date.timestamp()
    except (AttributeError, ValueError, OverflowError):
        return None
    if not now - 120 < created <= now + 60:
        return None
    text = getattr(message, "text", None)
    notice = "unsupported" if not isinstance(text, str) or not text.strip() else ""
    if not notice and len(text) > 4000:
        notice = "too_long"
    text = "" if notice else text_only(text)
    if not text.strip():
        notice = notice or "unsupported"
    reply = getattr(message, "reply_to_message", None)
    quote = getattr(getattr(message, "quote", None), "text", None)
    if not isinstance(quote, str):
        quote = getattr(reply, "text", None) or getattr(reply, "caption", None) or ""
    context = text_only(quote[:2000]) if isinstance(quote, str) and not notice else ""
    return {
        "id": hashlib.sha256(("guest:" + query).encode()).hexdigest(),
        "query_id": query,
        "actor": actor,
        "chat": chat_id,
        "message": number,
        "thread": thread,
        "text": text,
        "reply_context": context,
        "notice": notice,
        "created": created,
        "expires": min(created + 120, now + 120),
    }


class GuestBridge(Delivery):
    route = "public"

    def __init__(self, *args, home, formatter=None, owner_bridge=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.home = home
        self.formatter = formatter
        self.owner_bridge = owner_bridge
        self.capability_lock = asyncio.Lock()
        self.capable, self.checked_until = False, 0

    async def housekeeping(self):
        for _ in range(300):
            if self.application.running:
                break
            await asyncio.sleep(0.2)
        while self.application.running:
            state = load_state(self.home)
            self.store.prune(state.revision if state.enabled else None)
            await asyncio.sleep(30)

    async def refresh(self, identifier):
        state = load_state(self.home)
        if (
            not state.enabled
            or state.revision != identifier
            or pinned_owner(self.home, expected=str(self.owner)) != self.owner
        ):
            return False
        if self.clock() < self.checked_until:
            return self.capable
        async with self.capability_lock:
            if self.clock() < self.checked_until:
                return self.capable
            try:
                user = await self.application.bot.get_me()
                self.capable = getattr(user, "supports_guest_queries", False) is True
            except Exception:
                self.capable = False
            self.checked_until = self.clock() + (300 if self.capable else 5)
        return self.capable

    async def handle(self, update):
        state = load_state(self.home)
        if not state.enabled:
            return
        job = incoming(update, self.clock())
        if state.owner_agent:
            if job is not None and self.eligible(job) and self.owner_bridge is not None:
                await self.owner_bridge.handle(job, state.revision)
            return
        if job is not None and self.eligible(job) and self.store.submit(job, state.revision):
            self.kick(state.revision, job["chat"])

    def eligible(self, job):
        return job["actor"] == self.owner

    def rendered_reply(self, reply):
        # Reuse the pinned native adapter's escaping/conversion. Fall back before
        # sending, never by retrying an ambiguous answerGuestQuery request.
        plain = text_only(reply)
        if callable(self.formatter):
            try:
                formatted = self.formatter(plain)
                if isinstance(formatted, str) and formatted.strip() and len(formatted) <= 24000:
                    return formatted, "MarkdownV2"
            except Exception:
                pass
        return plain, None

    async def generate_reply(self, job):
        if job["notice"]:
            return Reply(NOTICES[job["notice"]])
        conversation = hashlib.sha256(
            f"guest:{job['actor']}:{job['chat']}:{job['thread']}".encode()
        ).hexdigest()
        payload = {
            "request_id": job["id"],
            "conversation": conversation,
            "route": "public",
            "audience": "new-users",
            "mode": "guest",
            "text": job["text"],
            "reply_context": job["reply_context"],
        }
        try:
            return Reply(await asyncio.to_thread(self.request, self.key, payload))
        except Exception:
            logger.warning("Guest generation unavailable; private details omitted.")
            return Reply("Не удалось подготовить ответ. Попробуйте вызвать меня ещё раз.")

    async def send_reply(self, job, reply):
        from telegram import InlineQueryResultArticle, InputTextMessageContent, LinkPreviewOptions

        text, parse_mode = self.rendered_reply(reply)
        result = InlineQueryResultArticle(
            id=job["id"],
            title="Ответ ассистента",
            input_message_content=InputTextMessageContent(
                text,
                parse_mode=parse_mode,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            ),
        )
        return await self.application.bot.answer_guest_query(
            guest_query_id=job["query_id"],
            result=result,
            read_timeout=10,
            write_timeout=10,
            connect_timeout=5,
            pool_timeout=5,
        )
