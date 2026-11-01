"""Deliver one substantive owner reply without acknowledgement or progress messages."""

import asyncio
import json
import logging

from . import owner_agent
from .guest import NOTICES
from .guest_policy import load_state

logger = logging.getLogger(__name__)
INTERRUPTED = "Не удалось подтвердить завершение. Проверьте сессию Council перед повтором задания: часть действий могла выполниться."


def bounded_reply(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Missing owner reply")
    if len(text) > 3000:
        return text[:2900] + "\n… Полный результат сохранён в сессии Council."
    return text


class OwnerGuestBridge:
    def __init__(self, guest, store, *, execute=owner_agent.execute):
        self.guest, self.store, self.execute = guest, store, execute
        self.recoverable = store.interrupted()

    async def authorized(self, job, epoch):
        return (
            self.guest.eligible(job)
            and load_state(self.guest.home).owner_agent
            and await self.guest.refresh(epoch)
        )

    async def edit(self, job, epoch, inline_id, text):
        from telegram import LinkPreviewOptions

        if not await self.authorized(job, epoch):
            raise PermissionError("Owner authority revoked")
        rendered, parse_mode = self.guest.rendered_reply(bounded_reply(text))
        await self.guest.application.bot.edit_message_text(
            inline_message_id=inline_id,
            text=rendered,
            parse_mode=parse_mode,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            read_timeout=10,
            write_timeout=10,
            connect_timeout=5,
            pool_timeout=5,
        )

    async def handle(self, job, epoch):
        if not await self.authorized(job, epoch) or not self.store.admit(job, epoch):
            return
        self.guest.application.create_task(self.run(job, epoch))

    async def run(self, job, epoch):
        delivery_attempted = False
        try:
            if job["expires"] <= self.guest.clock() or not await self.authorized(job, epoch):
                self.store.update(job["id"], status="cancelled")
                return
            self.store.update(job["id"], status="running")
            if job["notice"]:
                reply = NOTICES[job["notice"]]
            else:
                reply = await asyncio.wait_for(
                    self.execute(
                        job,
                        lambda: self.authorized(job, epoch),
                        lambda sid: self.store.update(job["id"], session_id=sid),
                    ),
                    1800,
                )
            reply = bounded_reply(reply)
            if not await self.authorized(job, epoch):
                raise PermissionError("Owner authority revoked")
            # This consumes the one guest reply. An ambiguous delivery must never
            # be retried or followed by a second answer, even without a receipt.
            delivery_attempted = True
            receipt = await self.guest.send_reply(job, reply)
            inline_id = getattr(receipt, "inline_message_id", None)
            if not isinstance(inline_id, str) or not inline_id:
                raise ValueError("No guest delivery receipt")
            self.store.update(job["id"], status="done", inline_id=inline_id)
        except BaseException as error:
            self.store.update(job["id"], status="uncertain")
            if not delivery_attempted and not isinstance(
                error, (PermissionError, asyncio.CancelledError)
            ):
                try:
                    if await self.authorized(job, epoch):
                        await self.guest.send_reply(job, INTERRUPTED)
                except Exception:
                    pass
            logger.warning("Owner guest run interrupted; private details omitted.")
            if isinstance(error, asyncio.CancelledError):
                raise

    async def recover(self):
        for row in self.recoverable:
            job = json.loads(row["payload"])
            # Never replay a potentially side-effectful run after an uncertain crash.
            try:
                if row["session_id"]:
                    await asyncio.wait_for(owner_agent.cancel_session(row["session_id"]), 15)
                if row["inline_id"]:
                    await self.edit(
                        job,
                        row["epoch"],
                        row["inline_id"],
                        "Сервис перезапущен. Проверьте результат задания в Council перед повтором; автоматического перезапуска нет.",
                    )
            except Exception:
                logger.warning("Owner guest recovery unavailable; private details omitted.")
            finally:
                self.store.update(row["id"], status="uncertain")
