"""
Route-level integration tests using FastAPI's TestClient.

Each test verifies a specific API contract — not just a 200 status, but that
the response contains the right data or that server state changed correctly.

The `client` fixture provides an isolated server instance: project storage and
library root are redirected to tmp_path, and a fresh AppState is created per
test so clock / sequencer / project state never leaks between tests.
"""

import time

import pytest
from fastapi.testclient import TestClient

from sila.main import app
from sila.security import generate_session_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _h():
    return {"X-SILA-Token": generate_session_token()}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with isolated filesystem state and a fresh AppState per test."""
    import sila.storage.project_store as ps
    import sila.library.browser as lb
    import sila.api.routes as routes_mod
    from sila.api.routes import AppState

    monkeypatch.setattr(ps, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(lb, "LIBRARY_ROOT", tmp_path / "library")
    monkeypatch.setattr(routes_mod, "LIBRARY_ROOT", tmp_path / "library")

    fresh = AppState()
    monkeypatch.setattr(routes_mod, "_state", fresh)

    with TestClient(app) as c:
        yield c


def _new_project(client, name="P"):
    return client.post("/api/project/new", json={"name": name}, headers=_h()).json()


def _add_track(client, name="T", step_count=16):
    return client.post(
        "/api/tracks", json={"name": name, "step_count": step_count}, headers=_h()
    ).json()


def _mock_audio(client):
    """Replace audio engine start with a no-op so tests don't open a real device."""
    import sila.api.routes as routes_mod
    routes_mod._state.audio_engine.start = lambda: None


# ---------------------------------------------------------------------------
# Existing auth tests (no fixture — testing global behaviour)
# ---------------------------------------------------------------------------

def test_status_requires_token():
    with TestClient(app) as c:
        resp = c.get("/api/sequencer/status")
    assert resp.status_code == 422  # missing header → validation error


def test_status_rejects_wrong_token():
    with TestClient(app) as c:
        resp = c.get("/api/sequencer/status", headers={"X-SILA-Token": "wrong"})
    assert resp.status_code == 401


def test_status_not_playing_on_fresh_server():
    with TestClient(app) as c:
        resp = c.get("/api/sequencer/status", headers=_h())
    assert resp.status_code == 200
    data = resp.json()
    assert data["playing"] is False
    assert data["error"] is None
    assert data["bpm"] is None or isinstance(data["bpm"], float)


# ---------------------------------------------------------------------------
# AppState startup
# ---------------------------------------------------------------------------

def test_startup_sets_recent_last_ping(client):
    """startup() must set last_ping or the heartbeat watchdog fires immediately."""
    import sila.api.routes as routes_mod
    assert routes_mod._state.last_ping_age() < 5.0


def test_startup_creates_canonical_library_structure(client, tmp_path):
    """startup() calls ensure_my_samples() — spot-check two canonical dirs."""
    assert (tmp_path / "library" / "My Samples" / "01. Kick").is_dir()
    assert (tmp_path / "library" / "My Samples" / "59. Field Recording").is_dir()


# ---------------------------------------------------------------------------
# Project endpoints
# ---------------------------------------------------------------------------

def test_new_project_returns_model_with_correct_name(client):
    resp = client.post("/api/project/new", json={"name": "MyProject"}, headers=_h())
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "MyProject"
    assert isinstance(data["tracks"], list)


