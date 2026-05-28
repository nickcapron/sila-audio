"""
SILA server entry point.

Binds exclusively to 127.0.0.1. Prints the session token to stdout once on
startup so the UI and test harness can pick it up. Never logged.
"""

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from vdigitakt.api.routes import router
from vdigitakt.security import generate_session_token

app = FastAPI(title="SILA", version="0.1.0")
app.include_router(router, prefix="/api")

# Serve the UI from the ui/ directory.
import pathlib
_UI_DIR = pathlib.Path(__file__).parent / "ui"
app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


def main() -> None:
    token = generate_session_token()
    # Print token once for the UI/harness to read. Not logged anywhere else.
    print(f"SILA_TOKEN={token}", flush=True)
    uvicorn.run(
        "vdigitakt.main:app",
        host="127.0.0.1",  # never 0.0.0.0
        port=8765,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
