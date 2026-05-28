"""
Project persistence — load/save JSON projects with backup-before-write
and undo/redo history.

Projects live at ~/SILA/projects/<name>/project.json.
Samples live at ~/SILA/projects/<name>/samples/.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from sila.models.project import ProjectModel
from sila.security import backup_before_write, safe_path

PROJECTS_ROOT = Path.home() / "SILA" / "projects"
MAX_UNDO = 100


class ProjectStore:
    """
    Owns the current project and its undo/redo stacks.
    Serializes to/from JSON. All disk paths go through safe_path.
    """

    def __init__(self) -> None:
        self._project: ProjectModel | None = None
        self._project_dir: Path | None = None
        self._undo_stack: deque[str] = deque(maxlen=MAX_UNDO)
        self._redo_stack: deque[str] = deque(maxlen=MAX_UNDO)

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def new_project(self, name: str) -> ProjectModel:
        project_dir = safe_path(PROJECTS_ROOT, name)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "samples").mkdir(exist_ok=True)
        self._project_dir = project_dir
        self._project = ProjectModel(name=name)
        self._undo_stack.clear()
        self._redo_stack.clear()
        return self._project

    def load(self, name: str) -> ProjectModel:
        project_dir = safe_path(PROJECTS_ROOT, name)
        json_path = safe_path(project_dir, "project.json")
        raw = json_path.read_text(encoding="utf-8")
        self._project = ProjectModel.model_validate_json(raw)
        self._project_dir = project_dir
        self._undo_stack.clear()
        self._redo_stack.clear()
        return self._project

    def save(self) -> Path:
        if self._project is None or self._project_dir is None:
            raise RuntimeError("No project loaded")
        json_path = self._project_dir / "project.json"
        backup_before_write(json_path)
        json_path.write_text(
            self._project.model_dump_json(indent=2), encoding="utf-8"
        )
        return json_path

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def snapshot(self) -> None:
        """Push current project state onto the undo stack."""
        if self._project is None:
            return
        self._undo_stack.append(self._project.model_dump_json())
        self._redo_stack.clear()

    def undo(self) -> ProjectModel | None:
        if not self._undo_stack:
            return None
        # Save current state to redo before reverting.
        if self._project:
            self._redo_stack.append(self._project.model_dump_json())
        raw = self._undo_stack.pop()
        self._project = ProjectModel.model_validate_json(raw)
        return self._project

    def redo(self) -> ProjectModel | None:
        if not self._redo_stack:
            return None
        if self._project:
            self._undo_stack.append(self._project.model_dump_json())
        raw = self._redo_stack.pop()
        self._project = ProjectModel.model_validate_json(raw)
        return self._project

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def project(self) -> ProjectModel:
        if self._project is None:
            raise RuntimeError("No project loaded")
        return self._project

    @property
    def samples_dir(self) -> Path:
        if self._project_dir is None:
            raise RuntimeError("No project loaded")
        return self._project_dir / "samples"

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)
