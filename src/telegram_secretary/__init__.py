"""Native Telegram extension with separate private, public and business routes."""


def register(ctx):
    from .plugin import attach

    # This owner's bot speaks Russian even with Hermes' default English UI.
    # Native locale packs preserve upstream shutdown delivery and unload cleanly.
    notices = {
        "gateway.shutdown.notice_restart": (
            "Перезапускаюсь. Текущая работа прервется. "
            "Когда снова буду на связи, напиши – попробую продолжить."
        ),
        "gateway.shutdown.notice_shutdown": (
            "Ухожу офлайн. Текущая работа прервется. "
            "Когда снова буду на связи, напиши – попробую продолжить."
        ),
    }
    for language in ("en", "ru"):
        ctx.register_locale(language, notices)
    ctx.register_telegram_handler(attach)
