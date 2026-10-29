#!/usr/bin/env python3
"""Adapt a copied profile using the YAML library inside the official image."""

import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def main():
    from ruamel.yaml import YAML

    config_path = Path(sys.argv[1])
    public_url = sys.argv[2]
    if urlsplit(public_url).scheme != "https" or not urlsplit(public_url).hostname:
        raise SystemExit("An HTTPS public URL is required.")
    yaml = YAML()
    config = yaml.load(config_path.read_text(encoding="utf-8")) or {}
    dashboard = config.setdefault("dashboard", {})
    dashboard["public_url"] = public_url
    # Nginx connects through the dedicated Docker bridge's host gateway.
    dashboard["trusted_proxies"] = ["172.30.90.1", "127.0.0.1", "::1"]
    terminal = config.setdefault("terminal", {})
    terminal["backend"] = "local"
    terminal["cwd"] = "/workspace"
    plugins = config.setdefault("plugins", {})
    disabled = set(plugins.get("disabled") or [])
    disabled.update(
        [
            "dashboard_auth/basic",
            "dashboard_auth/nous",
            "dashboard_auth/self_hosted",
            "basic",
            "nous",
            "self-hosted",
        ]
    )
    plugins["disabled"] = sorted(disabled)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=".docker-config-",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            yaml.dump(config, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, config_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print("Docker profile prepared; owner-only authentication remains enabled.")


if __name__ == "__main__":
    main()
