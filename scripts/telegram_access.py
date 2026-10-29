#!/usr/bin/env python3
"""Manage a small Telegram allowlist without printing or replacing credentials."""

import argparse
import fcntl
import os
import subprocess
import sys
from pathlib import Path

from env_utils import parse_environment, write_environment

CONFIG = Path("/etc/hermes/telegram.env")
COMPOSE = "/opt/agent-council/scripts/compose-server.sh"
ACCESS_KEY = "HERMES_TELEGRAM_ALLOWED_IDS"


def updated_environment(values, action, account_ids):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from telegram_auth.owner import parse_telegram_ids, telegram_id

    allowed = set(parse_telegram_ids(values.get(ACCESS_KEY, "")))
    requested = {telegram_id(account_id) for account_id in account_ids}
    if action == "add":
        allowed.update(requested)
    elif action == "remove":
        allowed.difference_update(requested)
    else:
        raise ValueError("Expected add or remove.")
    return {**values, ACCESS_KEY: ",".join(str(value) for value in sorted(allowed))}


def show_access(values):
    owner = values.get("HERMES_TELEGRAM_ALLOWED_ID", "")
    if not owner:
        username = values.get("HERMES_TELEGRAM_OWNER_USERNAME", "")
        owner = "@" + username.removeprefix("@") if username else "not configured"
    print("Owner (managed separately): " + owner)
    print("Invited Telegram IDs: " + (values.get(ACCESS_KEY, "") or "none"))


def restart_hermes():
    return subprocess.run(
        [
            "bash",
            COMPOSE,
            "up",
            "-d",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "120",
            "hermes",
        ],
        check=False,
    ).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "add", "remove"))
    parser.add_argument("ids", nargs="*")
    arguments = parser.parse_args()
    if (arguments.action == "list" and arguments.ids) or (
        arguments.action != "list" and not arguments.ids
    ):
        parser.error("Use list without IDs, or add/remove with one or more IDs.")
    if os.geteuid() != 0:
        parser.error("Run on the deployment server as root.")
    marker = CONFIG.with_suffix(".env.format")
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != "raw":
        parser.error("Upgrade the deployment and run migrate-auth-environment.py first.")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(CONFIG.with_suffix(".env.lock"), flags, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = parse_environment(CONFIG.read_text(encoding="utf-8"), raw=True)
        if arguments.action == "list":
            show_access(previous)
            return
        try:
            updated = updated_environment(previous, arguments.action, arguments.ids)
        except ValueError as error:
            parser.error(str(error))
        if updated.get(ACCESS_KEY, "") == previous.get(ACCESS_KEY, ""):
            show_access(previous)
            print("No change; Hermes was not restarted.")
            return
        write_environment(CONFIG, updated, raw=True)
        if restart_hermes() != 0:
            write_environment(CONFIG, previous, raw=True)
            recovery = restart_hermes()
            raise SystemExit(
                "Update failed; previous access settings restored. "
                + ("Hermes recovered." if recovery == 0 else "Check Hermes health on the server.")
            )
        show_access(updated)
        print("Applied. Owner access is unchanged; existing connections were restarted.")


if __name__ == "__main__":
    main()
