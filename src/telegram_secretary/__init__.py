"""Native Telegram extension; business updates never enter the owner's agent."""


def register(ctx):
    from .bridge import attach

    ctx.register_telegram_handler(attach)
