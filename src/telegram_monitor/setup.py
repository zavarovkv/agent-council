"""Interactive account login. Save a private session without ever printing it."""

import asyncio
import getpass
import logging
import os
import re
from contextlib import suppress
from pathlib import Path

from .codes import CodePrompt
from .config import Settings, source_name
from .login import SetupError, failure_message, sign_in


async def verify_source(client, source, expected_peer=None):
    peer = await client.get_entity(source)
    if type(peer).__name__ != "Channel" or getattr(peer, "left", False):
        raise SetupError("source")
    if expected_peer is not None and peer.id != expected_peer:
        raise SetupError("source_changed")
    await client.get_messages(peer, limit=1)
    return peer


async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    client, stage = None, "configuration"
    try:
        source = source_name(os.environ["COUNCIL_TG_READER_SOURCE"])
        print("Подключение Telegram. Ввод скрыт; для отмены нажмите Ctrl+C.", flush=True)
        api_id = getpass.getpass("Telegram API ID: ").strip()
        api_hash = getpass.getpass("Telegram API hash: ").strip()
        if (
            not re.fullmatch(r"[0-9]{1,16}", api_id)
            or int(api_id) <= 0
            or not re.fullmatch(r"[a-f0-9]{32}", api_hash)
        ):
            raise SetupError("api_credentials")
        phone = getpass.getpass("Номер Telegram с кодом страны: ").strip()
        if not re.fullmatch(r"\+[1-9][0-9]{6,14}", phone):
            raise SetupError("phone")
        client = TelegramClient(
            StringSession(),
            int(api_id),
            api_hash,
            receive_updates=False,
            device_model="Agent Council collector",
            request_retries=1,
            connection_retries=1,
            flood_sleep_threshold=0,
            timeout=15,
        )
        stage = "connect"
        await client.connect()
        stage = "send_code"
        sent = await client.send_code_request(phone)
        stage = "sign_in"
        code_prompt = CodePrompt(sent)
        await sign_in(client, phone, code_prompt.code_hash, code_prompt=code_prompt)
        account = await client.get_me()
        if account.bot:
            raise SetupError("account")
        stage = "source"
        print("Вход выполнен. Проверяю доступ к чату…", flush=True)
        expected = os.environ.get("COUNCIL_TG_READER_EXPECTED_PEER", "")
        peer = await verify_source(client, source, int(expected) if expected else None)
        stage = "save"
        values = {
            "COUNCIL_TG_READER_API_ID": api_id,
            "COUNCIL_TG_READER_API_HASH": api_hash,
            "COUNCIL_TG_READER_SESSION": client.session.save(),
            "COUNCIL_TG_READER_ACCOUNT_ID": str(account.id),
            "COUNCIL_TG_READER_PEER_ID": str(peer.id),
            "COUNCIL_TG_READER_SOURCE": source,
        }
        Settings.load(values)
        target = Path("/setup/telegram-reader.env")
        if target.exists() or target.is_symlink() or target.parent.resolve() != target.parent:
            raise ValueError("Use an empty dedicated setup directory.")
        with target.open("x", encoding="utf-8") as output:
            os.chmod(target, 0o600)
            output.write("".join(f"{name}={value}\n" for name, value in values.items()))
            output.flush()
            os.fsync(output.fileno())
        print(
            f"Authorized user account {account.id}; pinned source @{source} ({peer.id}). Credentials saved privately; no messages sent."
        )
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(failure_message(error, stage), flush=True)
        return 1
    finally:
        if client is not None:
            with suppress(Exception):
                await client.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
