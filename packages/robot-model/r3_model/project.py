"""A robot project on disk: ``robot.yaml`` + sources + generated ``build/``.

Layout of an R3 project (also the layout of its GitHub repository)::

    robot.yaml              description index (source of truth)
    parts/<name>.yaml       part descriptions included by the index
    electrical/ software/   included domain descriptions
    r3.lock.yaml            pinned external revisions (Onshape microversion, PCB hashes)
    catalog/                optional project-local catalog additions
    cad/                    CAD notes and source mapping; geometry lives in Onshape
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
from .spec_files import load_spec_files, spec_lock


@dataclass
class Project:
    root: Path
    spec: RobotSpec
    lock: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        root = Path(root).resolve()
        with spec_lock(root, shared=True):
            spec = load_spec_files(root).validate()
        lock_path = root / "r3.lock.yaml"
        lock = yaml.safe_load(lock_path.read_text()) if lock_path.exists() else {}
        project = cls(root=root, spec=spec, lock=lock or {})
        project.apply_sourcing()
        if (root / "cad/pulled/pull.json").exists():
            from .pulled_view import apply_pulled
            from .pulled import PullError
            try:
                apply_pulled(project)
            except PullError:
                pass  # geometry stages report the one stable pull diagnostic
        return project

    @property
    def assembly(self):  # -> r3_model.assembly.AssemblyResult | None
        """Read the pulled solved poses/mates and project them to stage-facing fields."""
        if not hasattr(self, "_assembly"):
            if not (self.root / "cad/pulled/pull.json").exists():
                return None
            from .pulled_view import apply_pulled
            apply_pulled(self)
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
            from .pulled import PullError
            if not hasattr(self, "_pulled") and any(comp.pulled is not None for comp in self.spec.components.values()):
                # Never turn invalid/deleted native evidence back into catalog
                # geometry for a physical consumer.
                if self.assembly is None or not hasattr(self, "_pulled"):
                    raise PullError("cad_pull_missing", "Native connector frames require a validated CAD pull")
            native = getattr(self, "_pulled", None)
            self._graph = RobotGraph(self.spec, self.catalog, native=native)
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
        with spec_lock(self.root, shared=True):
            digest = load_spec_files(self.root).sha256[:16]
        result = {"robot_yaml_sha256": digest}
        manifest = self.root / "cad/pulled/pull.json"
        if manifest.is_file():
            result["cad_pull_manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        from r3_catalog.revision import catalog_modelbank_revision

        result["catalog_modelbank_revision"] = catalog_modelbank_revision(self.root / "catalog")
        sources = hashlib.sha256()
        for folder in ("backend", "simulation", "tests"):
            for path in sorted((self.root / folder).rglob("*")):
                if not path.is_file() or any(part in ("__pycache__", ".pytest_cache", ".venv", "build") for part in path.relative_to(self.root).parts):
                    continue
                if self.root not in path.resolve().parents:
                    raise ValueError(f"project source escapes its root: {path.relative_to(self.root)}")
                name, content = str(path.relative_to(self.root)).encode(), path.read_bytes()
                for value in (name, content):
                    sources.update(len(value).to_bytes(8, "big"))
                    sources.update(value)
        result["project_sources_revision"] = sources.hexdigest()
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
