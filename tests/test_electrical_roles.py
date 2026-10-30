"""Shared rigid-body roles, independent of purchased parts and behavior ids."""
from pathlib import Path
import shutil

import pytest
from r3_model.project import Project
from r3_model.diagnostics import StageResult
from r3_sim.electrical.stage import vehicle_model
from r3_sim.vehicle.mjcf import build_vehicle_model
from r3_sim.vehicle.roles import wheel_joints


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'synthetic-role-test'
    shutil.copytree(Path(__file__).parent / 'fixtures/electrical_roles', root)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from pulled_fixture import write_pull
    import numpy as np
    mates = [{"id": "spin", "kind": "revolute", "a": ["root", "origin"], "b": ["shaft", "origin"], "axis": [0, 1, 0]},
             {"id": "rim_mount", "kind": "fastened", "a": ["shaft", "origin"], "b": ["rim", "origin"]},
             {"id": "actuator_mount", "kind": "fastened", "a": ["root", "origin"], "b": ["actuator", "origin"]}]
    shapes = {name: BRepPrimAPI_MakeBox(10, 10, 10).Shape() for name in ("root", "actuator", "shaft")}
    shapes["rim"] = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, -4, 0), gp_Dir(0, 1, 0)), 17.5, 8).Shape()
    write_pull(root, shapes, mates=mates)
    return Project.load(root)


def test_fixed_descendant_uses_the_actual_spin_joint_and_declared_actuator(project):
    roles = wheel_joints(project)
    assert roles['rim'].id == 'spin' and roles['rim'].child == 'shaft'
    result = StageResult(stage='electrical')
    load = vehicle_model(project, result)
    assert load['ground_drive']
    assert load['wheel_radius'] == pytest.approx(.0175)
    assert load['ratio'] == 3 and load['drivetrain_efficiency'] == .7
    assert load['driven_axle_load_fraction'] == 1
    native = build_vehicle_model(project)
    assert native.wheels['rim'].joint == roles['rim'].id
    assert native.wheels['rim'].driven


def test_stationary_fixture_uses_authored_joint_loads_not_road_load(project):
    project.spec.robot.mobility = 'stationary'
    load = vehicle_model(project, StageResult(stage='electrical'))
    assert not load['ground_drive']
    assert load['motor_operating_speed'] == 4 and load['motor_load_torque'] == .01


def test_radius_override_cannot_change_pulled_geometry(project):
    project.spec.components['rim'].params['radius'] = '21 mm'
    assert vehicle_model(project, StageResult(stage='electrical'))['wheel_radius'] == pytest.approx(.0175)
    assert build_vehicle_model(project).wheels['rim'].radius == pytest.approx(.0175)


def test_heterogeneous_static_load_estimate_is_explicitly_unsupported(project):
    from r3_model.spec import Transmission
    project.spec.mechanical.transmissions.append(Transmission(id='other', type='direct', actuator='actuator', joints=['spin'], ratio=4))
    result = StageResult(stage='electrical')
    vehicle_model(project, result)
    assert 'electrical_drive_layout_unsupported' in {d.code for d in result.errors}
