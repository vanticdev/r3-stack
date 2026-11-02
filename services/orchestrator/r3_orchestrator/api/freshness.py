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
import os
import pkgutil
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from r3_model.project import Project

from .errors import ApiError

#: Packages whose source defines the robot.yaml schema and how a project loads.
CODE_PACKAGES = ("r3_model", "r3_catalog")

FileStamps = dict[str, tuple[int, ...]]


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
        stamps[key(path)] = (stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, stat.st_ino)
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


def input_files(root: Path, known: list[Path] | None = None) -> list[Path]:
    """Every file ``Project.load`` reads for the project at ``root``."""
    from r3_catalog import DATA_DIR

    from r3_model.spec_files import load_spec_files, spec_lock

    if known is None:
        with spec_lock(root, shared=True):
            files = [root / name for name in load_spec_files(root).texts]
    else:
        files = list(known)
    files.append(root / "r3.lock.yaml")
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

    def __init__(self, size: int = 16, *, resolve_cad: bool = True) -> None:
        self.size = size
        self.resolve_cad = resolve_cad
        self._entries: OrderedDict[Path, tuple[tuple[str, str], Project]] = OrderedDict()
        self._inputs: dict[Path, list[Path]] = {}
        self._loads: dict[Path, threading.Lock] = {}
        self._lock = threading.Lock()

    def key(self, root: Path) -> tuple[str, str]:
        return code_version(), version_of(input_stamps(root))

    def load(self, root: str | Path) -> Project:
        root = Path(root).resolve()
        with self._lock:
            loading = self._loads.setdefault(root, threading.Lock())
        # A page requests several domains at once. Resolve a cold project once,
        # rather than parsing the same include graph in every request thread.
        with loading:
            return self._load(root)

    def _load(self, root: Path) -> Project:
        version = code_version()
        with self._lock:
            hit = self._entries.get(root)
            known = self._inputs.get(root)
        if hit is not None:
            files = input_files(root, known)
            key = (version, version_of(_stamps(files, str)))
            if hit[0] == key:
                with self._lock:
                    if root in self._entries:
                        self._entries.move_to_end(root)
                return hit[1]
        # An include file changed: rediscover the graph before replacing the
        # cache. Catalog directories are rescanned even on a hit, so additions
        # and removals still invalidate it without parsing unchanged YAML.
        files = input_files(root)
        key = (version, version_of(_stamps(files, str)))
        project = Project.load(root) if self.resolve_cad else Project.load(root, resolve_cad=False)
        with self._lock:
            self._entries[root] = (key, project)
            self._inputs[root] = files
            self._entries.move_to_end(root)
            while len(self._entries) > self.size:
                removed, _ = self._entries.popitem(last=False)
                self._inputs.pop(removed, None)
        return project

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._inputs.clear()


PROJECTS = ProjectCache()

# List rows need identity and result freshness, not solved geometry. Keep a
# separate cache so list requests never queue behind CAD initialization.
SUMMARIES = ProjectCache(resolve_cad=False)


class GeometryViews:
    """Cache HTTP display projections, never release/integrity decisions.

    A hit requires a fresh content fingerprint plus inode/ctime/mtime/size for
    native inputs and derived artifacts. Writers that restore mtimes still
    invalidate the display. Stage runners and release checks bypass this cache.
    """

    def __init__(self) -> None:
        self._entries: OrderedDict[tuple[str, Path], tuple[Any, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def read(self, view: Any, ref: Any) -> Any:
        project = PROJECTS.load(ref.root)
        paths = input_files(ref.root)
        for folder in (ref.root / 'cad' / 'pulled', ref.root / 'build' / 'model',
                       ref.root / 'build' / 'assembly', ref.root / 'build' / 'cad' / 'pulled-meshes'):
            paths.extend(p for p in folder.rglob('*') if p.is_file())
        if view.__name__ == 'cad_view':
            cad = ref.root / 'build' / 'cad'
            paths.append(cad / 'result.json')
            # Historical viewer snapshots are unrelated to the current view.
            # Include the pointer and every asset it actually references.
            pointer = cad / 'viewer-mesh' / 'latest.json'
            paths.append(pointer)
            try:
                preview = json.loads(pointer.read_text())
                for part in preview.get('parts', []):
                    paths.extend(pointer.parent / part[k] for k in ('mesh', 'provenance'))
            except (OSError, ValueError, KeyError, TypeError):
                pass  # the view reports malformed current preview data
            for directory, folders, names in os.walk(cad):
                folders[:] = [f for f in folders if f not in ('viewer-mesh', 'pulled-meshes')]
                paths.extend(Path(directory) / n for n in names
                             if Path(n).suffix.lower() in ('.png', '.jpg', '.jpeg', '.svg', '.gif'))
        signature = (project.fingerprint(), _stamps(paths, lambda p: f"{p}:{p.resolve()}"))
        key = (view.__name__, ref.root)
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit[0] == signature:
                self._entries.move_to_end(key)
                return hit[1]
        payload = view(ref)
        # Store the pre-computation signature: any concurrent write guarantees
        # the next request misses rather than blessing an inconsistent snapshot.
        with self._lock:
            self._entries[key] = (signature, payload)
            self._entries.move_to_end(key)
            while len(self._entries) > 8:
                self._entries.popitem(last=False)
        return payload


GEOMETRY_VIEWS = GeometryViews()
