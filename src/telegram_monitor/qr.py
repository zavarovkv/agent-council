"""Short-lived QR login rendered only in the operator's private terminal."""

import asyncio
import shutil
import sys
from contextlib import contextmanager

from .login import SetupError, sign_in_password


def require_terminal():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SetupError("qr_terminal")


def render_matrix(matrix):
    """Two square modules per cell, with explicit colors on either terminal theme."""
    rows = []
    for index in range(0, len(matrix), 2):
        top = matrix[index]
        bottom = matrix[index + 1] if index + 1 < len(matrix) else [False] * len(top)
        rows.append("".join(" ▄▀█"[int(a) * 2 + int(b)] for a, b in zip(top, bottom, strict=True)))
    return rows


@contextmanager
def qr_screen(url):
    import qrcode

    require_terminal()
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    rows = render_matrix(matrix)
    width, height = shutil.get_terminal_size()
    if width < max(76, len(matrix[0]) + 2) or height < len(rows) + 6:
        raise SetupError("qr_size")
    try:
        # Alternate screen keeps the bearer token out of ordinary scrollback.
        print("\033[?1049h\033[?25l\033[2J\033[H", end="", flush=True)
        print("Telegram на телефоне: Настройки → Устройства")
        print("→ Подключить устройство. Отсканируйте QR и подтвердите вход.")
        print("QR дает доступ к аккаунту: не пересылайте его. Ctrl+C – отмена.")
        for row in rows:
            print("\033[30;47m" + row + "\033[0m")
        print("Ожидаю подтверждения. Истекший QR обновится автоматически.", flush=True)
        yield
    finally:
        print("\033[0m\033[2J\033[?25h\033[?1049l", end="", flush=True)


async def sign_in_qr(client):
    from telethon.errors import SessionPasswordNeededError

    require_terminal()
    qr = await client.qr_login()
    for attempt in range(5):
        pending = asyncio.create_task(qr.wait())
        try:
            # Register Telethon's UpdateLoginToken listener before displaying a scannable QR.
            await asyncio.sleep(0)
            with qr_screen(qr.url):
                await pending
            return
        except SessionPasswordNeededError:
            await sign_in_password(client)
            return
        except TimeoutError:
            if attempt == 4:
                raise SetupError("qr_expired") from None
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await qr.recreate()
