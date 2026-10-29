"""A robot project on disk: ``robot.yaml`` + sources + generated ``build/``.

Layout of an R3 project (also the layout of its GitHub repository)::

    robot.yaml              unified spec (source of truth)
    r3.lock.yaml            pinned external revisions (Onshape microversion, PCB hashes)
    catalog/                optional project-local catalog additions
    cad/                    CAD generator source (FeatureScript) and part mapping
    pcb/<board>/            KiCad generator + generated KiCad project
    backend/                BLITZ deployable modules (backend/<language>/<module>)
    build/                  generated artifacts (ignored by git)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .spec import RobotSpec


@dataclass
class Project:
    root: Path
    spec: RobotSpec
    lock: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        root = Path(root).resolve()
        spec_path = root / "robot.yaml"
        spec = RobotSpec.model_validate(yaml.safe_load(spec_path.read_text()))
        lock_path = root / "r3.lock.yaml"
        lock = yaml.safe_load(lock_path.read_text()) if lock_path.exists() else {}
        return cls(root=root, spec=spec, lock=lock or {})

    # Catalog is resolved lazily so r3_model has no hard import-time dependency.
    @property
    def catalog(self):  # -> r3_catalog.Catalog
        if not hasattr(self, "_catalog"):
            from r3_catalog import load_catalog

            self._catalog = load_catalog(self.root / "catalog")
        return self._catalog

    @property
    def graph(self):  # -> RobotGraph
        if not hasattr(self, "_graph"):
            from .graph import RobotGraph

            self._graph = RobotGraph(self.spec, self.catalog)
        return self._graph

    def build_dir(self, stage: str = "") -> Path:
        path = self.root / "build" / stage if stage else self.root / "build"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def read_build_json(self, relative: str) -> Any | None:
        path = self.root / "build" / relative
        return json.loads(path.read_text()) if path.exists() else None

    def write_lock(self, section: str, value: Any) -> None:
        self.lock[section] = value
        (self.root / "r3.lock.yaml").write_text(yaml.safe_dump(self.lock, sort_keys=True))

    def fingerprint(self) -> dict[str, str]:
        """Revision fingerprints of authored inputs, recorded on every result."""
        digest = hashlib.sha256((self.root / "robot.yaml").read_bytes()).hexdigest()[:16]
        result = {"robot_yaml_sha256": digest}
        try:
            sha = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
            result["git_commit"] = sha.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        for key, value in self.lock.items():
            if isinstance(value, dict) and "revision" in value:
                result[f"{key}_revision"] = str(value["revision"])
        return result
