"""Structured status only; never log credentials, phone numbers or raw messages."""

import asyncio
import json
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime

from .collector import collect
from .config import Settings
from .evidence import daily_window, write_evidence


async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    logging.disable(logging.CRITICAL)
    settings = Settings.load(os.environ)
    now = datetime.now(UTC)
    client = TelegramClient(
        StringSession(settings.session),
        settings.api_id,
        settings.api_hash,
        receive_updates=False,
        flood_sleep_threshold=0,
        request_retries=1,
        connection_retries=1,
        timeout=15,
        device_model="Agent Council collector",
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=20)
        value = await asyncio.wait_for(collect(client, settings, now), timeout=120)
    except Exception as error:
        start, end = daily_window(now)
        value = {
            "schema": 1,
            "source": settings.source,
            "peer_id": settings.peer,
            "generated_at": now.isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "status": "unavailable",
            "coverage": "unknown",
            "reason": "rate_limited"
            if type(error).__name__ == "FloodWaitError"
            else "source_unavailable",
            "messages": [],
        }
    finally:
        with suppress(Exception):
            await asyncio.wait_for(client.disconnect(), timeout=10)
    path = write_evidence("/outbox", value)
    print(
        json.dumps(
            {
                "status": value["status"],
                "artifact": str(path),
                "window_end": value["window_end"],
                "candidate_count": value.get("candidate_count"),
            }
        )
    )
    return 0 if value["status"] == "complete" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (OSError, ValueError, KeyError):
        print(json.dumps({"status": "unavailable", "reason": "reader_not_configured"}))
        raise SystemExit(1) from None
