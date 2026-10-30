"""Pulled geometry authority, SI transforms, exact checks and mesh consumers."""
from pathlib import Path
import numpy as np
import pytest
import yaml
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from r3_model.project import Project
from r3_model.pulled import PullError, load_pulled
from r3_model.pulled_view import apply_pulled, cad_view, world_mesh
from r3_model.diagnostics import StageResult
from r3_onshape.step_geometry import part_from_step, geometry_from_pulled
from r3_onshape.fit import FitChecker, FitReport, VOLUME_TOL
from pulled_fixture import write_pull


@pytest.fixture
def project(tmp_path):
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump({"robot": {"name": "synthetic-coupon", "mobility": "stationary"},
        "components": {"coupon": {"type": "synthetic-coupon"}}}))
    folder = tmp_path / "catalog/components"
    folder.mkdir(parents=True)
    (folder / "coupon.yaml").write_text(yaml.safe_dump({"components": [{"id": "synthetic-coupon", "name": "Synthetic coupon",
        "kind": "structure", "mechanical": {"mass": 99, "size": [9, 9, 9], "fabricated": True, "material": "PLA"}}]}))
    return Project.load(tmp_path)


def pull(project, *, offset=(0.1, 0.2, 0.3)):
    pose = np.eye(4); pose[:3, 3] = offset
    shape = BRepPrimAPI_MakeBox(10, 20, 30).Shape()
    write_pull(project.root, {"coupon": shape}, poses={"coupon": pose})
    return load_pulled(project.root)


def test_step_units_faces_and_solids(project):
    pulled = pull(project)
    model = geometry_from_pulled(pulled)
    p = model["coupon"]
    np.testing.assert_allclose(p.hi - p.lo, [.01, .02, .03], atol=1e-9)
    np.testing.assert_allclose(p.lo, [.1, .2, .3], atol=1e-9)
    assert len(p.faces_of("PLANE")) == 6
    assert p.manifold().volume() == pytest.approx(6e-6, rel=2e-6)
    assert VOLUME_TOL == 5e-11


def test_step_bore_is_measured_not_catalog_authored(project):
    block = BRepPrimAPI_MakeBox(20, 20, 10).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(10, 10, -1), gp_Dir(0, 0, 1)), 2, 12).Shape()
    write_pull(project.root, {"coupon": BRepAlgoAPI_Cut(block, hole).Shape()})
    pulled = load_pulled(project.root)
    part = part_from_step("coupon", pulled.parts["coupon"].step)
    bores = [f for f in part.faces_of("CYLINDER") if not f.convex]
    assert len(bores) == 1
    assert bores[0].radius == pytest.approx(.002)
    from r3_fab.geometry import build_geometry
    geometry = build_geometry(project, "coupon")
    assert geometry.source == "pulled_stl"
    assert geometry.holes[0]["d_mm"] == pytest.approx(4)
    np.testing.assert_allclose(geometry.mesh.extents, [20, 20, 10])


def test_world_mesh_and_mass_are_pulled(project):
    pulled = pull(project)
    mesh = world_mesh(pulled, "coupon")
    np.testing.assert_allclose(mesh.bounds[0], [.1, .2, .3], atol=1e-8)
    view = cad_view(project, mass_required=True)
    assert view["parts"]["coupon"]["mass"] == .02
    np.testing.assert_allclose(view["parts"]["coupon"]["com"], [.105, .21, .315])
    assert pulled.parts["coupon"].stl.stat().st_mode & 0o222 == 0


def test_wiring_uses_pulled_mesh(project):
    pull(project)
    from r3_wiring.obstacles import build_obstacles
    obstacles, diagnostics = build_obstacles(project)
    assert obstacles.estimated == []
    assert obstacles.obstacles[0].source == "pulled_stl"
    lo, hi = obstacles.obstacles[0].shape.aabb()
    np.testing.assert_allclose(hi-lo, [.01, .02, .03], atol=1e-8)


def test_assembly_does_not_solve_authored_pose(project):
    pulled = pull(project)
    project.spec.components["coupon"].pose.xyz = [8, 8, 8]
    assembly = apply_pulled(project)
    np.testing.assert_allclose(assembly.poses["coupon"], pulled.poses["coupon"])
    np.testing.assert_allclose(project.graph.component_pose("coupon")[0], [.1, .2, .3])


@pytest.mark.parametrize("stage", ["assembly", "wiring", "fabrication", "connectors", "sim"])
def test_missing_pull_is_one_stable_diagnostic(project, stage):
    from importlib import import_module
    name = {"assembly": "r3_orchestrator.assembly_stage", "fabrication": "r3_fab.stage",
            "connectors": "r3_connectors.stage", "sim": "r3_sim.stage", "wiring": "r3_wiring.stage"}[stage]
    result = import_module(name).run(project)
    assert [d.code for d in result.errors] == ["cad_pull_missing"]


def test_sim_mass_mesh_and_mujoco_compile(project):
    pull(project, offset=(0, 0, 0))
    from r3_sim.assembly import build_assembly_model
    import mujoco
    model = build_assembly_model(project)
    native = mujoco.MjModel.from_xml_string(model.xml)
    assert model.total_mass == pytest.approx(.02)
    assert model.mass_source == {"coupon": "pulled"}
    assert native.nmesh > 0
    assert 'type="box"' not in model.xml.split('name="coupon"')[1]


def test_sim_does_not_estimate_missing_mass(project):
    pull(project)
    import json, hashlib
    manifest = project.root / "cad/pulled/pull.json"
    data = json.loads(manifest.read_text()); data["parts"]["coupon"]["inertia"] = None
    manifest.chmod(0o644); manifest.write_text(json.dumps(data)); manifest.chmod(0o444)
    (project.root / 'r3.lock.yaml').write_text(yaml.safe_dump({'cad_pull': {'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}}))
    from r3_sim.assembly import build_assembly_model
    with pytest.raises(PullError, match="mass, COM and inertia") as exc:
        build_assembly_model(project)
    assert exc.value.code == 'cad_pull_mass_missing'


def test_exact_interference_uses_step_not_catalog(project):
    pull(project, offset=(0, 0, 0))
    model = geometry_from_pulled(load_pulled(project.root))
    pose = np.eye(4); pose[:3, 3] = [.005, 0, 0]
    other = part_from_step('other', load_pulled(project.root).parts['coupon'].step, pose)
    model.parts['other'] = other
    checker = FitChecker(project, model, {}, StageResult(stage='cad'))
    assert checker.intersection('coupon', 'other') == pytest.approx(3e-6, rel=1e-6)


def test_pulled_motion_uses_mujoco(project):
    pull(project)
    from r3_onshape.motion import run_motion
    result = StageResult(stage='cad')
    sweep = run_motion(project, None, FitReport(), result)
    assert sweep['solver'] == 'mujoco'
    assert not result.errors
