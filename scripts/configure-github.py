#!/usr/bin/env python3
"""Prepare and authorize the official GitHub MCP in the native owner profile."""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import traceback
import urllib.request
from pathlib import Path

URL = "https://api.githubcopilot.com/mcp/"
ENV_KEY = "MCP_GITHUB_API_KEY"
TOOLSETS = "context,repos,issues,pull_requests,actions"


class SetupError(ValueError):
    """Safe, credential-free operator message."""


def server_config(*, enabled):
    return {
        "url": URL,
        "enabled": enabled,
        "headers": {
            "Authorization": "Bearer ${MCP_GITHUB_API_KEY}",
            "X-MCP-Toolsets": TOOLSETS,
        },
        "connect_timeout": 30,
        "tools": {"prompts": False, "resources": False},
    }


def validate_existing(existing):
    if (
        existing is not None
        and existing != server_config(enabled=False)
        and existing != server_config(enabled=True)
    ):
        raise SetupError("An existing GitHub connection differs; reconcile it before replacement.")


def normalize_token(raw):
    token = raw.strip()
    if not re.fullmatch(r"github_pat_[A-Za-z0-9_]{20,250}", token):
        raise SetupError("Enter a fine-grained GitHub personal access token.")
    return token


def github_get(path, token):
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AgentCouncilSetup/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def verify_identity(token, owner_id, repositories):
    identity = github_get("/user", token)
    if identity.get("id") != owner_id:
        raise SetupError("The token belongs to another GitHub account; previous settings are kept.")
    for repo in repositories:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise SetupError("Expected owner/repository.")
        github_get(f"/repos/{repo}/contents/README.md", token)
        github_get(f"/repos/{repo}/pulls?state=open&per_page=1", token)
        github_get(f"/repos/{repo}/actions/runs?per_page=1", token)
    return identity["login"]


async def probe_mcp(token, repository):
    from tools.mcp_tool_discovery import _connect_server

    config = server_config(enabled=True)
    config["headers"]["Authorization"] = "Bearer " + token
    server = await _connect_server("github", config)
    try:
        tools = {tool.name for tool in server._tools}
        required = {"get_file_contents", "create_pull_request", "push_files"}
        if not required <= tools:
            raise SetupError("GitHub MCP did not expose the expected repository and PR tools.")
        owner, repo = repository.split("/", 1)
        result = await server.session.call_tool(
            "get_file_contents", {"owner": owner, "repo": repo, "path": "README.md"}
        )
        if not successful_read(result):
            raise SetupError("GitHub MCP could not read the verification repository.")
        return sorted(tools)
    finally:
        await server.shutdown()


def successful_read(result):
    # MCP SDK 2 uses snake_case; SDK 1 exposed the wire-format alias.
    error = getattr(result, "is_error", getattr(result, "isError", None))
    return error is False and bool(getattr(result, "content", None))


def backup(home):
    directory = home / ".council" / "github-setup"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = Path(tempfile.mkdtemp(prefix="before-", dir=directory))
    for name in ("config.yaml", ".env"):
        source = home / name
        if source.exists():
            shutil.copyfile(source, destination / name)
            (destination / name).chmod(0o600)
    return destination


def main():
    from hermes_cli.config import (
        get_env_value_prefer_dotenv,
        read_user_config_raw,
        remove_env_value,
    )
    from hermes_cli.mcp_config import _save_bearer_auth_token, _save_mcp_server
    from hermes_cli.mcp_security import validate_mcp_server_entry
    from hermes_constants import get_default_hermes_root

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "authorize", "verify"))
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--repository", action="append", required=True)
    args = parser.parse_args()
    home = Path(os.environ["HERMES_HOME"])
    if os.geteuid() == 0 or home.resolve() != get_default_hermes_root().resolve():
        raise SetupError("Run only as Hermes in the native owner profile.")
    for name in ("config.yaml", ".env"):
        if (home / name).is_symlink():
            raise SetupError("Refusing a symlinked profile configuration.")
    config = read_user_config_raw(home / "config.yaml")
    existing = config.get("mcp_servers", {}).get("github")
    validate_existing(existing)
    candidate = server_config(enabled=args.action != "prepare")
    # Validate a serialized copy before changing the live profile.
    if validate_mcp_server_entry("github", json.loads(json.dumps(candidate))):
        raise SetupError("The installed Hermes version rejected the GitHub configuration.")
    if args.action == "prepare":
        if existing is None:
            backup(home)
            if not _save_mcp_server("github", candidate):
                raise SetupError("GitHub configuration was not saved.")
        print(json.dumps({"prepared": True, "enabled": bool(existing and existing["enabled"])}))
        return
    token = normalize_token(
        sys.stdin.read(513)
        if args.action == "authorize"
        else get_env_value_prefer_dotenv(ENV_KEY) or ""
    )
    login = verify_identity(token, args.owner_id, args.repository)
    tools = asyncio.run(asyncio.wait_for(probe_mcp(token, args.repository[0]), timeout=90))
    if args.action == "authorize":
        backup(home)
        previous = get_env_value_prefer_dotenv(ENV_KEY)
        try:
            _save_bearer_auth_token("github", token)
            if not _save_mcp_server("github", candidate):
                raise SetupError("GitHub configuration was not saved.")
        except Exception:
            if previous:
                _save_bearer_auth_token("github", previous)
            else:
                remove_env_value(ENV_KEY)
            raise
    saved = read_user_config_raw(home / "config.yaml").get("mcp_servers", {}).get("github")
    if saved != server_config(enabled=True):
        raise SetupError("GitHub is not enabled in the saved profile.")
    print(
        json.dumps(
            {
                "ready": True,
                "account": login,
                "profile": "default",
                "repositories_verified": args.repository,
                "tools": tools,
                "writes_tested": False,
            }
        )
    )


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        main()
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # SDK/HTTP exception strings may carry request headers. Never print them.
        frames = traceback.extract_tb(exc.__traceback__)
        for frame in frames:
            print(
                f"Failure location: {Path(frame.filename).name}:{frame.lineno} ({frame.name}).",
                file=sys.stderr,
            )
        print(
            f"GitHub setup stopped ({type(exc).__name__}); check token permissions and connectivity.",
            file=sys.stderr,
        )
        sys.exit(1)
