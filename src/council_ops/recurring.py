"""Provision scheduling grants and inspect expected editions without model calls."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime

from council_recurring.contract import ZONE, occurrence


def configure(runtime):
    from .profile_setup import configure as provision

    return provision(
        runtime,
        capability="recurring-tasks-v1",
        script="provision-recurring.py",
        result_key="recurring_configured",
        probe="from council_recurring.client import call,home_path; assert 'definitions' in call(home_path(),{'action':'list'}); print('Recurring grant verified')",
    )


def status(settings, *, now=None):
    home = settings.data_dir
    marker = home / ".council/recurring/jobs.json"
    if not marker.exists():
        return {"configured": False}
    now = now or datetime.now(ZONE)
    try:
        entries = json.loads(marker.read_text())
        path = home / ".council/linear/bridge/linear.sqlite3"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            overdue = unreported = expected = 0
            for entry in entries.values():
                value = entry["definition"]
                if not entry["enabled"] or now.weekday() not in value["weekdays"]:
                    continue
                period = occurrence(value, now.date().isoformat())
                if now.timestamp() < period["deadline"] + 300:
                    continue
                expected += 1
                row = db.execute(
                    "SELECT state FROM recurring_runs WHERE key=?", (period["key"],)
                ).fetchone()
                receipts = db.execute(
                    "SELECT kind,state,execution FROM recurring_deliveries WHERE run=?",
                    (period["key"],),
                ).fetchall()
                delivered = set()
                for receipt in receipts:
                    if receipt["state"] == "delivered":
                        delivered.add(receipt["kind"])
                    elif receipt["state"] == "pending":
                        native = home / "cron/executions.db"
                        with closing(
                            sqlite3.connect(native.as_uri() + "?mode=ro", uri=True)
                        ) as cron:
                            outcome = cron.execute(
                                "SELECT status,delivery_outcome FROM executions WHERE id=?",
                                (receipt["execution"],),
                            ).fetchone()
                        if outcome == ("completed", "delivered"):
                            delivered.add(receipt["kind"])
                if row and (row["state"] == "delivered" or "report" in delivered):
                    continue
                overdue += 1
                if "alert" not in delivered:
                    unreported += 1
        return {
            "configured": True,
            "available": True,
            "expected": expected,
            "overdue": overdue,
            "unreported": unreported,
        }
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {"configured": True, "available": False}
