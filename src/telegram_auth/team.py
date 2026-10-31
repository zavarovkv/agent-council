"""Serve the public team snapshot and fixed, image-owned portraits only."""

import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from hermes_cli.config import read_user_config_raw
from starlette.concurrency import run_in_threadpool

from council_team.view import AVATARS, snapshot

router = APIRouter(prefix="/auth/council")
lock = threading.Lock()
cached = None
expires = 0


def current():
    global cached, expires
    with lock:
        if cached is None or time.monotonic() >= expires:
            home = Path(os.environ["HERMES_HOME"])
            cached = snapshot(home, read_user_config_raw)
            expires = time.monotonic() + 300
        return cached


@router.get("/team")
async def team():
    return JSONResponse(await run_in_threadpool(current), headers={"Cache-Control": "no-store"})


@router.get("/avatars/{slug}")
async def avatar(slug: str):
    filename = AVATARS.get(slug)
    if filename is None:
        raise HTTPException(404, "Unknown portrait.")
    path = Path("/opt/agent-council/assets/agents") / filename
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Portrait unavailable.")
    return FileResponse(
        path, headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}
    )