def test_get_project_reflects_current_state(client):
    _new_project(client, "TestProj")
    resp = client.get("/api/project", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["name"] == "TestProj"


def test_save_and_load_round_trip_preserves_tracks(client):
    """Changes made before save must survive a project reload from disk."""
    _new_project(client, "P")
    _add_track(client, "KickDrum")
    client.post("/api/project/save", headers=_h())

    # Clobber current state, then load P again
    _new_project(client, "Other")
    resp = client.post("/api/project/load", json={"name": "P"}, headers=_h())
    assert resp.status_code == 200
    assert any(t["name"] == "KickDrum" for t in resp.json()["tracks"])


def test_bpm_change_is_persisted_in_project(client):
    _new_project(client)
    client.put("/api/project/bpm", json={"bpm": 140.0}, headers=_h())
    project = client.get("/api/project", headers=_h()).json()
    assert project["bpm"] == 140.0


# ---------------------------------------------------------------------------
# /projects management endpoints
# ---------------------------------------------------------------------------

def test_list_projects_empty_before_any_save(client):
    resp = client.get("/api/projects", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["projects"] == []


def test_list_projects_includes_saved_project(client):
    _new_project(client, "Alpha")
    client.post("/api/project/save", headers=_h())
    projects = client.get("/api/projects", headers=_h()).json()["projects"]
    assert "Alpha" in projects


def test_create_project_sanitizes_name(client):
    """POST /projects must sanitize the name, not just pass it raw."""
    resp = client.post("/api/projects", json={"name": "My 808 Pack!"}, headers=_h())
    assert resp.status_code == 200
    assert resp.json()["name"] == "My_808_Pack"


def test_create_project_rejects_name_empty_after_sanitization(client):
    resp = client.post("/api/projects", json={"name": "!!!!"}, headers=_h())
    assert resp.status_code == 400


def test_load_named_project_returns_correct_model(client):
    _new_project(client, "SavedProj")
    client.post("/api/project/save", headers=_h())
    resp = client.put("/api/projects/SavedProj/load", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["name"] == "SavedProj"


def test_load_missing_project_returns_404(client):
    resp = client.put("/api/projects/ghost/load", headers=_h())
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Track endpoints
# ---------------------------------------------------------------------------

def test_add_track_creates_correct_number_of_steps(client):
    _new_project(client)
    resp = client.post("/api/tracks", json={"name": "HH", "step_count": 32}, headers=_h())
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "HH"
    assert len(data["steps"]) == 32


def test_remove_track_removes_it_from_project(client):
    _new_project(client)
    track = _add_track(client, "ToDelete")
    resp = client.delete(f"/api/tracks/{track['id']}", headers=_h())
    assert resp.status_code == 200
    project = client.get("/api/project", headers=_h()).json()
    assert not any(t["id"] == track["id"] for t in project["tracks"])


def test_mute_toggle_flips_state_correctly(client):
    """Two toggles must return True then False (round-trip, not a no-op)."""
    _new_project(client)
    track = _add_track(client)
    first = client.put(f"/api/tracks/{track['id']}/mute", headers=_h()).json()
    second = client.put(f"/api/tracks/{track['id']}/mute", headers=_h()).json()
    assert first["muted"] is True
    assert second["muted"] is False


# ---------------------------------------------------------------------------
# Sequencer start / stop
# ---------------------------------------------------------------------------

def test_sequencer_start_returns_bpm_and_start_time(client):
    """start response must carry the BPM and a non-null start timestamp."""
    _new_project(client)
    _mock_audio(client)

    resp = client.post("/api/sequencer/start", json={"bpm": 120}, headers=_h())
    assert resp.status_code == 200
    data = resp.json()
    assert data["bpm"] == 120.0
    assert data["started_at"] is not None

    client.post("/api/sequencer/stop", headers=_h())


def test_sequencer_status_reflects_play_and_stop(client):
    """Status must show playing=True after start and playing=False after stop."""
    _new_project(client)
    _mock_audio(client)

    client.post("/api/sequencer/start", json={"bpm": 120}, headers=_h())
    status = client.get("/api/sequencer/status", headers=_h()).json()
    assert status["playing"] is True
    assert status["bpm"] == 120.0

    client.post("/api/sequencer/stop", headers=_h())
    status = client.get("/api/sequencer/status", headers=_h()).json()
    assert status["playing"] is False


# ---------------------------------------------------------------------------
# Heartbeat / ping
# ---------------------------------------------------------------------------

def test_ping_resets_last_ping_age(client):
    """ping must update last_ping — if it doesn't the watchdog shuts down the server."""
    import sila.api.routes as routes_mod
    routes_mod._state.last_ping = time.monotonic() - 200.0  # simulate stale
    assert routes_mod._state.last_ping_age() > 100

    resp = client.post("/api/ping", headers=_h())
    assert resp.status_code == 200
    assert routes_mod._state.last_ping_age() < 5.0  # must be reset


# ---------------------------------------------------------------------------
# Library browser
# ---------------------------------------------------------------------------

def test_library_returns_pack_list(client):
    resp = client.get("/api/library", headers=_h())
    assert resp.status_code == 200
    assert isinstance(resp.json()["packs"], list)


def test_library_preview_returns_404_for_missing_file(client):
    resp = client.post(
        "/api/library/preview",
        json={"path": "My Samples/01. Kick/ghost.wav"},
        headers=_h(),
    )
    assert resp.status_code == 404


def test_library_preview_blocks_path_traversal(client):
    resp = client.post(
        "/api/library/preview",
        json={"path": "../../../etc/passwd"},
        headers=_h(),
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Import tool
# ---------------------------------------------------------------------------

def test_import_scan_rejects_relative_path(client):
    resp = client.post("/api/import/scan", json={"path": "relative/path"}, headers=_h())
    assert resp.status_code == 400


def test_import_scan_groups_files_and_attaches_suggestion(client, tmp_path):
    pack = tmp_path / "MyPack"
    (pack / "Kicks").mkdir(parents=True)
    (pack / "Kicks" / "kick.wav").write_bytes(b"\x00" * 44)

    resp = client.post("/api/import/scan", json={"path": str(pack)}, headers=_h())
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files"] == 1
    kick_group = next(g for g in data["groups"] if g["name"] == "Kicks")
    assert kick_group["suggestion"] == "01. Kick"


def test_import_execute_rejects_non_canonical_category(client, tmp_path):
    """The security fix: unknown category names must be rejected with 400."""
    pack = tmp_path / "Pack"
    (pack / "Kicks").mkdir(parents=True)
    (pack / "Kicks" / "kick.wav").write_bytes(b"\x00" * 44)

    resp = client.post(
        "/api/import/execute",
        json={
            "source_path": str(pack),
            "pack_name": "TestPack",
            "mappings": {"Kicks": "Not A Real Category"},
        },
        headers=_h(),
    )
    assert resp.status_code == 400
    assert "Invalid category" in resp.json()["detail"]


def test_import_execute_copies_files_to_library(client, tmp_path):
    """Files must appear under library/<pack>/<category>/ after a successful import."""
    pack = tmp_path / "Pack"
    (pack / "Kicks").mkdir(parents=True)
    (pack / "Kicks" / "kick.wav").write_bytes(b"\x00" * 44)

    resp = client.post(
        "/api/import/execute",
        json={
            "source_path": str(pack),
            "pack_name": "TestPack",
            "mappings": {"Kicks": "01. Kick"},
        },
        headers=_h(),
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] == 1
    assert (tmp_path / "library" / "TestPack" / "01. Kick" / "kick.wav").exists()
