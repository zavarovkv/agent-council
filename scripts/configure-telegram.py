#!/usr/bin/env python3
"""Read Telegram OIDC credentials privately over an interactive SSH terminal."""

import getpass
import http.client
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run this script as root on the Hermes server.")
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
    values = {
        "HERMES_TELEGRAM_CLIENT_ID": client_id,
        "HERMES_TELEGRAM_CLIENT_SECRET": secret,
        "HERMES_TELEGRAM_OWNER_USERNAME": "zavarov",
        "HERMES_TELEGRAM_ALLOWED_ID": "",
    }
    target = Path("/etc/hermes/telegram.env")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=".telegram-", delete=False
        ) as output:
            temporary = Path(output.name)
            for key, value in values.items():
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                output.write(f'{key}="{escaped}"\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
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
    print("Only @zavarov can complete the first login; its account ID will then be pinned.")


if __name__ == "__main__":
    main()
