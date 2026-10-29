#!/usr/bin/env python3
"""Google Drive-only OAuth using Google's installed-app SDK inside Hermes."""

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
    payload["scopes"] = list(credentials.granted_scopes or SCOPES)
    private_write(home / "google_token.json", payload)


def verify(home):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    path = home / "google_token.json"
    if path.is_symlink():
        raise SetupError("Refusing a symlinked token file.")
    credentials = Credentials.from_authorized_user_file(str(path))
    if not credentials.has_scopes(SCOPES) or not credentials.refresh_token:
        raise SetupError("A renewable Google Drive grant is required.")
    # Prove refresh works as well as a still-valid short-lived access token.
    credentials.refresh(Request())
    save_credentials(home, credentials)
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
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
    service.files().list(
        q=f"'{target}' in parents and trashed = false",
        pageSize=1,
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    print(
        json.dumps(
            {"ready": True, "folder": folder["name"], "url": folder.get("webViewLink")},
            ensure_ascii=False,
        )
    )


def build_flow(home):
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_path = home / "google_client_secret.json"
    if client_path.is_symlink():
        raise SetupError("Refusing a symlinked client file.")
    validate_client(json.loads(client_path.read_text()))
    return InstalledAppFlow.from_client_secrets_file(
        str(client_path),
        scopes=SCOPES,
        autogenerate_code_verifier=True,
    )


def authorize(home):
    folder_id(home)
    if (home / "google_token.json").exists():
        verify(home)
        return
    # PKCE, state validation and the code exchange remain in Google's library.
    # No callback URL/code is passed through chat, process arguments or logs.
    flow = build_flow(home)
    credentials = flow.run_local_server(
        host="127.0.0.1",
        bind_addr="0.0.0.0",
        port=CALLBACK_PORT,
        open_browser=False,
        timeout_seconds=300,
        authorization_prompt_message="Open this Google consent URL:\n{url}",
        success_message="Google authorization received. You can close this tab.",
        access_type="offline",
        prompt="consent",
    )
    save_credentials(home, credentials)
    verify(home)


def main():
    os.umask(0o077)
    for name in ("google_auth_oauthlib", "oauthlib", "google.auth", "urllib3"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("import-client", "authorize", "verify"))
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
            authorize(home)
        else:
            verify(home)


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
                "Check that Drive API is enabled and Google consent completed; credentials were not printed.",
                file=sys.stderr,
            )
        sys.exit(1)
