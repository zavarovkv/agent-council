#!/usr/bin/env python3
"""Incremental Google OAuth through a private loopback callback; Drive is preserved."""

import argparse
import fcntl
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive"]
CALLBACK_PORT = 8765
MAX_CLIENT_BYTES = 65536


class SetupError(ValueError):
    """An operator-facing message known to contain no provider secrets."""


def validate_client(data):
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, dict):
        raise SetupError("A Google Desktop app JSON file is required.")
    if not str(installed.get("client_id", "")).endswith(".apps.googleusercontent.com"):
        raise SetupError("Invalid Google client ID.")
    if not installed.get("client_secret"):
        raise SetupError("The client file has no client secret.")
    if installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth":
        raise SetupError("Unexpected authorization endpoint.")
    if installed.get("token_uri") != "https://oauth2.googleapis.com/token":
        raise SetupError("Unexpected token endpoint.")
    return data


def private_write(path, data):
    if path.is_symlink():
        raise SetupError("Refusing a symlinked credential file.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".drive-", delete=False) as output:
            temporary = Path(output.name)
            output.write((json.dumps(data, indent=2) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_client(home, raw):
    if len(raw) > MAX_CLIENT_BYTES:
        raise SetupError("Client JSON exceeds the size limit.")
    data = validate_client(json.loads(raw))
    path = home / "google_client_secret.json"
    if path.exists():
        if path.is_symlink() or json.loads(path.read_text()) != data:
            raise SetupError(
                "A different client is already configured; reconcile it before replacement."
            )
    private_write(path, data)
    print("Google Desktop client saved privately.")


def folder_id(home):
    config = json.loads((home / "council-workspace.json").read_text())
    files = config.get("files", {})
    value = files.get("folder_id", "")
    if files.get("provider") != "google_drive" or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise SetupError("Configure a Google Drive folder in council-workspace.json first.")
    return value


def save_credentials(home, credentials):
    if not credentials.refresh_token:
        raise SetupError("Google did not issue a renewable grant; authorize again with consent.")
    payload = json.loads(credentials.to_json())
    payload["type"] = "authorized_user"
    payload["scopes"] = list(credentials.granted_scopes or credentials.scopes or SCOPES)
    fd = os.open(home / ".google-token.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        private_write(home / "google_token.json", payload)


def requested_scopes(services):
    from council_workspace.google_auth import SCOPES as available

    if not services or not set(services) <= set(available):
        raise SetupError("Choose drive, calendar, gmail and/or analytics.")
    return sorted({scope for name in {"drive", *services} for scope in available[name]})


def check_services(home, credentials, services, expected_email=None):
    from googleapiclient.discovery import build

    granted = credentials.granted_scopes or credentials.scopes or []
    if not set(requested_scopes(services)) <= set(granted):
        raise SetupError(
            "Google did not grant every requested permission; existing access is kept."
        )
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    email = service.about().get(fields="user(emailAddress)").execute()["user"]["emailAddress"]
    if expected_email and email.casefold() != expected_email.casefold():
        raise SetupError("Use the same Google account as the existing Drive connection.")
    target = folder_id(home)
    folder = (
        service.files()
        .get(
            fileId=target,
            supportsAllDrives=True,
            fields="id,name,mimeType,trashed,webViewLink,capabilities(canAddChildren)",
        )
        .execute()
    )
    if folder.get("mimeType") != "application/vnd.google-apps.folder" or folder.get("trashed"):
        raise SetupError("The configured destination is not an active Drive folder.")
    if not folder.get("capabilities", {}).get("canAddChildren"):
        raise SetupError("The Google account cannot add files to the selected folder.")
    ready = {"drive": {"ready": True, "folder": folder["name"], "url": folder.get("webViewLink")}}
    for name in services:
        try:
            if name == "calendar":
                calendar = build("calendar", "v3", credentials=credentials, cache_discovery=False)
                calendar.calendarList().list(maxResults=1).execute()
                ready[name] = {"ready": True}
            elif name == "gmail":
                gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
                profile = gmail.users().getProfile(userId="me").execute()
                if profile["emailAddress"].casefold() != email.casefold():
                    raise SetupError("Gmail and Drive account identities differ.")
                ready[name] = {"ready": True, "account": email}
            elif name == "analytics":
                admin = build(
                    "analyticsadmin", "v1beta", credentials=credentials, cache_discovery=False
                )
                result = admin.accountSummaries().list(pageSize=200).execute()
                # Metadata is read-only and works even if the account has no properties.
                data = build(
                    "analyticsdata", "v1beta", credentials=credentials, cache_discovery=False
                )
                data.properties().getMetadata(name="properties/0/metadata").execute()
                ready[name] = {
                    "ready": True,
                    "accounts_on_first_page": len(result.get("accountSummaries", [])),
                }
        except SetupError:
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            raise SetupError(
                f"{name} API check failed (HTTP {status or 'n/a'}). Enable its API and check access; the previous grant is kept."
            ) from None
    return {"ready": True, "account": email, "services": ready}


def verify(home, services=("drive",)):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = home / "google_token.json"
    if path.is_symlink():
        raise SetupError("Refusing a symlinked token file.")
    credentials = Credentials.from_authorized_user_file(str(path))
    if not credentials.has_scopes(requested_scopes(services)) or not credentials.refresh_token:
        raise SetupError("Authorize the requested services with a renewable Google grant.")
    # Prove refresh works as well as a still-valid short-lived access token.
    credentials.refresh(Request())
    result = check_services(home, credentials, services)
    save_credentials(home, credentials)
    print(json.dumps(result, ensure_ascii=False))


def build_flow(home, scopes=None):
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_path = home / "google_client_secret.json"
    if client_path.is_symlink():
        raise SetupError("Refusing a symlinked client file.")
    validate_client(json.loads(client_path.read_text()))
    return InstalledAppFlow.from_client_secrets_file(
        str(client_path),
        scopes=scopes or SCOPES,
        autogenerate_code_verifier=True,
    )


def authorize(home, services=("drive",)):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    folder_id(home)
    scopes = requested_scopes(services)
    expected_email = None
    token = home / "google_token.json"
    if token.exists():
        if token.is_symlink() or token.stat().st_mode & 0o077:
            raise SetupError("A private regular token file is required.")
        previous = Credentials.from_authorized_user_file(str(token))
        if previous.has_scopes(scopes):
            verify(home, services)
            return
        if not previous.valid:
            previous.refresh(Request())
        expected_email = (
            build("drive", "v3", credentials=previous, cache_discovery=False)
            .about()
            .get(fields="user(emailAddress)")
            .execute()["user"]["emailAddress"]
        )
        scopes = sorted(set(scopes) | set(previous.scopes or []))
    # PKCE, state validation and the code exchange remain in Google's library.
    # No callback URL/code is passed through chat, process arguments or logs.
    flow = build_flow(home, scopes)
    credentials = flow.run_local_server(
        host="127.0.0.1",
        bind_addr="0.0.0.0",
        port=CALLBACK_PORT,
        open_browser=False,
        timeout_seconds=600,
        authorization_prompt_message="Open this Google consent URL:\n{url}",
        success_message="Google authorization received. You can close this tab.",
        access_type="offline",
        prompt="consent",
        login_hint=expected_email or "",
    )
    if not credentials.refresh_token or not set(scopes) <= set(
        credentials.granted_scopes or credentials.scopes or []
    ):
        raise SetupError("The renewed grant is incomplete; previous access is kept.")
    credentials.refresh(Request())
    result = check_services(home, credentials, services, expected_email)
    save_credentials(home, credentials)
    print(json.dumps(result, ensure_ascii=False))


def main():
    os.umask(0o077)
    for name in ("google_auth_oauthlib", "oauthlib", "google.auth", "urllib3"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("import-client", "authorize", "verify"))
    parser.add_argument(
        "--services", default="drive", help="Comma-separated drive,calendar,gmail,analytics"
    )
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SetupError("Run inside the container as the hermes user.")
    home = Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve(strict=True)
    fd = os.open(home / ".drive-setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "import-client":
            import_client(home, sys.stdin.buffer.read(MAX_CLIENT_BYTES + 1))
        elif args.action == "authorize":
            authorize(home, args.services.split(","))
        else:
            verify(home, args.services.split(","))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider errors may contain callback URLs or token responses.
        status = getattr(getattr(exc, "resp", None), "status", None)
        if isinstance(exc, SetupError):
            print(f"Drive setup stopped: {exc}", file=sys.stderr)
        else:
            print(
                f"Drive setup failed ({type(exc).__name__}, HTTP {status or 'n/a'}).",
                file=sys.stderr,
            )
            print(
                "Check that the requested Google APIs are enabled and consent completed; credentials were not printed.",
                file=sys.stderr,
            )
        sys.exit(1)
