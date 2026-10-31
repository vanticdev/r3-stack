"""Read-only, pinned CAD hand-off. No Onshape integration is imported here.

``cad/pulled/pull.json`` has schema ``r3/pull@1``, document_id, version_id,
parts, mates and poses. Parts are keyed by instance name and contain stl/step
paths relative to cad/pulled, sha256 (stl and step), mass (kg), com (m),
inertia (nine row-major kg m² values), material, mate_connectors (4x4 local
frames), and purchased. Poses are solved 4x4 assembly transforms in metres.
Additional part metadata may record the pinned Onshape reference.

The immutable manifest checksum is pinned in
``r3.lock.yaml: cad_pull.manifest_sha256``. A missing pin is an integrity error.
All files must be regular, inside cad/pulled, and have no write permission bits.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

SCHEMA = "r3/pull@1"


class PullError(ValueError):
    """Stable diagnostic code shared by every geometry consumer."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class PulledPart:
    name: str
    stl: Path
    step: Path
    sha256: dict[str, str]
    mass: float | None
    com: list[float] | None
    inertia: list[float] | None
    material: str | None
    mate_connectors: dict[str, np.ndarray]
    purchased: bool


@dataclass(frozen=True)
class PulledMate:
    id: str
    kind: str
    a: tuple[str, str]
    b: tuple[str, str]
    axis: list[float] | None
    limits: list[float] | None


@dataclass(frozen=True)
class PulledModel:
    document_id: str
    version_id: str
    parts: dict[str, PulledPart]
    mates: list[PulledMate]
    poses: dict[str, np.ndarray]


def _frame(value: Any, label: str) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise PullError("cad_pull_invalid", f"{label}: expected a finite 4x4 frame")
    r = matrix[:3, :3]
    if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-9) or not np.allclose(r.T @ r, np.eye(3), atol=1e-7) or not np.isclose(np.linalg.det(r), 1, atol=1e-7):
        raise PullError("cad_pull_invalid", f"{label}: frame must be a rigid right-handed transform")
    matrix.setflags(write=False)
    return matrix


def _file(folder: Path, relative: str, expected: str, label: str) -> Path:
    path = folder / relative
    if path.is_symlink() or folder.resolve() not in path.resolve().parents or not path.is_file():
        raise PullError("cad_pull_missing", f"{label}: missing file or path outside cad/pulled: {relative}")
    if path.stat().st_mode & 0o222:
        raise PullError("cad_pull_modified", f"{label}: pulled file is writable; pull again")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise PullError("cad_pull_modified", f"{label}: SHA-256 differs from the pinned pull; pull again")
    return path


def _numbers(value: Any, count: int, label: str) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != count or not np.isfinite(np.asarray(value, dtype=float)).all():
        raise PullError("cad_pull_invalid", f"{label}: expected {count} finite numbers")
    return [float(v) for v in value]


def load_pulled(project_root: Path) -> PulledModel:
    """Validate pinned bytes, paths and assembly references before returning CAD.

    Raises PullError with cad_pull_missing, cad_pull_modified or cad_pull_invalid.
    Never reconstructs geometry, contacts Onshape or substitutes catalog shapes.
    """
    root = Path(project_root).resolve()
    folder = root / "cad" / "pulled"
    manifest = folder / "pull.json"
    if root not in folder.resolve().parents:
        raise PullError("cad_pull_invalid", "cad/pulled escapes the project root")
    if not manifest.is_file():
        raise PullError("cad_pull_missing", "No pulled CAD. Model and mate in Onshape, then run r3 cad pull.")
    try:
        lock = yaml.safe_load((root / "r3.lock.yaml").read_text()) or {}
        expected = lock.get("cad_pull", {}).get("manifest_sha256")
        if not expected:
            raise PullError("cad_pull_modified", "CAD manifest has no pinned checksum in r3.lock.yaml; pull again")
        _file(folder, "pull.json", expected, "manifest")
        data = json.loads(manifest.read_text())
        if data["schema"] != SCHEMA or not data["document_id"] or not data["version_id"]:
            raise PullError("cad_pull_invalid", "CAD requires a pinned Onshape document and version")
        if not data["parts"]:
            raise PullError("cad_pull_invalid", "Pulled CAD contains no parts")
        pin = lock["cad_pull"]
        for key in ("document_id", "version_id"):
            if key in pin and pin[key] != data[key]:
                raise PullError("cad_pull_modified", f"Pinned {key} differs from the manifest")
        parts = {}
        for name, item in data["parts"].items():
            if not name or item.get("name", name) != name:
                raise PullError("cad_pull_invalid", f"Invalid part identity {name!r}")
            hashes = item["sha256"]
            if set(hashes) != {"stl", "step"} or not all(isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v) for v in hashes.values()):
                raise PullError("cad_pull_invalid", f"{name}: expected STL and STEP SHA-256 hashes")
            if type(item["purchased"]) is not bool:
                raise PullError("cad_pull_invalid", f"{name}: purchased must be a boolean")
            mass = item.get("mass")
            if mass is not None and (not np.isfinite(mass) or mass <= 0):
                raise PullError("cad_pull_invalid", f"{name}: mass must be finite and positive")
            parts[name] = PulledPart(name, _file(folder, item["stl"], hashes["stl"], name),
                _file(folder, item["step"], hashes["step"], name), dict(hashes), mass,
                _numbers(item.get("com"), 3, f"{name}.com"), _numbers(item.get("inertia"), 9, f"{name}.inertia"),
                item.get("material"), {key: _frame(frame, f"{name}.{key}") for key, frame in item["mate_connectors"].items()},
                item["purchased"])
        poses = {name: _frame(frame, f"pose {name}") for name, frame in data["poses"].items()}
        if set(poses) != set(parts):
            raise PullError("cad_pull_invalid", "Every pulled part must have exactly one solved assembly pose")
        mates, ids = [], set()
        for item in data["mates"]:
            ends = []
            for key in ("a", "b"):
                end = item[key]
                if not isinstance(end, list) or len(end) != 2 or end[0] not in parts or end[1] not in parts[end[0]].mate_connectors:
                    raise PullError("cad_pull_invalid", f"Mate {item['id']}: missing part or mate connector {end}")
                ends.append(tuple(end))
            if not item["id"] or item["id"] in ids:
                raise PullError("cad_pull_invalid", "Mate ids must be nonempty and unique")
            ids.add(item["id"])
            axis = _numbers(item.get("axis"), 3, "mate axis")
            limits = _numbers(item.get("limits"), 2, "mate limits")
            if axis is not None and not np.isclose(np.linalg.norm(axis), 1, atol=1e-7):
                raise PullError("cad_pull_invalid", f"Mate {item['id']}: axis must be a unit vector")
            if limits is not None and limits[0] > limits[1]:
                raise PullError("cad_pull_invalid", f"Mate {item['id']}: reversed limits")
            mates.append(PulledMate(item["id"], item["kind"], *ends, axis, limits))
        for item in data.get("images", []):
            _file(folder, item["path"], item["sha256"], "viewport evidence")
        return PulledModel(data["document_id"], data["version_id"], parts, mates, poses)
    except PullError:
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise PullError("cad_pull_invalid", f"Invalid CAD pull manifest: {error}") from error
