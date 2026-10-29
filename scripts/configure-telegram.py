#!/usr/bin/env python3
"""Compatibility launcher for the previous server-side setup command."""

import os
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).with_name("configure_telegram.py")
    if not target.exists():
        target = Path("/opt/agent-council/scripts/configure_telegram.py")
    os.execv("/usr/bin/python3", ["python3", str(target)])
