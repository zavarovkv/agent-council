#!/usr/bin/env python3
"""Read Telegram OIDC credentials privately over an interactive SSH terminal."""

import fcntl
import getpass
import http.client
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

try:
    from .env_utils import parse_environment, write_environment
except ImportError:
    from env_utils import parse_environment, write_environment


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run this script as root on the Hermes server.")
    target = Path("/etc/hermes/telegram.env")
    format_marker = target.with_suffix(".env.format")
    raw_format = (
        format_marker.exists() and format_marker.read_text(encoding="utf-8").strip() == "raw"
    )
    existing = (
        parse_environment(target.read_text(encoding="utf-8"), raw=raw_format)
        if target.exists()
        else {}
    )
    origin = "https://hermes.2.29.13.129.sslip.io"
    deployment_kind = Path("/etc/hermes/deployment-kind")
    docker_deployment = (
        deployment_kind.exists() and deployment_kind.read_text(encoding="utf-8").strip() == "docker"
    )
    if docker_deployment:
        for line in Path("/etc/agent-council/compose.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("HERMES_PUBLIC_URL="):
                origin = line.split("=", 1)[1].strip().rstrip("/")
        if urlsplit(origin).scheme != "https" or not urlsplit(origin).hostname:
            raise SystemExit("The deployment public URL must use HTTPS.")
    print("In @BotFather → your bot → Login Widget, register these Allowed URLs:")
    print(origin)
    print(origin + "/auth/callback")
    print("Use the Login Widget Client Secret, not the bot's Bot API token.")
    client_id = input("Telegram Login Client ID: ").strip()
    if not re.fullmatch(r"[0-9]{1,30}", client_id):
        raise SystemExit("Client ID must be the number shown in BotFather.")
    secret = getpass.getpass("Telegram Login Client Secret (hidden): ").strip()
    if len(secret) < 16 or any(ord(char) < 32 for char in secret):
        raise SystemExit("Client Secret is empty or invalid; existing configuration was preserved.")
    previous_id = existing.get("HERMES_TELEGRAM_ALLOWED_ID", "")
    previous_username = existing.get("HERMES_TELEGRAM_OWNER_USERNAME", "")
    owner_id = (
        input(f"Allowed numeric Telegram ID [{previous_id or 'optional, preferred'}]: ").strip()
        or previous_id
    )
    owner_username = previous_username
    if not owner_id:
        owner_username = (
            input(f"Allowed Telegram username [{previous_username}]: ").strip() or previous_username
        )
        owner_username = owner_username.removeprefix("@").lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", owner_username):
            raise SystemExit("A valid owner username or numeric ID is required.")
    elif not re.fullmatch(r"[0-9]{1,19}", owner_id) or not 0 < int(owner_id) < 2**63:
        raise SystemExit("The Telegram account ID is invalid.")
    values = {
        **existing,
        "HERMES_TELEGRAM_CLIENT_ID": client_id,
        "HERMES_TELEGRAM_CLIENT_SECRET": secret,
        "HERMES_TELEGRAM_OWNER_USERNAME": owner_username,
        "HERMES_TELEGRAM_ALLOWED_ID": owner_id,
    }
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(target.with_suffix(".env.lock"), flags, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        latest = (
            parse_environment(target.read_text(encoding="utf-8"), raw=raw_format)
            if target.exists()
            else {}
        )
        # Preserve invitations added while this interactive prompt was open.
        values["HERMES_TELEGRAM_ALLOWED_IDS"] = latest.get("HERMES_TELEGRAM_ALLOWED_IDS", "")
        write_environment(target, values, raw=docker_deployment)
        if docker_deployment:
            format_marker.write_text("raw\n", encoding="utf-8")
            format_marker.chmod(0o600)
    if docker_deployment:
        subprocess.run(
            [
                "bash",
                "/opt/agent-council/scripts/compose-server.sh",
                "up",
                "-d",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "120",
                "hermes",
            ],
            check=True,
        )
    else:
        subprocess.run(["systemctl", "restart", "hermes-dashboard.service"], check=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection("127.0.0.1", 9119, timeout=2)
        try:
            connection.request(
                "GET", "/api/status", headers={"Host": origin.removeprefix("https://")}
            )
            response = connection.getresponse()
            status = json.loads(response.read())
            if (
                response.status == 200
                and status.get("auth_required") is True
                and status.get("auth_providers") == ["telegram-owner"]
            ):
                Path("/var/www/hermes-status/setup-pending").unlink(missing_ok=True)
                break
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(1)
    else:
        raise SystemExit(
            "Credentials saved, but Hermes did not become ready; public access remains closed."
        )
    print("Saved privately. Hermes restarted. Open " + origin)
    print("Only the configured owner and explicitly invited Telegram IDs can sign in.")


if __name__ == "__main__":
    main()
