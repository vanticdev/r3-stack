"""Synthetic immutable STEP/STL hand-offs; no catalog-generated CAD."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import yaml


def write_pull(root: Path, shapes: dict, *, poses=None, mates=None, connectors=None, mass=0.02):
    import trimesh
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.Interface import Interface_Static
    from r3_onshape.step_geometry import part_from_step

    folder = root / "cad/pulled"
    (folder / "parts").mkdir(parents=True, exist_ok=True)
    poses = poses or {name: np.eye(4) for name in shapes}
    data = {"schema": "r3/pull@1", "document_id": "synthetic-document", "version_id": "synthetic-version",
            "parts": {}, "poses": {name: np.asarray(frame).tolist() for name, frame in poses.items()}, "mates": mates or []}
    for index, (name, shape) in enumerate(shapes.items()):
        step = folder / f"parts/{index}.step"
        stl = folder / f"parts/{index}.stl"
        for path in (step, stl):
            if path.exists():
                path.chmod(0o644)
        # Fixture dimensions are explicitly mm, independent of reader settings.
        Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
        writer = STEPControl_Writer()
        Interface_Static.SetCVal_s("write.step.unit", "MM")
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(str(step))
        geometry = part_from_step(name, step)
        mesh = trimesh.Trimesh(geometry.vertices, geometry.triangles, process=True)
        mesh.export(stl)
        props = mesh.mass_properties
        inertia = props.inertia * mass / props.mass
        data["parts"][name] = {"stl": str(stl.relative_to(folder)), "step": str(step.relative_to(folder)),
            "sha256": {"stl": hashlib.sha256(stl.read_bytes()).hexdigest(), "step": hashlib.sha256(step.read_bytes()).hexdigest()},
            "mass": mass, "com": props.center_mass.tolist(), "inertia": inertia.reshape(-1).tolist(),
            "material": "PLA", "purchased": False,
            "mate_connectors": {key: np.asarray(frame).tolist() for key, frame in (connectors or {}).get(name, {"origin": np.eye(4)}).items()}}
        step.chmod(0o444); stl.chmod(0o444)
    manifest = folder / "pull.json"
    if manifest.exists():
        manifest.chmod(0o644)
    manifest.write_text(json.dumps(data))
    lock_path = root / "r3.lock.yaml"
    lock = yaml.safe_load(lock_path.read_text()) if lock_path.exists() else {}
    lock["cad_pull"] = {"manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}
    lock_path.write_text(yaml.safe_dump(lock))
    manifest.chmod(0o444)
    return data
