"""
SILA server entry point.

Binds exclusively to 127.0.0.1. Prints the session token to stdout once on
startup so the UI and test harness can pick it up. Never logged.
"""

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sila.api.routes import router, startup as routes_startup, last_ping_age, shutdown as routes_shutdown
from sila.security import generate_session_token

_PORT = 8765
_HEARTBEAT_TIMEOUT = 30.0   # shut down if no browser ping for this many seconds
# beforeunload+sendBeacon stops audio immediately on normal tab close, so the
# watchdog is now only a crash fallback.  30 s is accepted with the knowledge
# that Chrome's background-tab throttle (~60 s) could trigger a spurious
# shutdown if the user leaves the tab for >30 s without closing it.
_HEARTBEAT_POLL = 5.0       # how often the watchdog checks


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
            # Stop clock and audio explicitly before killing so cleanup runs
            # even on Windows where os.kill(SIGTERM) = TerminateProcess and
            # the lifespan finally-block never executes.
            routes_shutdown()
            os.kill(os.getpid(), signal.SIGTERM)
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    routes_startup()
    # --open: launch the browser at the token URL once the server is up. The
    # token lives in the URL hash (#token=), which app.js reads then stores.
    # Done in a daemon thread so webbrowser.open() never blocks startup. Read
    # from the env (set by main()) so it survives uvicorn's app re-import.
    if os.environ.get("SILA_OPEN") == "1":
        url = f"http://127.0.0.1:{_PORT}/#token={generate_session_token()}"
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
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


@app.middleware("http")
async def _no_cache_ui(request, call_next):
    """Tell the browser to revalidate UI assets every load.

    Without this, browsers cache app.js/index.html aggressively, so a code
    change only shows after a manual hard-refresh — a recurring source of
    "I don't see it". no-cache (not no-store) still lets the server answer 304
    when nothing changed, so it stays cheap on localhost.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".html", ".css")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

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
    parser = argparse.ArgumentParser(
        prog="sila", description="SILA step sequencer server"
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Open SILA in your default browser (at the token URL) once it's up",
    )
    args = parser.parse_args()

    _kill_port(_PORT)
    token = generate_session_token()
    # Print token once for the UI/harness to read. Not logged anywhere else.
    print(f"SILA_TOKEN={token}", flush=True)
    if args.open:
        # Signal the lifespan hook (which runs in the re-imported app module).
        os.environ["SILA_OPEN"] = "1"
        print(f"Opening http://127.0.0.1:{_PORT}/ in your browser…", flush=True)
    uvicorn.run(
        "sila.main:app",
        host="127.0.0.1",  # never 0.0.0.0
        port=_PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
