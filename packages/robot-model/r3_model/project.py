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
        project = cls(root=root, spec=spec, lock=lock or {})
        project.apply_sourcing()
        if spec.assembly is not None:
            project.assembly  # compile now so every stage sees derived poses
        return project

    @property
    def assembly(self):  # -> r3_model.assembly.AssemblyResult | None
        """Compiled object/interface assembly. Compiling fills
        ``spec.components[*].pose`` (and ``mount`` / derived joints) with the
        derived poses, so stages that read poses work unchanged. Specs without
        an ``assembly`` section are left untouched and return None."""
        if self.spec.assembly is None:
            return None
        if not hasattr(self, "_assembly"):
            from .assembly import apply_to_spec, compile_assembly
            from .mechanics import derive_mechanical

            result = compile_assembly(self.spec, self.catalog)
            result.diagnostics.extend(apply_to_spec(self.spec, result))
            result.diagnostics.extend(derive_mechanical(self.spec, self.catalog, result))
            self._assembly = result
        return self._assembly

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

    def apply_sourcing(self) -> dict[str, str]:
        """Resolve sourced slots: a component with ``source`` whose slot is pinned in
        ``r3.lock.yaml`` (``sourcing.<slot>.catalog_id``) uses the pinned catalog id
        instead of its authored ``type``. Returns component id -> resolved type.
        Re-sourcing never happens here; it is the explicit ``r3 source`` command."""
        pins = self.lock.get("sourcing") or {}
        resolved: dict[str, str] = {}
        for slot, members in self.spec.source_slots().items():
            pin = pins.get(slot)
            if isinstance(pin, dict) and pin.get("catalog_id"):
                for cid in members:
                    self.spec.components[cid].type = str(pin["catalog_id"])
                    resolved[cid] = str(pin["catalog_id"])
        return resolved

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
        if self.lock.get("sourcing"):
            pinned = json.dumps(self.lock["sourcing"], sort_keys=True, default=str)
            result["sourcing_sha256"] = hashlib.sha256(pinned.encode()).hexdigest()[:16]
        return result
