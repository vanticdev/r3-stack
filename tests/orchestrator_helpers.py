"""Fixture helpers for orchestrator tests: copy the bench and inject faults."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any, Callable

import yaml

from r3_catalog import DATA_DIR

ROOT = Path(__file__).resolve().parents[1]
# Independent source fixture. Faults are injected explicitly so model-stage
# checks do not depend on a project's incidental placement or catalog updates.
BENCH = ROOT / "tests" / "fixtures" / "bench"


def bench_spec() -> dict[str, Any]:
    spec = yaml.safe_load((BENCH / "robot.yaml").read_text())
    spec["components"]["block_a"] = {"type": "test-panel", "mount": "base", "pose": {"xyz": [0, 0, .3]}}
    spec["components"]["block_b"] = {"type": "test-panel", "mount": "base", "pose": {"xyz": [0, 0, .3]}}
    return spec


def make_project(tmp_path: Path, mutate: Callable[[dict[str, Any]], None] | None = None,
                 catalog_components: list[dict[str, Any]] | None = None, name: str = "bench",
                 with_pull: bool = False) -> Path:
    """Write a copy of the bench robot.yaml (optionally mutated) and a project-local catalog."""
    root = tmp_path / name
    root.mkdir(parents=True)
    shutil.copytree(BENCH / "catalog", root / "catalog")  # independent fixture catalog
    spec = bench_spec()
    if mutate:
        mutate(spec)
    from io import StringIO
    from ruamel.yaml import YAML
    editor = YAML()
    editor.indent(mapping=2, sequence=4, offset=2)
    buffer = StringIO()
    editor.dump(spec, buffer)
    (root / "robot.yaml").write_text("# Robot frame: synthetic stationary rig\n" + buffer.getvalue())
    if catalog_components:
        (root / "catalog" / "components").mkdir(parents=True, exist_ok=True)
        (root / "catalog" / "components" / "overrides.yaml").write_text(yaml.safe_dump({"components": catalog_components}))
    if with_pull:
        write_bench_pull(root)
    return root


def write_bench_pull(root: Path) -> None:
    """Publish explicit synthetic solids, never reconstructed catalog geometry.

    The plate is a 90 x 90 x 4 mm test coupon; all other parts are 4 mm cubes.
    Each has an independently assigned 100 g mass. Spec poses locate these
    test assets only: the stages subsequently read the pinned solved poses.
    """
    import numpy as np
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from pulled_fixture import write_pull
    from r3_model.graph import rpy_matrix
    from r3_model.spec_files import load_spec_files

    spec = load_spec_files(root).validate()
    shapes, poses = {}, {}
    for name, component in spec.components.items():
        if component.type == "test-panel":
            shapes[name] = BRepPrimAPI_MakeBox(gp_Pnt(-45, -45, 0), 90, 90, 4).Shape()
        else:
            shapes[name] = BRepPrimAPI_MakeBox(gp_Pnt(-2, -2, 0), 4, 4, 4).Shape()
        frame = np.eye(4)
        frame[:3, :3] = rpy_matrix(component.pose.rpy)
        frame[:3, 3] = component.pose.xyz
        poses[name] = frame
    write_pull(root, shapes, poses=poses, mass=0.1)


def catalog_entry(type_id: str) -> dict[str, Any]:
    """A deep copy of the catalog component the fixture layout uses (frozen entry, else bundled), to override."""
    for path in [*sorted((BENCH / "catalog" / "components").glob("*.yaml")), *sorted((DATA_DIR / "components").glob("*.yaml"))]:
        for entry in yaml.safe_load(path.read_text()).get("components", []):
            if entry["id"] == type_id:
                return copy.deepcopy(entry)
    raise KeyError(type_id)


def wire(spec: dict[str, Any], wire_id: str) -> dict[str, Any]:
    for harness in spec["electrical"]["harnesses"]:
        for item in harness["wires"]:
            if item["id"] == wire_id:
                return item
    raise KeyError(wire_id)


def codes(result: Any) -> set[str]:
    return {d.code for d in result.diagnostics}


def find(result: Any, code: str) -> list[Any]:
    return [d for d in result.diagnostics if d.code == code]
