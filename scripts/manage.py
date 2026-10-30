#!/usr/bin/env python3
"""Run versioned deployment operations on the Docker host."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from council_ops.cli import entrypoint

if __name__ == "__main__":
    entrypoint()
