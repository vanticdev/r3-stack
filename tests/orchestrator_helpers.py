"""Fixture helpers for orchestrator tests: copy the RC car and inject faults."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import yaml

from r3_catalog import DATA_DIR

ROOT = Path(__file__).resolve().parents[1]
RC_CAR = ROOT / "examples" / "robots" / "rc-car"


def rc_car_spec() -> dict[str, Any]:
    return yaml.safe_load((RC_CAR / "robot.yaml").read_text())


def make_project(tmp_path: Path, mutate: Callable[[dict[str, Any]], None] | None = None,
                 catalog_components: list[dict[str, Any]] | None = None, name: str = "car") -> Path:
    """Write a copy of the RC car robot.yaml (optionally mutated) and a project-local catalog."""
    root = tmp_path / name
    root.mkdir(parents=True)
    if mutate is None and catalog_components is None:
        (root / "robot.yaml").write_text((RC_CAR / "robot.yaml").read_text())
        return root
    spec = rc_car_spec()
    if mutate:
        mutate(spec)
    (root / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    if catalog_components:
        (root / "catalog" / "components").mkdir(parents=True)
        (root / "catalog" / "components" / "overrides.yaml").write_text(yaml.safe_dump({"components": catalog_components}))
    return root


def catalog_entry(type_id: str) -> dict[str, Any]:
    """A deep copy of a bundled catalog component (to override in a project catalog)."""
    for path in sorted((DATA_DIR / "components").glob("*.yaml")):
        for entry in yaml.safe_load(path.read_text())["components"]:
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
