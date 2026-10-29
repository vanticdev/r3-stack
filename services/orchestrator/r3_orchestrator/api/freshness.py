"""Code and input versions for project loading in a long-running ``r3 serve``.

A server process imports ``r3_model`` / ``r3_catalog`` once. If their source
changes on disk afterwards, the process would keep validating ``robot.yaml``
with the old schema. ``CodeGuard`` remembers the code version the process
loaded and refuses project requests with ``server_code_stale`` once the files
on disk differ (``r3 serve --reload`` restarts the worker instead).

``ProjectCache`` keeps loaded projects keyed on the code version plus the
mtimes of every file ``Project.load`` reads (robot.yaml, r3.lock.yaml, the
project catalog and the bundled catalog data), so a cached project is never
older than its inputs or the code that validated it.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from r3_model.project import Project

from .errors import ApiError

#: Packages whose source defines the robot.yaml schema and how a project loads.
CODE_PACKAGES = ("r3_model", "r3_catalog")

FileStamps = dict[str, tuple[int, int]]


def _package_dir(name: str) -> Path:
    module = importlib.import_module(name)
    return Path(module.__file__ or "").resolve().parent


def _stamps(files: list[Path], key: Any) -> FileStamps:
    stamps: FileStamps = {}
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        stamps[key(path)] = (stat.st_mtime_ns, stat.st_size)
    return stamps


def code_files() -> FileStamps:
    """``package/relative.py -> (mtime_ns, size)`` for the Python source of ``CODE_PACKAGES``."""
    stamps: FileStamps = {}
    for name in CODE_PACKAGES:
        base = _package_dir(name)
        files = sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
        stamps.update(_stamps(files, lambda p, base=base, name=name: f"{name}/{p.relative_to(base).as_posix()}"))
    return stamps


def version_of(stamps: FileStamps) -> str:
    return hashlib.sha256(json.dumps(sorted(stamps.items())).encode()).hexdigest()[:16]


def code_version() -> str:
    """Version of the ``r3_model`` / ``r3_catalog`` source currently on disk."""
    return version_of(code_files())


def _import_schema_modules() -> None:
    """Import every module project loading may import lazily, so the snapshot matches what is in memory."""
    for name in CODE_PACKAGES:
        package = importlib.import_module(name)
        for info in pkgutil.iter_modules(package.__path__):
            if not info.ispkg:
                importlib.import_module(f"{name}.{info.name}")


class CodeGuard:
    """Detects that the schema code on disk is newer than the code this process imported."""

    def __init__(self) -> None:
        _import_schema_modules()
        self.loaded = code_files()
        self.version = version_of(self.loaded)

    def changed(self) -> list[str]:
        current = code_files()
        return sorted(k for k in self.loaded.keys() | current.keys() if self.loaded.get(k) != current.get(k))

    def check(self) -> None:
        changed = self.changed()
        if not changed:
            return
        shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        raise ApiError("server_code_stale",
                       f"The R3 server is running older robot-model/catalog code than is on disk ({shown} changed). "
                       "Restart `r3 serve` (or run it with --reload) to load the new code.",
                       {"changed": changed, "loaded_version": self.version, "current_version": code_version()})


def input_files(root: Path) -> list[Path]:
    """Every file ``Project.load`` reads for the project at ``root``."""
    from r3_catalog import DATA_DIR

    files = [root / "robot.yaml", root / "r3.lock.yaml"]
    for catalog in (root / "catalog", Path(DATA_DIR)):
        if catalog.is_dir():
            files.extend(sorted(p for p in catalog.rglob("*") if p.suffix in (".yaml", ".yml")))
    return files


def input_stamps(root: Path) -> FileStamps:
    return _stamps(input_files(root), str)


class ProjectCache:
    """Loaded projects keyed on (code version, input file stamps); least recently used are evicted.

    Cached ``Project`` objects are shared between requests, so views must treat
    them as read-only. Writers (spec patches, sourcing pins) change the input
    files, which invalidates the entry.
    """

    def __init__(self, size: int = 16) -> None:
        self.size = size
        self._entries: OrderedDict[Path, tuple[tuple[str, str], Project]] = OrderedDict()
        self._lock = threading.Lock()

    def key(self, root: Path) -> tuple[str, str]:
        return code_version(), version_of(input_stamps(root))

    def load(self, root: str | Path) -> Project:
        root = Path(root).resolve()
        key = self.key(root)
        with self._lock:
            hit = self._entries.get(root)
            if hit is not None and hit[0] == key:
                self._entries.move_to_end(root)
                return hit[1]
        project = Project.load(root)
        with self._lock:
            self._entries[root] = (key, project)
            self._entries.move_to_end(root)
            while len(self._entries) > self.size:
                self._entries.popitem(last=False)
        return project

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


PROJECTS = ProjectCache()
