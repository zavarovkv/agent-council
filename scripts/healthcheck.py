#!/usr/bin/env python3
"""Read-only readiness probe: auth gate, supervised process and fresh gateway state."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def check():
    origin = urlsplit(os.environ["HERMES_PUBLIC_URL"])
    if origin.scheme != "https" or not origin.hostname:
        raise ValueError("An HTTPS origin is required.")
    headers = {"Host": origin.netloc}
    request = urllib.request.Request("http://127.0.0.1:9119/api/status", headers=headers)
    with urllib.request.urlopen(request, timeout=3) as response:
        data = json.load(response)
    if data.get("auth_required") is not True or data.get("auth_providers") != ["telegram-owner"]:
        raise ValueError("The owner auth gate is not ready.")
    private = urllib.request.Request("http://127.0.0.1:9119/api/config", headers=headers)
    try:
        urllib.request.urlopen(private, timeout=3)
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise ValueError("Unexpected private API response.") from None
    else:
        raise ValueError("Private API accepted an unauthenticated request.")
    if (
        subprocess.check_output(
            ["/command/s6-svstat", "-o", "up", "/run/service/gateway-default"], timeout=2
        ).strip()
        != b"true"
    ):
        raise ValueError("The gateway supervisor is not running.")
    from gateway.status import read_runtime_status, runtime_status_is_stale

    state = read_runtime_status()
    if not state or runtime_status_is_stale(state) or state.get("gateway_state") != "running":
        raise ValueError("The gateway is not ready.")
    pid = state.get("pid")
    if type(pid) is not int or pid <= 1:
        raise ValueError("The gateway PID is invalid.")
    os.kill(pid, 0)


if __name__ == "__main__":
    try:
        check()
    except Exception:
        # Docker retains health output; never include URLs, env values or raw provider errors.
        print("Hermes readiness check failed.", file=sys.stderr)
        sys.exit(1)
