"""Bounded daily windows, candidate matching, context and atomic evidence files."""

import json
import os
import re
import tempfile
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Europe/Moscow")
MAX_EVIDENCE_BYTES = 2 * 1048576
ALIASES = {
    "Booking.com": ("booking.com", "booking", "букинг"),
    "Expedia": ("expedia", "экспедиа", "hotels.com", "vrbo"),
    "Agoda": ("agoda", "агода"),
    "Trip.com": ("trip.com", "ctrip", "трипком"),
    "Airbnb": ("airbnb", "аирбнб", "эйрбнб"),
    "Ostrovok": ("ostrovok", "островок", "ratehawk"),
    "Yandex Travel": ("яндекс путешествия", "яндекс.путешествия", "travel.yandex"),
    "Ozon Travel": ("ozon travel", "ozon.travel", "озон тревел", "озон трэвел"),
    "Tutu": ("tutu", "туту"),
    "Aviasales": ("aviasales", "авиасейлс", "авиасэйлс"),
    "OneTwoTrip": ("onetwotrip", "уантутрип"),
}
PATTERNS = {
    name: re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(alias) for alias in aliases) + r")(?!\w)", re.IGNORECASE
    )
    for name, aliases in ALIASES.items()
}


def read_json(path, limit=65536):
    path = Path(path)
    if path.resolve() != path or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Missing or unsafe source artifact.")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("An object artifact is required.")
    return value


def daily_window(now):
    if now.tzinfo is None:
        raise ValueError("Use an aware collection timestamp.")
    local = now.astimezone(ZONE)
    end = datetime.combine(local.date(), time(7, 15), tzinfo=ZONE)
    if end > local:
        end -= timedelta(days=1)
    return (end - timedelta(days=1)).astimezone(UTC), end.astimezone(UTC)


def select_context(messages, *, limit=300):
    """Keep small windows whole, otherwise prioritize keyword context and recent text."""
    if type(limit) is not int or not 1 <= limit <= 300:
        raise ValueError("Use a context limit from one to 300 messages.")
    messages = sorted(messages, key=lambda item: (item["date"], item["id"]))
    candidates, indexes = {}, set()
    for index, message in enumerate(messages):
        matched = [name for name, pattern in PATTERNS.items() if pattern.search(message["text"])]
        if matched:
            candidates[message["id"]] = matched
            indexes.update(range(max(0, index - 2), min(len(messages), index + 3)))
    chosen = set(sorted(indexes, reverse=True)[: max(0, limit - min(50, limit))])
    for index in range(len(messages) - 1, -1, -1):
        if len(chosen) >= limit:
            break
        chosen.add(index)
    selected = []
    for index in sorted(chosen):
        message = messages[index]
        selected.append({**message, "ota_candidates": candidates.get(message["id"], [])})
    return selected, len(candidates), len(selected) < len(messages)


def encode_evidence(value):
    def encode():
        return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()

    data = encode()
    if len(data) <= MAX_EVIDENCE_BYTES:
        return data
    messages = value["messages"]
    value.update(status="incomplete", coverage="partial_text_window", evidence_truncated=True)
    low, high = 0, len(messages)
    while low < high:
        count = (low + high + 1) // 2
        value["messages"] = messages[-count:]
        if len(encode()) <= MAX_EVIDENCE_BYTES:
            low = count
        else:
            high = count - 1
    value["messages"] = messages[-low:] if low else []
    data = encode()
    if len(data) > MAX_EVIDENCE_BYTES:
        raise ValueError("Evidence metadata exceeds the bounded daily artifact size.")
    return data


def write_evidence(root, value):
    root = Path(root)
    if root.resolve() != root or not root.is_dir():
        raise ValueError("Use the dedicated regular evidence directory.")
    day = datetime.fromisoformat(value["window_end"]).astimezone(ZONE).date().isoformat()
    target = root / (day + ".json")
    if target.is_symlink():
        raise ValueError("Refusing a symlinked evidence file.")
    data = encode_evidence(value)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".evidence-", delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    # Only bounded source-evidence files; reports and arbitrary workspace files are separate.
    for old in sorted(root.glob("????-??-??.json"))[:-7]:
        if old.is_symlink() or not old.is_file() or old.stat().st_size > MAX_EVIDENCE_BYTES:
            continue
        try:
            previous = json.loads(old.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(previous, dict):
            continue
        if previous.get("schema") == 1 and previous.get("peer_id") == value["peer_id"]:
            old.unlink()
    return target
