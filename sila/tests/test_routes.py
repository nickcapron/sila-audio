"""
Route-level tests for the sequencer status endpoint.

Uses FastAPI's TestClient so no real server process is needed.
"""

from fastapi.testclient import TestClient

from sila.main import app
from sila.security import generate_session_token


def _client():
    return TestClient(app)


def _headers():
    return {"X-SILA-Token": generate_session_token()}


# ---------------------------------------------------------------------------
# GET /sequencer/status
# ---------------------------------------------------------------------------

def test_status_not_playing_on_fresh_server():
    resp = _client().get("/api/sequencer/status", headers=_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["playing"] is False
    assert data["error"] is None
    assert data["bpm"] is None or isinstance(data["bpm"], float)


def test_status_requires_token():
    resp = _client().get("/api/sequencer/status")
    assert resp.status_code == 422  # missing header → validation error


def test_status_rejects_wrong_token():
    resp = _client().get("/api/sequencer/status", headers={"X-SILA-Token": "wrong"})
    assert resp.status_code == 401
