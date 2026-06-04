"""
Project persistence — load/save JSON projects with backup-before-write
and undo/redo history.

Projects live at ~/SILA/projects/<name>/project.json.
Samples live at ~/SILA/projects/<name>/samples/.
"""

from __future__ import annotations

import json
import os
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
        self.autosave()  # make the project immediately visible to list_projects()
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

    def rename(self, old_name: str, new_name: str) -> Path:
        """Rename a project directory and its stored name field.

        Works whether or not the project is the one currently loaded. Raises
        FileNotFoundError if *old_name* has no project, FileExistsError if a
        different project already uses *new_name*. Returns the new directory.
        """
        old_dir = safe_path(PROJECTS_ROOT, old_name)
        if not (old_dir / "project.json").exists():
            raise FileNotFoundError(old_name)
        new_dir = safe_path(PROJECTS_ROOT, new_name)

        def _norm(p: Path) -> str:
            return os.path.normcase(os.path.normpath(str(p)))

        case_only = _norm(old_dir) == _norm(new_dir)  # e.g. "Test" -> "test"
        if new_dir.exists() and not case_only:
            raise FileExistsError(new_name)

        was_current = self._project_dir is not None and _norm(self._project_dir) == _norm(old_dir)

        if case_only:
            # Case-insensitive filesystems won't rename in place — go via a temp.
            tmp = old_dir.with_name(old_dir.name + ".__rename__")
            old_dir.rename(tmp)
            tmp.rename(new_dir)
        else:
            old_dir.rename(new_dir)

        # Update the name stored inside project.json so it matches the folder.
        json_path = new_dir / "project.json"
        proj = ProjectModel.model_validate_json(json_path.read_text(encoding="utf-8"))
        proj.name = new_name
        json_path.write_text(proj.model_dump_json(indent=2), encoding="utf-8")

        if was_current:
            self._project_dir = new_dir
            if self._project is not None:
                self._project.name = new_name
        return new_dir

    def save(self) -> Path:
        if self._project is None or self._project_dir is None:
            raise RuntimeError("No project loaded")
        json_path = self._project_dir / "project.json"
        backup_before_write(json_path)
        json_path.write_text(
            self._project.model_dump_json(indent=2), encoding="utf-8"
        )
        return json_path

    def autosave(self) -> None:
        """Write current project to disk without creating a backup.

        Called after every incremental mutation (step toggle, sample
        assignment, BPM change, …) so the project survives a restart without
        the user having to click Save.  Uses a temp-file rename for atomicity.
        Silent no-op when no project is loaded.
        """
        if self._project is None or self._project_dir is None:
            return
        json_path = self._project_dir / "project.json"
        tmp = json_path.with_suffix(".tmp")
        tmp.write_text(self._project.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(json_path)

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

    def list_projects(self) -> list[str]:
        """Return names of all saved projects, most recently modified first."""
        if not PROJECTS_ROOT.exists():
            return []
        return [
            p.parent.name
            for p in sorted(
                PROJECTS_ROOT.glob("*/project.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        ]

    def load_latest(self) -> ProjectModel | None:
        """Load the most recently saved project. Returns None if none exist."""
        names = self.list_projects()
        if not names:
            return None
        return self.load(names[0])

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)
