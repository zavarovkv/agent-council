"""Interactive account login. Save a private session without ever printing it."""

import asyncio
import getpass
import logging
import os
import re
from pathlib import Path

from .config import Settings, source_name


async def verify_source(client, source, expected_peer=None):
    peer = await client.get_entity(source)
    if (
        type(peer).__name__ != "Channel"
        or getattr(peer, "left", False)
        or (expected_peer is not None and peer.id != expected_peer)
    ):
        raise ValueError("The source pin or existing membership is not valid.")
    await client.get_messages(peer, limit=1)
    return peer


async def main():
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession

    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    source = source_name(os.environ["COUNCIL_TG_READER_SOURCE"])
    api_id = getpass.getpass("Telegram API ID: ").strip()
    api_hash = getpass.getpass("Telegram API hash: ").strip()
    if (
        not re.fullmatch(r"[0-9]{1,16}", api_id)
        or int(api_id) <= 0
        or not re.fullmatch(r"[a-f0-9]{32}", api_hash)
    ):
        raise ValueError("Invalid Telegram API credentials.")
    phone = getpass.getpass("Telegram account phone with country code: ").strip()
    if not re.fullmatch(r"\+[1-9][0-9]{6,14}", phone):
        raise ValueError("Use an international account phone number.")
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
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        code = getpass.getpass("Telegram login code (hidden): ").strip()
        try:
            await client.sign_in(phone, code=code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("Telegram two-step password (hidden): "))
        account = await client.get_me()
        if account.bot:
            raise ValueError("Use an existing user account that is a member of the source.")
        expected = os.environ.get("COUNCIL_TG_READER_EXPECTED_PEER", "")
        peer = await verify_source(client, source, int(expected) if expected else None)
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
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (Exception, KeyboardInterrupt):
        print(
            "Telegram reader setup did not complete. No account or source was joined automatically; credentials were not printed."
        )
        raise SystemExit(1) from None
