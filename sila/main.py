"""
SILA server entry point.

Binds exclusively to 127.0.0.1. Prints the session token to stdout once on
startup so the UI and test harness can pick it up. Never logged.
"""

import asyncio
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sila.api.routes import router, startup as routes_startup, last_ping_age, shutdown as routes_shutdown
from sila.security import generate_session_token

_PORT = 8765
_HEARTBEAT_TIMEOUT = 120.0  # shut down if no browser ping for this many seconds
# 120 s gives headroom for Chrome's background-tab timer throttling (~1 min)
# while still cleaning up if the user genuinely closes the app.
_HEARTBEAT_POLL = 5.0      # how often the watchdog checks


def _should_watchdog_fire() -> bool:
    """Return True when the browser has been silent long enough to shut down.

    Extracted from the async loop so it can be unit-tested synchronously.
    """
    return last_ping_age() > _HEARTBEAT_TIMEOUT


def _kill_port(port: int) -> None:
    """Kill any process already listening on *port*. Silent on any failure."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                cols = line.split()
                if len(cols) >= 5 and f":{port}" in cols[1] and cols[3] == "LISTENING":
                    pid = int(cols[4])
                    if pid > 0:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            capture_output=True, timeout=5,
                        )
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=5,
            )
            for pid_str in result.stdout.split():
                pid = int(pid_str.strip())
                if pid > 0:
                    os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


async def _heartbeat_watchdog() -> None:
    """Shut down the server when the browser stops pinging (tab/window closed)."""
    while True:
        await asyncio.sleep(_HEARTBEAT_POLL)
        if _should_watchdog_fire():
            os.kill(os.getpid(), signal.SIGTERM)
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    routes_startup()
    task = asyncio.create_task(_heartbeat_watchdog())
    yield
    # Clean shutdown: stop clock first, then audio engine, before the process exits.
    routes_shutdown()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="SILA", version="0.1.0", lifespan=lifespan)
app.include_router(router, prefix="/api")

# Serve the UI from the ui/ directory.
import pathlib
_UI_DIR = pathlib.Path(__file__).parent / "ui"

# Explicit route so /import serves the import tool page, not index.html.
# Must be registered before the catch-all StaticFiles mount.
@app.get("/import", include_in_schema=False)
async def import_page() -> FileResponse:
    return FileResponse(str(_UI_DIR / "import.html"))

app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


def main() -> None:
    _kill_port(_PORT)
    token = generate_session_token()
    # Print token once for the UI/harness to read. Not logged anywhere else.
    print(f"SILA_TOKEN={token}", flush=True)
    uvicorn.run(
        "sila.main:app",
        host="127.0.0.1",  # never 0.0.0.0
        port=_PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
