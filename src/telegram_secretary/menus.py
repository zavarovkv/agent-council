"""Apply official Bot API scopes after pinned Hermes publishes its startup menu."""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def configure_after_connect(application, adapter, owner):
    # Hermes v0.22.5 runs menu registration as deferred housekeeping. Wait for it,
    # rather than racing it or changing the upstream adapter/SDK methods.
    for _ in range(300):
        task = getattr(adapter, "_post_connect_task", None)
        if application.running and task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=90)
            except Exception:
                pass
            break
        if application.running and getattr(adapter, "_command_menu_fingerprint", ""):
            break
        await asyncio.sleep(0.2)
    else:
        logger.error("Public command menu not configured: native startup did not finish.")
        return

    for attempt in range(3):
        try:
            await configure_menu(application.bot, owner)
            return
        except Exception:
            logger.warning("Public command menu update failed; private diagnostics omitted.")
            if attempt < 2:
                await asyncio.sleep(3)


async def configure_menu(bot, owner):
    from hermes_cli.commands_platforms import telegram_menu_commands, telegram_menu_max_commands
    from telegram import (
        BotCommand,
        BotCommandScopeAllGroupChats,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
        BotCommandScopeDefault,
    )

    rows, _ = await asyncio.to_thread(
        telegram_menu_commands, max_commands=telegram_menu_max_commands()
    )
    private = [BotCommand(name, str(description or "")[:256]) for name, description in rows]
    public = [BotCommand("start", "Начать диалог"), BotCommand("help", "Как общаться с помощником")]
    await bot.set_my_commands(private, scope=BotCommandScopeChat(chat_id=owner))
    for scope in (
        BotCommandScopeDefault,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeAllGroupChats,
    ):
        await bot.set_my_commands(public, scope=scope())
