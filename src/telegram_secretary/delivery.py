"""Shared durable delivery lifecycle; transports implement reception and sending."""

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .policy import normalize_username
from .rpc import acknowledge_reply, request_reply

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reply:
    text: str = field(repr=False)
    requires_ack: bool = False


class Delivery(ABC):
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
        contacts=frozenset(),
        spouse_username="",
        capacity=None,
    ):
        self.application, self.owner, self.key, self.store = application, owner, key, store
        self.request, self.clock = request, clock
        self.acknowledge = acknowledge
        self.contacts = frozenset(contacts)
        self.spouse_username = normalize_username(spouse_username)
        self.tasks = {}
        self.capacity = capacity if capacity is not None else asyncio.Semaphore(2)

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
        try:
            while job := self.store.claim(connection, chat):
                sent = False
                try:
                    async with self.capacity:
                        if not await self.refresh(connection) or not self.store.can_generate(
                            job["id"]
                        ):
                            self.store.finish(job["id"], "cancelled")
                            continue
                        reply = await self.generate_reply(job)
                        if (
                            not isinstance(reply, Reply)
                            or not reply.text.strip()
                            or len(reply.text) > 3000
                        ):
                            raise ValueError("Invalid reply.")
                        # Recheck after generation too: rights/human takeover may have changed.
                        if not await self.refresh(connection) or not self.store.reserve(job["id"]):
                            self.store.finish(job["id"], "cancelled")
                            continue
                        sent = True  # A lost Telegram HTTP response must never trigger a retry.
                        await self.send_reply(job, reply.text)
                        self.store.finish(job["id"], "sent")
                        if reply.requires_ack:
                            try:
                                await asyncio.to_thread(self.acknowledge, self.key, job["id"])
                            except Exception:
                                logger.warning(
                                    "History acknowledgement failed; delivery was confirmed."
                                )
                except asyncio.CancelledError:
                    self.store.finish(job["id"], "uncertain" if sent else "failed")
                    raise
                except Exception:
                    self.store.finish(job["id"], "uncertain" if sent else "failed")
                    logger.warning("Secretary request failed; private details omitted.")
        finally:
            self.tasks.pop((connection, chat), None)

    async def generate_reply(self, job):
        payload = {
            "request_id": job["id"],
            "conversation": hashlib.sha256(
                f"{job['connection']}:{job['chat']}".encode()
            ).hexdigest(),
            "text": job["text"],
            "audience": self.audience(job["chat"], job["sender_username"]),
        }
        return Reply(await asyncio.to_thread(self.request, self.key, payload), requires_ack=True)

    @abstractmethod
    async def refresh(self, identifier):
        """Validate this transport's current right to deliver."""
        raise NotImplementedError

    @abstractmethod
    async def send_reply(self, job, reply):
        raise NotImplementedError

    def audience(self, chat, sender_username):
        if self.spouse_username and sender_username == self.spouse_username:
            return "spouse"
        return "contacts" if chat in self.contacts else "new-users"
