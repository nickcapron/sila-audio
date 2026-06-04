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


def test_small_speaker_toggle_sets_engine_flag(client):
    """The small-speaker route flips the live audio-engine flag both ways."""
    import sila.api.routes as routes_mod
    assert routes_mod._state.audio_engine.small_speaker is False

    r = client.put("/api/sequencer/small-speaker?active=true", headers=_h())
    assert r.status_code == 200 and r.json() == {"small_speaker": True}
    assert routes_mod._state.audio_engine.small_speaker is True

    r = client.put("/api/sequencer/small-speaker?active=false", headers=_h())
    assert r.status_code == 200 and r.json() == {"small_speaker": False}
    assert routes_mod._state.audio_engine.small_speaker is False


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

# ---------------------------------------------------------------------------
# Startup project-loading behaviour
# ---------------------------------------------------------------------------

def test_startup_loads_most_recent_project_for_returning_user(tmp_path, monkeypatch):
    """Returning user: startup() must load the most recently saved project."""
    import sila.storage.project_store as ps
    import sila.library.browser as lb
    from sila.api.routes import AppState

    monkeypatch.setattr(ps, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(lb, "LIBRARY_ROOT",  tmp_path / "library")

    # Simulate saved work from a previous session
    seed_store = ps.ProjectStore()
    seed_store.new_project("MySavedProject")

    # Server restart — fresh AppState + startup()
    state = AppState()
    state.startup()

    assert state.store.project.name == "MySavedProject", (
        "startup() did not restore the most recently saved project"
    )


def test_startup_creates_untitled_for_first_time_user(tmp_path, monkeypatch):
    """First-time user: startup() must create an 'Untitled' project automatically."""
    import sila.storage.project_store as ps
    import sila.library.browser as lb
    from sila.api.routes import AppState

    monkeypatch.setattr(ps, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(lb, "LIBRARY_ROOT",  tmp_path / "library")

    # No projects on disk at all
    state = AppState()
    state.startup()

    assert state.store.project.name == "Untitled", (
        "startup() must create an Untitled project for first-time users"
    )
    assert (tmp_path / "projects" / "Untitled" / "project.json").exists(), (
        "the default project must be persisted so it survives the next restart"
    )


def test_startup_project_always_accessible_via_api(client):
    """GET /project must return 200 after startup — 'No project loaded' must never happen."""
    resp = client.get("/api/project", headers=_h())
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"], "project name must be non-empty"
    assert isinstance(data["tracks"], list)


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


def test_new_project_clears_song_mode_and_chain(client):
    """A freshly created project must not inherit song-mode state from the
    previous one — the client re-syncs the song bar from this contract."""
    _new_project(client, "WithSong")
    client.put("/api/song/mode?active=true", headers=_h())
    client.put("/api/song/chain", json={"chain": [0, 2, 1]}, headers=_h())

    # Create a brand-new project; it must report a clean song state.
    client.post("/api/projects", json={"name": "Fresh"}, headers=_h())
    patterns = client.get("/api/patterns", headers=_h()).json()
    assert patterns["song_mode"] is False
    assert patterns["chain"] == []
    assert patterns["slots_used"] == []


def test_rename_project_changes_listing_and_name(client):
    """Renaming moves the folder, updates the stored name, and (since it's the
    open project) updates the in-memory project too."""
    _new_project(client, "OldName")
    r = client.put("/api/projects/OldName/rename", json={"new_name": "NewName"}, headers=_h())
    assert r.status_code == 200 and r.json()["new_name"] == "NewName"

    names = client.get("/api/projects", headers=_h()).json()["projects"]
    assert "NewName" in names and "OldName" not in names
    # The open project's name updated, and loading by the new name works.
    assert client.get("/api/project", headers=_h()).json()["name"] == "NewName"
    loaded = client.post("/api/project/load", json={"name": "NewName"}, headers=_h())
    assert loaded.status_code == 200 and loaded.json()["name"] == "NewName"


def test_rename_project_collision_returns_409(client):
    _new_project(client, "Alpha")
    _new_project(client, "Beta")
    r = client.put("/api/projects/Alpha/rename", json={"new_name": "Beta"}, headers=_h())
    assert r.status_code == 409


def test_rename_missing_project_returns_404(client):
    _new_project(client, "Real")
    r = client.put("/api/projects/Ghost/rename", json={"new_name": "Whatever"}, headers=_h())
    assert r.status_code == 404


def test_rename_empty_name_returns_400(client):
    _new_project(client, "Keep")
    r = client.put("/api/projects/Keep/rename", json={"new_name": "***"}, headers=_h())
    assert r.status_code == 400


def test_bpm_change_is_persisted_in_project(client):
    _new_project(client)
    client.put("/api/project/bpm", json={"bpm": 140.0}, headers=_h())
    project = client.get("/api/project", headers=_h()).json()
    assert project["bpm"] == 140.0


# ---------------------------------------------------------------------------
# /projects management endpoints
# ---------------------------------------------------------------------------

def test_list_projects_includes_default_after_startup(client):
    """Startup always creates 'Untitled' on first run, so the list is never empty."""
    resp = client.get("/api/projects", headers=_h())
    assert resp.status_code == 200
    projects = resp.json()["projects"]
    # The default "Untitled" project created by startup must be present
    assert "Untitled" in projects


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


# ---------------------------------------------------------------------------
# + TRACK regression tests
# ---------------------------------------------------------------------------

def test_add_track_returns_valid_track_with_correct_defaults(client):
    """POST /tracks must return a fully-formed track so the UI can render it."""
    _new_project(client)
    resp = client.post("/api/tracks", json={"name": "Kick", "step_count": 16}, headers=_h())
    assert resp.status_code == 200
    t = resp.json()
    # Identity and naming
    assert t["id"]   # non-empty UUID
    assert t["name"] == "Kick"
    # Steps initialised to the requested count — this is what the UI renders
    assert len(t["steps"]) == 16
    assert all(s["active"] is False for s in t["steps"])
    # Default FX values (sliders must not be None in the UI)
    assert t["fx"]["volume"]           == 1.0
    assert t["fx"]["pan"]              == 0.0
    assert t["fx"]["filter_cutoff"]    == 1.0
    assert t["fx"]["filter_resonance"] == 0.0
    # Default LFO present
    assert t["lfo"]["shape"]       == "sine"
    assert t["lfo"]["destination"] == "volume"
    # Structural fields used by the inspector
    assert t["muted"] is False
    assert t["solo"]  is False
    assert isinstance(t["color"], str)   # palette color assigned
    assert t["humanize"] == 0.0


def test_add_multiple_tracks_each_has_unique_id(client):
    """Every added track must have a unique ID and sequential naming."""
    _new_project(client)
    tracks = [_add_track(client, f"T{i}") for i in range(4)]
    ids    = [t["id"] for t in tracks]
    assert len(set(ids)) == 4, "duplicate track IDs — UI would render the wrong track"
    # Project must contain all four tracks
    proj = client.get("/api/project", headers=_h()).json()
    proj_ids = {t["id"] for t in proj["tracks"]}
    assert set(ids) <= proj_ids


def test_add_track_autosaves_so_it_survives_reload(client):
    """Adding a track must persist without an explicit Save click."""
    _new_project(client, "P")
    track = _add_track(client, "AutoSaved")
    # Force-reload from disk by loading the project again
    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    assert any(t["id"] == track["id"] for t in reloaded["tracks"]), (
        "track was lost on reload — autosave after add_track is broken"
    )


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


# ---------------------------------------------------------------------------
# Auto-save persistence (no explicit Save click required)
# ---------------------------------------------------------------------------

def test_step_toggle_persists_without_explicit_save(client):
    """Toggling a step must survive a project reload with no manual Save."""
    _new_project(client, "P")
    track = _add_track(client)

    step = {"active": True, "velocity": 100, "pitch_offset": 0,
            "probability": 100, "trig_condition": "always", "p_locks": {}}
    client.put(f"/api/tracks/{track['id']}/steps/0", json={"step": step}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["steps"][0]["active"] is True


def test_step_velocity_persists(client):
    """Velocity edited via inspector must survive a project reload."""
    _new_project(client, "P")
    track = _add_track(client)

    step = {"active": True, "velocity": 64, "pitch_offset": 0,
            "probability": 100, "trig_condition": "always", "p_locks": {}}
    client.put(f"/api/tracks/{track['id']}/steps/0", json={"step": step}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["steps"][0]["velocity"] == 64


def test_step_pitch_offset_persists(client):
    """Pitch offset edited via inspector must survive a project reload."""
    _new_project(client, "P")
    track = _add_track(client)

    step = {"active": True, "velocity": 100, "pitch_offset": -7,
            "probability": 100, "trig_condition": "always", "p_locks": {}}
    client.put(f"/api/tracks/{track['id']}/steps/0", json={"step": step}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["steps"][0]["pitch_offset"] == -7


def test_step_probability_persists(client):
    """Probability edited via inspector must survive a project reload."""
    _new_project(client, "P")
    track = _add_track(client)

    step = {"active": True, "velocity": 100, "pitch_offset": 0,
            "probability": 50, "trig_condition": "always", "p_locks": {}}
    client.put(f"/api/tracks/{track['id']}/steps/0", json={"step": step}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["steps"][0]["probability"] == 50


def test_step_trig_condition_persists(client):
    """Trig condition edited via inspector must survive a project reload."""
    _new_project(client, "P")
    track = _add_track(client)

    step = {"active": True, "velocity": 100, "pitch_offset": 0,
            "probability": 100, "trig_condition": "1:2", "p_locks": {}}
    client.put(f"/api/tracks/{track['id']}/steps/0", json={"step": step}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["steps"][0]["trig_condition"] == "1:2"


def test_step_micro_timing_persists(client):
    """Micro-timing edited via inspector must survive a project reload."""
    _new_project(client, "P")
    track = _add_track(client)

    step = {"active": True, "velocity": 100, "pitch_offset": 0,
            "probability": 100, "trig_condition": "always", "p_locks": {},
            "micro_timing": 5}
    client.put(f"/api/tracks/{track['id']}/steps/0", json={"step": step}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["steps"][0]["micro_timing"] == 5


def test_step_micro_timing_negative_persists(client):
    """Negative micro-timing (early trigger) must also round-trip correctly."""
    _new_project(client, "P")
    track = _add_track(client)

    step = {"active": True, "velocity": 100, "pitch_offset": 0,
            "probability": 100, "trig_condition": "always", "p_locks": {},
            "micro_timing": -7}
    client.put(f"/api/tracks/{track['id']}/steps/0", json={"step": step}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["steps"][0]["micro_timing"] == -7


def test_step_micro_timing_default_for_existing_projects(client):
    """Steps created without micro_timing (existing projects) default to 0."""
    _new_project(client, "P")
    track = _add_track(client)

    # Write a step without the micro_timing key — simulates a pre-feature project.
    step = {"active": True, "velocity": 100, "pitch_offset": 0,
            "probability": 100, "trig_condition": "always", "p_locks": {}}
    client.put(f"/api/tracks/{track['id']}/steps/0", json={"step": step}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["steps"][0].get("micro_timing", 0) == 0


def test_sample_assignment_persists_without_explicit_save(client, tmp_path):
    """Sample assignment must survive a project reload with no manual Save."""
    _new_project(client, "P")
    track = _add_track(client)

    # Plant a sample file in the project samples dir
    sample = tmp_path / "projects" / "P" / "samples" / "kick.wav"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_bytes(b"\x00" * 44)

    layer = {"path": "kick.wav", "velocity_min": 0, "velocity_max": 127,
             "start": 0.0, "end": 1.0, "loop": False, "rr_group": 0}
    client.put(f"/api/tracks/{track['id']}/samples",
               json={"samples": [layer]}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert len(t["samples"]) == 1
    assert t["samples"][0]["path"] == "kick.wav"


def test_library_relative_sample_path_is_copied_and_stored_as_filename(client, tmp_path):
    """Issue B: a library-relative path like 'Pack/Cat/kick.wav' must be copied
    into the project's samples/ dir and stored as just 'kick.wav'."""
    # Plant a sample file in the (patched) library
    lib_sample = tmp_path / "library" / "My Pack" / "Kicks" / "kick.wav"
    lib_sample.parent.mkdir(parents=True, exist_ok=True)
    lib_sample.write_bytes(b"\x00" * 44)

    _new_project(client, "P")
    track = _add_track(client)

    layer = {"path": "My Pack/Kicks/kick.wav",
             "velocity_min": 0, "velocity_max": 127,
             "start": 0.0, "end": 1.0, "loop": False, "rr_group": 0}
    resp = client.put(f"/api/tracks/{track['id']}/samples",
                      json={"samples": [layer]}, headers=_h())
    assert resp.status_code == 200

    # File must be copied into the project's own samples/ directory
    samples_dir = tmp_path / "projects" / "P" / "samples"
    assert (samples_dir / "kick.wav").exists(), (
        "file should be copied from library into project samples/"
    )

    # Stored path must be the bare filename, not the library-relative path
    project = client.get("/api/project", headers=_h()).json()
    t = next(t for t in project["tracks"] if t["id"] == track["id"])
    assert t["samples"][0]["path"] == "kick.wav", (
        "SampleLayer.path must be project-samples-relative after resolution"
    )


def test_library_relative_path_not_copied_if_already_in_samples(client, tmp_path):
    """If a file with the same name already exists in the project's samples/ dir,
    no copy is made and the bare filename is still stored."""
    # File exists in library AND already in project samples (e.g. a previous copy)
    lib_sample = tmp_path / "library" / "Pack" / "Cat" / "snare.wav"
    lib_sample.parent.mkdir(parents=True, exist_ok=True)
    lib_sample.write_bytes(b"\x01" * 44)

    _new_project(client, "P")
    track = _add_track(client)

    # Pre-place the file in the project's samples/ dir with different content
    proj_sample = tmp_path / "projects" / "P" / "samples" / "snare.wav"
    proj_sample.parent.mkdir(parents=True, exist_ok=True)
    proj_sample.write_bytes(b"\x02" * 44)

    layer = {"path": "Pack/Cat/snare.wav",
             "velocity_min": 0, "velocity_max": 127,
             "start": 0.0, "end": 1.0, "loop": False, "rr_group": 0}
    client.put(f"/api/tracks/{track['id']}/samples",
               json={"samples": [layer]}, headers=_h())

    # Existing file must NOT be overwritten
    assert proj_sample.read_bytes() == b"\x02" * 44, (
        "existing project sample must not be overwritten"
    )


def test_track_notes_persist_without_explicit_save(client):
    """Track notes must survive a project reload with no manual Save."""
    _new_project(client, "P")
    track = _add_track(client)

    client.put(f"/api/tracks/{track['id']}/notes",
               json={"notes": "four-on-the-floor kick"}, headers=_h())

    reloaded = client.post("/api/project/load", json={"name": "P"}, headers=_h()).json()
    t = next(t for t in reloaded["tracks"] if t["id"] == track["id"])
    assert t["notes"] == "four-on-the-floor kick"


def test_new_project_appears_in_list_without_explicit_save(client):
    """A newly created project must appear in GET /projects immediately."""
    _new_project(client, "BrandNew")
    projects = client.get("/api/projects", headers=_h()).json()["projects"]
    assert "BrandNew" in projects


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
