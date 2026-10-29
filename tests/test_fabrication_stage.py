"""The ``fabrication`` stage on small temporary projects: part selection, printability checks,
orientation, insert recommendations, strength and print-file export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh
import yaml

from r3_fab import stage
from r3_model.project import Project

PETG = {"material": "PETG", "density": 1280, "color": "#2c6e9e", "fabricated": True}

# A PETG plate on two aluminium standoffs above a base that carries a wheel, with a bought box
# screwed straight into a tapped hole in the plate (an M3 thread in printed plastic).
ASSEMBLY_CATALOG = [
    {"id": "fab-base", "kind": "structure", "name": "Base plate",
     "mechanical": {"shape": "box", "size": ["200 mm", "100 mm", "3 mm"], "mass": 0.15, "material": "Aluminum 6061-T6",
                    "fabricated": True},
     "interfaces": {
         "so_a": {"kind": "hole_pattern", "origin": ["-40 mm", 0, "3 mm"], "params": {"holes": [[0, 0, "3.4 mm", "clearance", "3 mm"]]}},
         "so_b": {"kind": "hole_pattern", "origin": ["40 mm", 0, "3 mm"], "params": {"holes": [[0, 0, "3.4 mm", "clearance", "3 mm"]]}},
         "wheel": {"kind": "hole_pattern", "origin": ["-90 mm", 0, 0], "z": [0, 0, -1],
                   "params": {"holes": [[0, 0, "3 mm", "tapped", "3 mm"]]}}}},
    {"id": "fab-wheel", "kind": "wheel", "name": "Wheel",
     "mechanical": {"shape": "cylinder", "size": ["40 mm", "10 mm"], "mass": 0.05, "material": "Rubber"},
     "interfaces": {"hub": {"kind": "hole_pattern", "origin": [0, 0, "10 mm"], "params": {"holes": [[0, 0, "3.4 mm", "clearance", "9 mm"]]}}}},
    {"id": "fab-standoff", "kind": "structure", "name": "Standoff M3 x 20",
     "mechanical": {"shape": "cylinder", "size": ["5.5 mm", "20 mm"], "mass": 0.002, "material": "Aluminum"},
     "interfaces": {"bottom": {"kind": "hole_pattern", "z": [0, 0, -1], "params": {"holes": [[0, 0, "3 mm", "tapped", "6 mm"]]}},
                    "top": {"kind": "hole_pattern", "origin": [0, 0, "20 mm"], "params": {"holes": [[0, 0, "3 mm", "tapped", "6 mm"]]}}}},
    {"id": "fab-plate-petg", "kind": "structure", "name": "Printed plate",
     "mechanical": {"shape": "box", "size": ["120 mm", "80 mm", "4 mm"], "mass": 0.05, **PETG},
     "interfaces": {
         "h_a": {"kind": "hole_pattern", "origin": ["-40 mm", 0, 0], "z": [0, 0, -1], "params": {"holes": [[0, 0, "3.4 mm", "clearance", "4 mm"]]}},
         "h_b": {"kind": "hole_pattern", "origin": ["40 mm", 0, 0], "z": [0, 0, -1], "params": {"holes": [[0, 0, "3.4 mm", "clearance", "4 mm"]]}},
         "box_seat": {"kind": "hole_pattern", "origin": [0, 0, "4 mm"], "params": {"holes": [[0, 0, "3 mm", "tapped", "4 mm"]]}}}},
    {"id": "fab-box", "kind": "sensor", "name": "Bought box", "mechanical": {"shape": "box", "size": ["30 mm", "30 mm", "20 mm"], "mass": 0.2},
     "interfaces": {"bottom": {"kind": "hole_pattern", "z": [0, 0, -1], "params": {"holes": [[0, 0, "3.4 mm", "clearance", "2 mm"]]}}}},
]

ASSEMBLY_ROBOT = {
    "schema": "r3/robot@1",
    "robot": {"name": "fab-bench"},
    "components": {"base": {"type": "fab-base", "pose": {"xyz": [0, 0, 0.02]}}, "wheel": {"type": "fab-wheel"},
                   "so_a": {"type": "fab-standoff"}, "so_b": {"type": "fab-standoff"}, "plate": {"type": "fab-plate-petg"},
                   "box": {"type": "fab-box"}},
    "assembly": {"root": "base", "joins": [
        {"id": "wheel_mount", "a": "base.wheel", "b": "wheel.hub", "fasteners": {"type": "M3x12"}},
        {"id": "so_a_bottom", "a": "base.so_a", "b": "so_a.bottom", "fasteners": {"type": "M3x8"}},
        {"id": "so_b_bottom", "a": "base.so_b", "b": "so_b.bottom", "fasteners": {"type": "M3x8"}},
        {"id": "so_a_top", "a": "so_a.top", "b": "plate.h_a", "fasteners": {"type": "M3x8"}},
        {"id": "so_b_top", "a": "so_b.top", "b": "plate.h_b", "fasteners": {"type": "M3x8"}},
        {"id": "box_mount", "a": "plate.box_seat", "b": "box.bottom", "fasteners": {"type": "M3x6"}}]},
}

# No assembly: an oversize plate, a sphere (always needs support) and a 0.5 mm wall tube.
LOOSE_CATALOG = [
    {"id": "fab-oversize", "kind": "structure", "name": "Oversize plate",
     "mechanical": {"shape": "box", "size": ["300 mm", "280 mm", "3 mm"], "mass": 0.3, **PETG}},
    {"id": "fab-ball", "kind": "structure", "name": "Ball",
     "mechanical": {"shape": "custom", "size": ["20 mm", "20 mm", "20 mm"], "mass": 0.005, **PETG,
                    "solids": [{"kind": "sphere", "position": [0, 0, "10 mm"], "diameter": "20 mm"}]}},
    {"id": "fab-thin-tube", "kind": "structure", "name": "Thin tube",
     "mechanical": {"shape": "custom", "size": ["20 mm", "20 mm", "30 mm"], "mass": 0.002, **PETG,
                    "solids": [{"kind": "cylinder", "diameter": "20 mm", "length": "30 mm"},
                               {"kind": "cylinder", "op": "remove", "diameter": "19 mm", "length": "30 mm"}]}},
]
LOOSE_ROBOT = {
    "schema": "r3/robot@1",
    "robot": {"name": "fab-loose"},
    "components": {"big": {"type": "fab-oversize"}, "ball": {"type": "fab-ball", "pose": {"xyz": [0.4, 0, 0]}},
                   "tube": {"type": "fab-thin-tube", "pose": {"xyz": [0.6, 0, 0]}}},
}


def make_project(root: Path, robot: dict[str, Any], components: list[dict[str, Any]]) -> Project:
    (root / "catalog" / "components").mkdir(parents=True)
    (root / "robot.yaml").write_text(yaml.safe_dump(robot, sort_keys=False))
    (root / "catalog" / "components" / "fab.yaml").write_text(yaml.safe_dump({"components": components}, sort_keys=False))
    return Project.load(root)


def codes(result: Any, code: str, subject: str | None = None) -> list[Any]:
    return [d for d in result.diagnostics if d.code == code and (subject is None or subject in d.subjects)]


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    project = make_project(tmp_path_factory.mktemp("fab") / "bench", ASSEMBLY_ROBOT, ASSEMBLY_CATALOG)
    assert not project.assembly.errors, [d.message for d in project.assembly.errors]
    return project, stage.run(project)


@pytest.fixture(scope="module")
def loose(tmp_path_factory):
    project = make_project(tmp_path_factory.mktemp("fab") / "loose", LOOSE_ROBOT, LOOSE_CATALOG)
    return project, stage.run(project)


def test_only_fdm_parts_are_printed(bench):
    project, result = bench
    plan = json.loads((project.root / "build" / "fabrication" / "plan.json").read_text())
    assert set(plan["parts"]) == {"plate"}
    assert plan["parts"]["plate"]["material"] == "petg"
    not_printed = codes(result, "not_printed")[0]
    assert "base" in not_printed.data["parts"]  # aluminium plate: fabricated, not printed
    assert codes(result, "print_geometry_estimated", "plate")  # no CAD mesh in this project


def test_plate_prints_flat(bench):
    project, result = bench
    entry = json.loads((project.root / "build" / "fabrication" / "plan.json").read_text())["parts"]["plate"]
    build = np.asarray(entry["orientation"]["build_direction"])
    assert abs(abs(build[2]) - 1.0) < 1e-9  # the 4 mm direction is up
    assert entry["height_mm"] == pytest.approx(4.0, abs=1e-6)
    assert entry["support_area_mm2"] == pytest.approx(0.0)
    assert not codes(result, "print_bed_exceeded") and not codes(result, "print_overhang", "plate")


def test_tapped_hole_in_printed_plate_recommends_an_insert(bench):
    _, result = bench
    tapped = codes(result, "printed_tapped_hole", "plate")
    assert len(tapped) == 1 and "box_mount" in tapped[0].subjects
    data = tapped[0].data
    # CNC Kitchen M3 standard insert: 4.0 mm hole, 5.7 mm long (+1 mm blind depth), 1.6 mm wall.
    assert data["thread"] == "M3"
    assert data["insert_hole_d"] == pytest.approx(4.0e-3)
    assert data["insert_hole_depth"] == pytest.approx(6.7e-3)
    assert data["boss_min_d"] == pytest.approx(7.2e-3)
    assert "low loads" in tapped[0].hint
    # Screw heads of the standoff screws seat on the printed plate: counterbore = head + 2 x cap clearance.
    heads = codes(result, "printed_counterbore", "plate")
    assert {d.subjects[1] for d in heads} == {"so_a_top", "so_b_top"}
    assert heads[0].data["counterbore_d"] == pytest.approx(5.5e-3 + 2 * 0.3e-3)
    # 3.4 mm holes for M3 are 0.4 mm clearance: inside the FDM range.
    assert not codes(result, "printed_hole_clearance")


def test_strength_on_the_load_path(bench):
    project, result = bench
    ok = codes(result, "print_strength_ok", "plate") or codes(result, "print_strength_margin_low", "plate")
    assert ok, [(d.code, d.message) for d in result.diagnostics]
    data = ok[0].data
    assert data["safety_factor"] > 1.0 and np.isfinite(data["safety_factor"])
    assert set(data["supports"]) == {"so_a_top", "so_b_top"}
    assert data["loads"] == {"box_mount": pytest.approx(0.2)}
    assert data["max_von_mises"] > 0 and data["max_deflection"] > 0
    entry = json.loads((project.root / "build" / "fabrication" / "plan.json").read_text())["parts"]["plate"]
    assert entry["strength"]["runtime_s"] < 20
    assert (project.root / "build" / "fabrication" / "plate" / "plate_stress.png").stat().st_size > 10_000


def test_print_files_are_readable_and_on_the_bed(bench):
    project, result = bench
    folder = project.root / "build" / "fabrication" / "plate"
    for name in ("plate.stl", "plate.3mf"):
        mesh = trimesh.load(folder / name, force="mesh")
        assert isinstance(mesh, trimesh.Trimesh) and len(mesh.faces) > 0
        assert mesh.bounds[0, 2] == pytest.approx(0.0, abs=1e-6)
        assert sorted(mesh.extents.round(3)) == pytest.approx([4.0, 80.0, 120.0], abs=1e-3)
    entry = json.loads((project.root / "build" / "fabrication" / "plan.json").read_text())["parts"]["plate"]
    holes = 2 * np.pi * (3.4 / 2) ** 2 * 4 + np.pi * (2.5 / 2) ** 2 * 5.5  # two clearance holes + the M3 tap drill (2.5 mm)
    assert entry["volume_mm3"] == pytest.approx(120 * 80 * 4 - holes, rel=2e-3)
    assert entry["print_mass_kg"] == pytest.approx(entry["volume_mm3"] * 1e-9 * 1280, rel=1e-4)  # solid: infill 1.0
    assert (project.root / "build" / "fabrication" / "preview.png").exists()
    assert {a.kind for a in result.artifacts} >= {"stl", "3mf", "json", "plot_png"}


def test_bed_overhang_and_thin_walls(loose):
    _, result = loose
    bed = codes(result, "print_bed_exceeded", "big")
    assert len(bed) == 1 and max(bed[0].data["extents"]) == pytest.approx(0.300, abs=1e-6)
    assert not codes(result, "print_bed_exceeded", "ball")
    overhang = codes(result, "print_overhang", "ball")
    assert len(overhang) == 1 and overhang[0].data["support_area"] > 0 and overhang[0].data["max_angle_deg"] > 45
    thin = codes(result, "print_wall_thin", "tube")
    assert len(thin) == 1 and thin[0].severity.value == "error"
    assert thin[0].data["min_thickness"] == pytest.approx(0.5e-3, abs=0.05e-3)
    assert codes(result, "print_strength_not_applicable")  # no assembly: no load paths
    assert not result.ok


def test_orientation_override_and_unknown_printer(tmp_path):
    robot = {**LOOSE_ROBOT, "components": {"ball": {"type": "fab-ball"}},
             "fabrication": {"printer": "no-such-printer", "parts": {"ball": {"orientation": [90, 0, 0]}}}}
    project = make_project(tmp_path / "p", robot, LOOSE_CATALOG)
    result = stage.run(project)
    assert codes(result, "printer_unknown")
    plan = json.loads((project.root / "build" / "fabrication" / "plan.json").read_text())
    assert plan["parts"]["ball"]["orientation"]["override"] is True
    assert plan["parts"]["ball"]["orientation"]["rotation_deg"] == pytest.approx([90, 0, 0], abs=1e-6)


def test_print_false_and_no_printed_parts_is_not_applicable(tmp_path):
    robot = {**LOOSE_ROBOT, "components": {"tube": {"type": "fab-thin-tube"}}, "fabrication": {"parts": {"tube": {"print": False}}}}
    result = stage.run(make_project(tmp_path / "p", robot, LOOSE_CATALOG))
    assert result.ok and codes(result, "not_applicable")
    assert "tube" in codes(result, "not_printed")[0].data["parts"]


def test_blank_project_is_not_applicable(tmp_path):
    root = tmp_path / "blank"
    root.mkdir()
    (root / "robot.yaml").write_text(yaml.safe_dump({"schema": "r3/robot@1", "robot": {"name": "blank"}, "components": {}}))
    result = stage.run(Project.load(root))
    assert result.ok and [d.code for d in result.diagnostics] == ["not_applicable"]
