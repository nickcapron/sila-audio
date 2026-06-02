"""
SILA MCP bridge.

Exposes SILA's sequencer as an MCP (Model Context Protocol) server so that
Claude and other MCP clients can control the sequencer via tool calls.

Usage:
    python -m sila.mcp.bridge

The bridge reads SILA_TOKEN from the environment (or the printed startup line)
and connects to http://127.0.0.1:8765.  It implements the MCP stdio transport
so it can be wired directly into Claude Code's MCP config.

Supported tools (all map 1:1 to existing REST endpoints):
  sila_status          — GET /sequencer/status
  sila_play            — POST /sequencer/start  {bpm?}
  sila_stop            — POST /sequencer/stop
  sila_get_project     — GET /project
  sila_list_projects   — GET /projects
  sila_load_project    — PUT /projects/{name}/load
  sila_new_project     — POST /projects  {name}
  sila_set_bpm         — PUT /project/bpm  {bpm}
  sila_set_swing       — PUT /project/swing  {swing}
  sila_add_track       — POST /tracks  {name, step_count}
  sila_remove_track    — DELETE /tracks/{track_id}
  sila_toggle_step     — PUT /tracks/{track_id}/steps/{step_index}  {active}
  sila_set_track_mute  — PUT /tracks/{track_id}/mute
  sila_set_track_solo  — PUT /tracks/{track_id}/solo
  sila_randomize_track — POST /tracks/{track_id}/randomize  {density}
  sila_assign_sample   — PUT /tracks/{track_id}/samples  {path}
  sila_list_library    — GET /library
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


_BASE = "http://127.0.0.1:8765"


def _get_token() -> str:
    token = os.environ.get("SILA_TOKEN", "")
    if not token:
        raise RuntimeError(
            "SILA_TOKEN environment variable not set. "
            "Start SILA and copy the token from its startup output."
        )
    return token


def _call(method: str, path: str, body: dict | None = None) -> Any:
    token = _get_token()
    url = _BASE + "/api" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-SILA-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"SILA API {method} {path} → {e.code}: {body_text}") from e


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def sila_status() -> dict:
    return _call("GET", "/sequencer/status")


def sila_play(bpm: float | None = None) -> dict:
    return _call("POST", "/sequencer/start", {"bpm": bpm} if bpm else {})


def sila_stop() -> dict:
    return _call("POST", "/sequencer/stop")


def sila_get_project() -> dict:
    return _call("GET", "/project")


def sila_list_projects() -> dict:
    return _call("GET", "/projects")


def sila_load_project(name: str) -> dict:
    return _call("PUT", f"/projects/{urllib.parse.quote(name)}/load")


def sila_new_project(name: str) -> dict:
    return _call("POST", "/projects", {"name": name})


def sila_set_bpm(bpm: float) -> dict:
    return _call("PUT", "/project/bpm", {"bpm": bpm})


def sila_set_swing(swing: float) -> dict:
    return _call("PUT", "/project/swing", {"swing": swing})


def sila_add_track(name: str = "Track", step_count: int = 16) -> dict:
    return _call("POST", "/tracks", {"name": name, "step_count": step_count})


def sila_remove_track(track_id: str) -> dict:
    return _call("DELETE", f"/tracks/{track_id}")


def sila_toggle_step(track_id: str, step_index: int, active: bool) -> dict:
    # Fetch current step, flip active
    project = _call("GET", "/project")
    track = next((t for t in project["tracks"] if t["id"] == track_id), None)
    if track is None:
        raise RuntimeError(f"Track {track_id!r} not found")
    if step_index < 0 or step_index >= len(track["steps"]):
        raise RuntimeError(f"Step index {step_index} out of range")
    step = track["steps"][step_index]
    step["active"] = active
    return _call("PUT", f"/tracks/{track_id}/steps/{step_index}", {"step": step})


def sila_set_track_mute(track_id: str) -> dict:
    return _call("PUT", f"/tracks/{track_id}/mute")


def sila_set_track_solo(track_id: str) -> dict:
    return _call("PUT", f"/tracks/{track_id}/solo")


def sila_randomize_track(track_id: str, density: float = 0.5) -> dict:
    return _call("POST", f"/tracks/{track_id}/randomize", {"density": density})


def sila_assign_sample(track_id: str, path: str) -> dict:
    # Reject obviously bad paths before hitting the HTTP API.
    # The HTTP endpoint handles library-relative resolution, but we do not
    # want the MCP layer passing traversal attempts or absolute paths through.
    if ".." in path or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        raise ValueError(
            f"sila_assign_sample: path {path!r} must be a bare filename or "
            "library-relative path (e.g. 'kick.wav' or 'Pack/Cat/kick.wav'). "
            "Path separators indicating traversal (.. or absolute paths) are not allowed."
        )
    layer = {
        "path": path,
        "velocity_min": 0,
        "velocity_max": 127,
        "start": 0.0,
        "end": 1.0,
        "loop": False,
        "rr_group": 0,
    }
    return _call("PUT", f"/tracks/{track_id}/samples", {"samples": [layer]})


def sila_list_library() -> dict:
    return _call("GET", "/library")


# ---------------------------------------------------------------------------
# MCP schema
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "name": "sila_status",
        "description": "Get SILA sequencer status (playing, BPM, health).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "sila_play",
        "description": "Start playback. Optionally set BPM.",
        "inputSchema": {
            "type": "object",
            "properties": {"bpm": {"type": "number", "description": "Tempo in BPM (20-300)"}},
            "required": [],
        },
    },
    {
        "name": "sila_stop",
        "description": "Stop playback.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "sila_get_project",
        "description": "Return the full current project (tracks, steps, BPM, etc.).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "sila_list_projects",
        "description": "List all saved project names.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "sila_load_project",
        "description": "Load a saved project by name.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "sila_new_project",
        "description": "Create a new project with the given name.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "sila_set_bpm",
        "description": "Set the project BPM. Takes effect immediately during playback.",
        "inputSchema": {
            "type": "object",
            "properties": {"bpm": {"type": "number"}},
            "required": ["bpm"],
        },
    },
    {
        "name": "sila_set_swing",
        "description": "Set swing amount (0=straight, 1=full triplet swing).",
        "inputSchema": {
            "type": "object",
            "properties": {"swing": {"type": "number", "minimum": 0, "maximum": 1}},
            "required": ["swing"],
        },
    },
    {
        "name": "sila_add_track",
        "description": "Add a new track to the project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "step_count": {"type": "integer", "enum": [16, 32, 64, 128]},
            },
            "required": [],
        },
    },
    {
        "name": "sila_remove_track",
        "description": "Remove a track by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"track_id": {"type": "string"}},
            "required": ["track_id"],
        },
    },
    {
        "name": "sila_toggle_step",
        "description": "Set a step on a track active or inactive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_id": {"type": "string"},
                "step_index": {"type": "integer", "minimum": 0},
                "active": {"type": "boolean"},
            },
            "required": ["track_id", "step_index", "active"],
        },
    },
    {
        "name": "sila_set_track_mute",
        "description": "Toggle mute on a track.",
        "inputSchema": {
            "type": "object",
            "properties": {"track_id": {"type": "string"}},
            "required": ["track_id"],
        },
    },
    {
        "name": "sila_set_track_solo",
        "description": "Toggle solo on a track (solos it, un-solos all others).",
        "inputSchema": {
            "type": "object",
            "properties": {"track_id": {"type": "string"}},
            "required": ["track_id"],
        },
    },
    {
        "name": "sila_randomize_track",
        "description": "Randomize a track's step pattern with a musical density.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_id": {"type": "string"},
                "density": {"type": "number", "minimum": 0, "maximum": 1,
                            "description": "0=sparse, 0.5=medium, 1=dense"},
            },
            "required": ["track_id"],
        },
    },
    {
        "name": "sila_assign_sample",
        "description": (
            "Assign a sample file to a track. "
            "path must be a bare filename ('kick.wav') already in the project's samples/ "
            "directory, or a library-relative path ('Pack/Cat/kick.wav') which will be "
            "copied automatically. Traversal sequences (..) and absolute paths are rejected."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_id": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": (
                        "Bare filename ('kick.wav') or library-relative path "
                        "('Pack/Cat/kick.wav'). Must not contain '..' or start with '/'."
                    ),
                },
            },
            "required": ["track_id", "path"],
        },
    },
    {
        "name": "sila_list_library",
        "description": "Return the full sample library tree (packs → categories → samples).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

_TOOL_MAP = {
    "sila_status":          lambda a: sila_status(),
    "sila_play":            lambda a: sila_play(a.get("bpm")),
    "sila_stop":            lambda a: sila_stop(),
    "sila_get_project":     lambda a: sila_get_project(),
    "sila_list_projects":   lambda a: sila_list_projects(),
    "sila_load_project":    lambda a: sila_load_project(a["name"]),
    "sila_new_project":     lambda a: sila_new_project(a["name"]),
    "sila_set_bpm":         lambda a: sila_set_bpm(float(a["bpm"])),
    "sila_set_swing":       lambda a: sila_set_swing(float(a["swing"])),
    "sila_add_track":       lambda a: sila_add_track(a.get("name", "Track"), int(a.get("step_count", 16))),
    "sila_remove_track":    lambda a: sila_remove_track(a["track_id"]),
    "sila_toggle_step":     lambda a: sila_toggle_step(a["track_id"], int(a["step_index"]), bool(a["active"])),
    "sila_set_track_mute":  lambda a: sila_set_track_mute(a["track_id"]),
    "sila_set_track_solo":  lambda a: sila_set_track_solo(a["track_id"]),
    "sila_randomize_track": lambda a: sila_randomize_track(a["track_id"], float(a.get("density", 0.5))),
    "sila_assign_sample":   lambda a: sila_assign_sample(a["track_id"], a["path"]),
    "sila_list_library":    lambda a: sila_list_library(),
}


# ---------------------------------------------------------------------------
# MCP stdio transport
# ---------------------------------------------------------------------------

def _send(msg: dict) -> None:
    line = json.dumps(msg, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _handle(msg: dict) -> None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        _send({
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sila-mcp", "version": "0.1.0"},
            },
        })

    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _TOOLS}})

    elif method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        fn = _TOOL_MAP.get(name)
        if fn is None:
            _send({"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32601, "message": f"Unknown tool: {name}"}})
            return
        try:
            result = fn(args)
            _send({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    "isError": False,
                },
            })
        except Exception as exc:
            _send({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            })

    elif method == "notifications/initialized":
        pass  # no response needed

    else:
        if msg_id is not None:
            _send({"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32601, "message": f"Method not found: {method}"}})


def main() -> None:
    """Run the MCP bridge over stdio."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        _handle(msg)


if __name__ == "__main__":
    main()
