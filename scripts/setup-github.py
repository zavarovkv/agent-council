#!/usr/bin/env python3
"""Connect native Hermes to GitHub; the token travels only over SSH stdin."""

import argparse
import getpass
import os
import shlex
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path

REPOSITORIES = (
    "zavarovkv/agent-council",
    "zavarovkv/agent-council-skills",
    "zavarovkv/zavarov.com",
)


def token_url():
    return "https://github.com/settings/personal-access-tokens/new?" + urllib.parse.urlencode(
        {
            "name": "Agent Council MCP",
            "target_name": "zavarovkv",
            "expires_in": "90",
            "description": "Qui-Gon: code, issues, pull requests and CI for selected repositories",
            "contents": "write",
            "pull_requests": "write",
            "issues": "write",
            "actions": "write",
            "workflows": "write",
        }
    )


def command(action, repositories):
    source = Path(__file__).with_name("configure-github.py").read_text()
    remote = [
        "flock",
        "-w",
        "30",
        "/etc/agent-council/operations.lock",
        "docker",
        "exec",
        "-i",
        "--user",
        "hermes",
        "--workdir",
        "/opt/hermes",
        "agent-council-hermes-1",
        "/opt/hermes/.venv/bin/python",
        "-u",
        "-c",
        source,
        action,
        "--owner-id",
        "4748829",
    ]
    for repository in repositories:
        remote += ["--repository", repository]
    host = os.environ.get("COUNCIL_SSH_HOST", "root@2.29.13.129")
    if host.startswith("-"):
        raise ValueError("Invalid SSH host.")
    return [
        "ssh",
        "-T",
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
        host,
        shlex.join(remote),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--repository", action="append", help="Repository to verify; repeat as needed."
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Use a token already created on GitHub."
    )
    args = parser.parse_args()
    repositories = args.repository or REPOSITORIES
    action = "verify" if args.verify else "prepare"
    subprocess.run(command(action, repositories), input=b"", check=True)
    if args.prepare or args.verify:
        return
    if not sys.stdin.isatty():
        raise ValueError("Run in a local terminal for hidden token entry.")
    print("GitHub: choose Only select repositories and include:\n" + "\n".join(repositories))
    print(
        "The form requests code, PR, issue and CI access for 90 days. Create the token, then paste it below."
    )
    if not args.no_browser:
        webbrowser.open(token_url())
    token = getpass.getpass("GitHub token (hidden): ").strip()
    if not token:
        raise ValueError("No token entered.")
    subprocess.run(command("authorize", repositories), input=token.encode(), check=True)
    print(
        "GitHub connected. Start a new Hermes session, or send /reload-mcp in the private bot chat."
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, KeyboardInterrupt, EOFError) as exc:
        status = (
            f", exit {exc.returncode}" if isinstance(exc, subprocess.CalledProcessError) else ""
        )
        print(f"GitHub connection stopped ({type(exc).__name__}{status}).", file=sys.stderr)
        sys.exit(1)
