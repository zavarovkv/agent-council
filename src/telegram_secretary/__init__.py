"""Native Telegram extension with separate private, public and business routes."""


def register(ctx):
    from .plugin import attach

    ctx.register_telegram_handler(attach)
