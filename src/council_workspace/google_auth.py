"""One private Google grant, serialized refresh, and service-specific scopes."""

import fcntl
import json
import os
import tempfile

SCOPES = {
    "drive": ["https://www.googleapis.com/auth/drive"],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/calendar.freebusy",
    ],
    "gmail": ["https://www.googleapis.com/auth/gmail.modify"],
    "analytics": ["https://www.googleapis.com/auth/analytics.readonly"],
}
APIS = {
    "drive": ("drive", "v3"),
    "calendar": ("calendar", "v3"),
    "gmail": ("gmail", "v1"),
    "analytics": ("analyticsdata", "v1beta"),
    "analyticsadmin": ("analyticsadmin", "v1beta"),
}


def private_write(path, payload):
    if path.resolve() != path:
        raise PermissionError("Refusing a symlinked Google grant.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = output.name
            os.fchmod(output.fileno(), 0o600)
            output.write((json.dumps(payload) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def service_for(home, name):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    from httplib2 import Http

    required = SCOPES["analytics" if name == "analyticsadmin" else name]
    path = home / "google_token.json"
    with os.fdopen(
        os.open(home / ".google-token.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600),
        "a",
    ) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.resolve() != path or path.stat().st_mode & 0o077:
            raise PermissionError("A private existing Google grant is required.")
        credentials = Credentials.from_authorized_user_file(str(path))
        if not credentials.has_scopes(required):
            raise PermissionError("Authorize the requested Google service first.")
        if not credentials.valid:
            credentials.refresh(Request())
            payload = json.loads(credentials.to_json())
            payload["type"] = "authorized_user"
            private_write(path, payload)
    return build(
        *APIS[name], http=AuthorizedHttp(credentials, http=Http(timeout=30)), cache_discovery=False
    )
