"""Activate daily navigation and inspect freshness independently of the worker."""

import json
import sqlite3
import time
from contextlib import closing

from .profile_setup import configure as provision


def configure(runtime):
    return provision(
        runtime,
        capability="workspace-index-v1",
        script="provision-workspace-index.py",
        result_key="workspace_index_configured",
        probe="import json; from council_workspace.client import call; assert json.loads(call({'action':'index_status'}))['configured']; print('Daily workspace index configured')",
    )


def status(settings):
    marker = settings.data_dir / ".council/workspace/index-config.json"
    if not marker.exists():
        return {"configured": False}
    try:
        path = settings.data_dir / ".council/workspace/receipts.sqlite3"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT body FROM navigation_state WHERE key='index'").fetchone()
        value = json.loads(row[0]) if row else {}
        last = value.get("last_success", 0)
        age = time.time() - (last or marker.stat().st_mtime)
        return {
            "configured": True,
            "available": True,
            "last_success": last,
            "stale": age > 26 * 3600,
            "failed": bool(value.get("error")),
            "dirty": value.get("dirty", False),
        }
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return {"configured": True, "available": False}
