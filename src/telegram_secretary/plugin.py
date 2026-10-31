"""Wire native SDK admission once; all external routes stay outside the owner agent."""

import asyncio
import logging
import os

from .bridge import BUSINESS_FIELDS, Bridge
from .guest import GuestBridge, is_guest
from .guest_store import GuestStore
from .policy import Settings
from .public import PublicBridge
from .store import Store

logger = logging.getLogger(__name__)


def background(application, coroutine):
    tasks = application.bot_data.setdefault("council-secretary-background", set())
    task = asyncio.get_running_loop().create_task(coroutine)
    tasks.add(task)

    def finished(completed):
        tasks.discard(completed)
        if not completed.cancelled() and completed.exception() is not None:
            logger.error("Secretary background task failed; private details omitted.")

    task.add_done_callback(finished)


def attach(application, adapter):
    from telegram import Update
    from telegram.ext import ApplicationHandlerStop, TypeHandler

    if application.bot_data.get("council-secretary-registered"):
        return
    settings = Settings.load(os.environ)
    bridges = {"business": None, "public": None, "guest": None}
    public_capacity = asyncio.Semaphore(1)
    options = {
        "contacts": settings.contacts,
        "spouse_username": settings.spouse_username,
    }
    if settings.error:
        logger.error("Invalid secretary policy; external updates remain closed.")
    for route, active, filename, factory in (
        ("business", settings.enabled, "queue.sqlite3", Bridge),
        ("public", settings.public_enabled, "public-queue.sqlite3", PublicBridge),
    ):
        if not active:
            continue
        try:
            # One independent generation slot per route: public traffic cannot
            # queue ahead of or occupy the business route's reserved capacity.
            extra = {"capacity": public_capacity if route == "public" else asyncio.Semaphore(1)}
            if route == "public":
                extra["greeting"] = (
                    f"Я {settings.assistant_name}, цифровой помощник {settings.owner_name}. "
                    "Помогу с общими вопросами и уточню ваш запрос. "
                    "Личные договорённости подтверждает сам владелец. Чем могу помочь?"
                )
            bridge = factory(
                application,
                settings.owner,
                settings.key,
                Store(settings.home / "plugin-data/council-secretary" / filename),
                **options,
                **extra,
            )
            bridges[route] = bridge
            background(application, bridge.recover())
        except Exception:
            logger.error("Secretary route unavailable; its external updates remain closed.")
    if settings.enabled:
        try:
            guest_bridge = GuestBridge(
                application,
                settings.owner,
                settings.key,
                GuestStore(settings.home / "plugin-data/council-secretary/guest-queue.sqlite3"),
                home=settings.home,
                formatter=getattr(adapter, "format_message", None),
                capacity=public_capacity,
            )
            bridges["guest"] = guest_bridge
            from .owner_guest import OwnerGuestBridge
            from .owner_store import OwnerStore

            guest_bridge.owner_bridge = OwnerGuestBridge(
                guest_bridge,
                OwnerStore(settings.home / "plugin-data/council-secretary/owner-guest.sqlite3"),
            )
            background(application, guest_bridge.owner_bridge.recover())
            background(application, guest_bridge.recover())
            background(application, guest_bridge.housekeeping())
        except Exception:
            logger.error("Guest route unavailable; guest updates remain closed.")
    if bridges["public"] is not None and adapter is not None:
        from .menus import configure_after_connect

        background(application, configure_after_connect(application, adapter, settings.owner))

    class BusinessHandler(TypeHandler):
        def check_update(self, update):
            return (
                isinstance(update, Update)
                and not is_guest(update)
                and any(getattr(update, name, None) is not None for name in BUSINESS_FIELDS)
            )

    class PublicHandler(TypeHandler):
        def check_update(self, update):
            if (
                not isinstance(update, Update)
                or is_guest(update)
                or any(getattr(update, name, None) is not None for name in BUSINESS_FIELDS)
            ):
                return False
            query = getattr(update, "callback_query", None) or getattr(update, "inline_query", None)
            if query is not None:
                return settings.owner is None or query.from_user.id != settings.owner
            message = getattr(update, "message", None) or getattr(update, "edited_message", None)
            return (
                message is not None
                and getattr(message.chat, "type", None) == "private"
                and (
                    settings.owner is None
                    or getattr(getattr(message, "from_user", None), "id", None) != settings.owner
                )
            )

    class GuestHandler(TypeHandler):
        def check_update(self, update):
            return isinstance(update, Update) and is_guest(update)

    async def guest(update, _context):
        try:
            if bridges["guest"] is not None:
                await bridges["guest"].handle(update)
        except Exception:
            logger.warning("Guest update rejected; private details omitted.")
        finally:
            raise ApplicationHandlerStop

    async def business(update, _context):
        try:
            if bridges["business"] is not None:
                await bridges["business"].handle(update)
        except Exception:
            logger.warning("Business update rejected; private details omitted.")
        finally:
            raise ApplicationHandlerStop

    async def public(update, _context):
        try:
            if getattr(update, "callback_query", None) is not None:
                await update.callback_query.answer("Используйте /help для общения с помощником.")
            elif getattr(update, "inline_query", None) is not None:
                await update.inline_query.answer([], cache_time=0, is_personal=True)
            elif bridges["public"] is not None:
                await bridges["public"].handle(update)
        except Exception:
            logger.warning("Public update rejected; private details omitted.")
        finally:
            raise ApplicationHandlerStop

    application.add_handler(GuestHandler(Update, guest), group=-1100)
    application.add_handler(BusinessHandler(Update, business), group=-1000)
    application.add_handler(PublicHandler(Update, public), group=-1001)
    application.bot_data["council-secretary-registered"] = True
