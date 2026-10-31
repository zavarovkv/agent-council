"""Read one pinned source with a user account; never send, join or acknowledge chats."""

from datetime import UTC

from .evidence import daily_window, select_context


async def collect(client, settings, now, *, limit=5000):
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError("Use a history limit from one to 5000 messages.")
    start, end = daily_window(now)
    value = {
        "schema": 1,
        "source": settings.source,
        "peer_id": settings.peer,
        "generated_at": now.astimezone(UTC).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "status": "unavailable",
        "coverage": "unknown",
        "content_scope": "Message text and captions only; attachments, audio and images are not inspected.",
        "selection": "All text within the context budget; otherwise OTA keyword context plus recent text.",
        "messages_seen": 0,
        "candidate_count": 0,
        "messages": [],
    }
    if not await client.is_user_authorized():
        return {**value, "reason": "reauthorization_required"}
    account = await client.get_me()
    if account.id != settings.account or getattr(account, "bot", False):
        return {**value, "reason": "account_pin_mismatch"}
    peer = await client.get_entity(settings.source)
    if type(peer).__name__ != "Channel" or peer.id != settings.peer or getattr(peer, "left", False):
        return {**value, "reason": "source_pin_or_membership_mismatch"}
    messages, scanned, bounded = [], 0, False
    async for message in client.iter_messages(peer, offset_date=end, limit=limit + 1):
        if message.date.tzinfo is None:
            return {**value, "reason": "invalid_source_timestamp"}
        if message.date < start:
            break
        if message.date >= end:
            continue
        scanned += 1
        if scanned > limit:
            bounded = True
            break
        text = message.message or ""
        if not isinstance(text, str):
            continue
        messages.append(
            {
                "id": message.id,
                "date": message.date.astimezone(UTC).isoformat(),
                "text": text[:8192],
                "text_truncated": len(text) > 8192,
                "url": f"https://t.me/{settings.source}/{message.id}",
            }
        )
    selected, count, truncated = select_context(messages)
    partial = bounded or truncated or any(message["text_truncated"] for message in messages)
    return {
        **value,
        "status": "incomplete" if partial else "complete",
        "coverage": "partial_text_window" if partial else "full_text_window",
        "messages_seen": min(scanned, limit),
        "candidate_count": count,
        "messages": selected,
    }
