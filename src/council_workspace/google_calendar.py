"""Calendar operations with explicit invitations, revision checks and readback."""

import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def window(args):
    values = [args.get("start"), args.get("end")]
    try:
        times = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in values]
        if any(value.tzinfo is None for value in times) or times[1] <= times[0]:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise ValueError(
            "Provide start and end as ISO timestamps with UTC offsets; end must follow start."
        ) from None
    return values


def email(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value)
        or len(value) > 254
    ):
        raise ValueError("Use explicit participant email addresses, not guessed names.")
    return value


def event_body(args):
    body = args.get("body")
    if not isinstance(body, dict) or not set(body) <= {
        "summary",
        "description",
        "location",
        "start",
        "end",
        "timezone",
        "attendees",
        "meet",
    }:
        raise ValueError("Unsupported calendar event fields.")
    if not isinstance(body.get("summary"), str) or not 1 <= len(body["summary"].strip()) <= 200:
        raise ValueError("Provide a short meeting title.")
    start, end = window(body)
    zone = body.get("timezone", "Europe/Moscow")
    try:
        ZoneInfo(zone)
    except (TypeError, ValueError, KeyError):
        raise ValueError("Use an IANA timezone, such as Europe/Moscow.") from None
    result = {
        "summary": body["summary"].strip(),
        "start": {"dateTime": start, "timeZone": zone},
        "end": {"dateTime": end, "timeZone": zone},
    }
    for key in ("description", "location"):
        if key in body:
            if not isinstance(body[key], str) or len(body[key]) > 8000:
                raise ValueError("Use bounded event text.")
            result[key] = body[key]
    if "attendees" in body:
        if not isinstance(body["attendees"], list) or len(body["attendees"]) > 50:
            raise ValueError("Use up to 50 explicit participant emails.")
        result["attendees"] = [
            {"email": value} for value in dict.fromkeys(email(v) for v in body["attendees"])
        ]
    if "meet" in body and type(body["meet"]) is not bool:
        raise ValueError("meet must be a boolean.")
    return result


class Calendar:
    def __init__(self, api):
        self.api = api

    def read(self, args):
        action, calendar = args["action"], args.get("calendar", "primary")
        if action == "calendars":
            return (
                self.api.calendarList().list(maxResults=100, pageToken=args.get("cursor")).execute()
            )
        if action == "get":
            return self.api.events().get(calendarId=calendar, eventId=args["id"]).execute()
        start, end = window(args)
        if action == "availability":
            return (
                self.api.freebusy()
                .query(body={"timeMin": start, "timeMax": end, "items": [{"id": calendar}]})
                .execute()
            )
        return (
            self.api.events()
            .list(
                calendarId=calendar,
                timeMin=start,
                timeMax=end,
                maxResults=100,
                singleEvents=True,
                orderBy="startTime",
                pageToken=args.get("cursor"),
            )
            .execute()
        )

    def prepare_write(self, args):
        action, calendar = args["action"], args.get("calendar", "primary")
        api = self.api.events()
        if action == "create":
            body = event_body(args)
            body["id"] = "c" + hashlib.sha256((calendar + ":" + args["key"]).encode()).hexdigest()
            if args["body"].get("meet"):
                body["conferenceData"] = {
                    "createRequest": {
                        "requestId": body["id"],
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }
            request = api.insert(
                calendarId=calendar, body=body, sendUpdates="all", conferenceDataVersion=1
            )
            identity = body["id"]
        else:
            identity = args["id"]
            current = api.get(calendarId=calendar, eventId=identity).execute()
            if not args.get("revision") or args["revision"] != current.get("etag"):
                raise ValueError("Read the current event and pass its etag as revision.")
            if action == "cancel":
                request = api.delete(calendarId=calendar, eventId=identity, sendUpdates="all")
            else:
                body = event_body(args)
                request = api.patch(
                    calendarId=calendar, eventId=identity, body=body, sendUpdates="all"
                )
            request.headers["If-Match"] = current["etag"]

        def perform():
            request.execute(num_retries=0)
            if action == "cancel":
                return {"id": identity, "status": "cancelled", "notifications_requested": True}
            saved = api.get(calendarId=calendar, eventId=identity).execute()
            if saved.get("summary") != body["summary"] or saved.get("status") == "cancelled":
                raise ValueError(
                    "Calendar readback did not match; inspect the event before retrying."
                )
            for field in ("start", "end"):
                expected = datetime.fromisoformat(body[field]["dateTime"].replace("Z", "+00:00"))
                actual = datetime.fromisoformat(saved[field]["dateTime"].replace("Z", "+00:00"))
                if expected != actual:
                    raise ValueError("Calendar time readback differs.")
            if "attendees" in body and {p["email"].casefold() for p in body["attendees"]} != {
                p["email"].casefold() for p in saved.get("attendees", [])
            }:
                raise ValueError("Calendar attendee readback differs.")
            return {**saved, "verified": True, "notifications_requested": True}

        return perform
