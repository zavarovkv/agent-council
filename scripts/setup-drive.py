#!/usr/bin/env python3
"""Connect Google services without putting credentials in SSH arguments."""

import argparse
import ipaddress
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

PORT = 8765
CONTAINER = "agent-council-hermes-1"


def ssh_options():
    return [
        "ssh",
        "-i",
        os.environ.get("COUNCIL_SSH_KEY", str(Path.home() / ".ssh/agent_council_ed25519")),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ConnectionAttempts=1",
    ]


def remote_command(action, services="drive"):
    # Only versioned public Python source enters the command line. Credentials
    # arrive on stdin; the OAuth callback arrives through the SSH tunnel.
    source = Path(__file__).with_name("configure-drive.py").read_text()
    return shlex.join(
        [
            "flock",
            "-n",
            "/etc/agent-council/operations.lock",
            "docker",
            "exec",
            "-i",
            "--user",
            "hermes",
            CONTAINER,
            "/opt/hermes/.venv/bin/python",
            "-u",
            "-c",
            source,
            action,
            "--services",
            services,
        ]
    )


def callback_address(networks):
    network = networks.get("agent-council_hermes", {})
    address = ipaddress.ip_address(network.get("IPAddress", ""))
    if address.version != 4 or not address.is_private:
        raise ValueError("Expected the private Hermes bridge address.")
    return address


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--services", default="drive", help="Comma-separated drive,calendar,gmail,analytics"
    )
    parser.add_argument(
        "--client-json", type=Path, help="Downloaded Desktop OAuth JSON; never copied to Git."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify", action="store_true", help="Only check the saved grant and workspace."
    )
    mode.add_argument(
        "--import-only", action="store_true", help="Save the client without starting consent."
    )
    args = parser.parse_args()
    if not set(args.services.split(",")) <= {"drive", "calendar", "gmail", "analytics"}:
        parser.error("Unknown Google service")
    if args.import_only and not args.client_json:
        parser.error("--import-only requires --client-json")
    host = os.environ.get("COUNCIL_SSH_HOST", "root@2.29.13.129")
    if host.startswith("-"):
        raise ValueError("Invalid SSH host.")
    if args.client_json:
        with args.client_json.expanduser().open("rb") as source:
            client = source.read(65537)
        if len(client) > 65536:
            raise ValueError("Client JSON exceeds the size limit.")
        subprocess.run(
            ssh_options() + ["-T", host, remote_command("import-client")], input=client, check=True
        )
    if args.import_only:
        return
    if args.verify:
        subprocess.run(
            ssh_options() + ["-T", host, remote_command("verify", args.services)], check=True
        )
        return
    inspect = shlex.join(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            CONTAINER,
        ]
    )
    result = subprocess.run(
        ssh_options() + ["-T", host, inspect], capture_output=True, text=True, check=True
    )
    address = callback_address(json.loads(result.stdout))
    forward = f"127.0.0.1:{PORT}:{address}:{PORT}"
    subprocess.run(
        ssh_options()
        + [
            "-T",
            "-o",
            "ExitOnForwardFailure=yes",
            "-L",
            forward,
            host,
            remote_command("authorize", args.services),
        ],
        check=True,
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Drive connection stopped ({type(exc).__name__}).", file=sys.stderr)
        sys.exit(1)
