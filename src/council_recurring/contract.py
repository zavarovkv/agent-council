"""Versioned, bounded recurring briefs shared by the scheduler and adapter."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Europe/Moscow")
FIELDS = {
    "id",
    "title",
    "brief",
    "team",
    "project",
    "notion_parent",
    "start_time",
    "cutoff_time",
    "delivery_time",
    "weekdays",
    "window_days",
    "telegram_source",
}


def definition(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("Use the recurring definition schema.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,47}", value["id"]):
        raise ValueError("Use a stable lowercase job name.")
    for key, limit in (("title", 120), ("brief", 8000)):
        if not isinstance(value[key], str) or not 1 <= len(value[key].strip()) <= limit:
            raise ValueError("A bounded title and self-contained brief are required.")
    for key in ("team", "project", "notion_parent"):
        if key == "project" and value[key] is None:
            continue
        if not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9-]{32,36}", value[key]):
            raise ValueError("Use an explicit immutable destination ID.")
    for key in ("start_time", "cutoff_time", "delivery_time"):
        if not isinstance(value[key], str) or not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", value[key]
        ):
            raise ValueError("Times must be HH:MM in Europe/Moscow.")
    if not value["cutoff_time"] <= value["start_time"] < value["delivery_time"]:
        raise ValueError("Require cutoff <= preparation < delivery on the same Moscow date.")
    days = value["weekdays"]
    if (
        not isinstance(days, list)
        or not days
        or len(days) > 7
        or any(type(d) is not int or not 0 <= d <= 6 for d in days)
        or len(set(days)) != len(days)
    ):
        raise ValueError("Weekdays are unique integers, Monday=0 through Sunday=6.")
    if type(value["window_days"]) is not int or not 1 <= value["window_days"] <= 31:
        raise ValueError("Reporting window must be 1–31 days.")
    if type(value["telegram_source"]) is not bool:
        raise ValueError("Telegram coverage must be explicitly selected.")
    return {**value, "weekdays": sorted(days)}


def revision(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def occurrence(value, day):
    date = datetime.strptime(day, "%Y-%m-%d").date()
    if date.isoformat() != day or date.weekday() not in value["weekdays"]:
        raise ValueError("This date is outside the schedule.")
    end = datetime.fromisoformat(day + "T" + value["cutoff_time"]).replace(tzinfo=ZONE)
    deadline = datetime.fromisoformat(day + "T" + value["delivery_time"]).replace(tzinfo=ZONE)
    return {
        "key": value["id"] + ":" + day,
        "day": day,
        "window_start": (end - timedelta(days=value["window_days"])).isoformat(),
        "window_end": end.isoformat(),
        "deadline": deadline.timestamp(),
    }


def start_schedule(value):
    # The pinned native scheduler uses UTC. Moscow has a fixed +03:00 offset;
    # shift weekdays as well for preparation before 03:00 local time.
    hour, minute = map(int, value["start_time"].split(":"))
    days = sorted({(d + 1 - (hour < 3)) % 7 for d in value["weekdays"]})
    return f"{minute} {(hour - 3) % 24} * * {','.join(map(str, days))}"
