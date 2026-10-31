"""Pinned Onshape downloads for inspection; only cad pull installs a model."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from r3_model.pulled import PullError

from .native import NativeCadTransport
from .pull import target


def export_part(project: Any, url: str, part_id: str, format: str = "step",
                version_id: str = "", transport: Any = None) -> dict[str, Any]:
    """Download unchanged bytes with a version pin and content-addressed provenance."""
    if format not in ("step", "stl") or not part_id.strip():
        raise PullError("cad_export_invalid", "Supply a part_id and format step or stl")
    pin = target(url, version_id)
    source = {**pin, "part_id": part_id, "format": format}
    identity = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    root = Path(project.root).resolve()
    build = (root / "build").resolve()
    folder = (build / "cad" / "inspection-exports" / identity).resolve()
    if root not in build.parents or build not in folder.parents:
        raise PullError("cad_export_invalid", "Inspection exports must stay inside this project's build directory")
    native = transport if transport is not None else NativeCadTransport()
    data = getattr(native, format)(pin["document_id"], pin["version_id"], pin["element_id"],
                                   part_id, pin["configuration"])
    if not data:
        raise PullError("cad_export_empty", "Onshape returned an empty export")
    checksum = hashlib.sha256(data).hexdigest()
    path = folder / f"{checksum}.{format}"
    manifest = path.with_suffix(".json")
    if path.is_symlink() or manifest.is_symlink():
        raise PullError("cad_export_invalid", "Inspection export files must not be symlinks")
    folder.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise PullError("cad_export_modified", "An inspection export was modified; retain it for diagnosis")
    if not path.exists():
        path.write_bytes(data)
    record = {"source": "onshape-native-export", "onshape": source, "sha256": checksum,
              "bytes": len(data), "path": str(path.relative_to(root)),
              "units": "m" if format == "stl" else "STEP declared units",
              "qualification": "unverified_export", "note": "Inspection only. Run cad pull to install and verify the model."}
    manifest.write_text(json.dumps(record, indent=2) + "\n")
    return {"ok": True, **record, "file": str(path), "manifest": str(manifest.relative_to(root))}
