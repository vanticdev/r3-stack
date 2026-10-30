"""Generic mechanical fixtures; no example robot or design-specific thresholds."""

from __future__ import annotations

import math
import json

import numpy as np
from pathlib import Path

import mujoco
import pytest
import yaml

from r3_model.connections import (
    compile_connections,
    interference_capacity,
    screw_capacities,
    snap_capacity,
)
from r3_model.project import Project
from r3_sim.load_cases import FACES, LoadCase, all_scenarios, configure_model
from r3_sim.assembly import build_assembly_model
from r3_sim.dynamics import RobotDynamics
from r3_sim.joints import loosen_preload


def fixture_project(tmp_path: Path, connection: str = "screw", nut: bool = True) -> Project:
    catalog = tmp_path / "catalog" / "components"
    catalog.mkdir(parents=True)
    catalog.joinpath("plates.yaml").write_text(
        yaml.safe_dump(
            {
                "components": [
                    {
                        "id": "plate",
                        "name": "Plate",
                        "kind": "structure",
                        "mechanical": {
                            "mass": 0.1,
                            "shape": "box",
                            "size": [0.06, 0.06, 0.005],
                            "material": "Aluminum 6061-T6",
                        },
                    }
                ]
            }
        )
    )
    mechanical = {}
    if connection == "screw":
        mechanical["fasteners"] = [
            dict(
                id="S",
                type="shcs-m3",
                length=0.02,
                position=[0, 0, 0.01],
                axis=[0, 0, -1],
                clamps=["base", "part"],
                grip=0.01,
                **({"nut": "nut-m3-nyloc"} if nut else {}),
            )
        ]
    elif connection == "tape":
        mechanical["interfaces"] = [
            dict(
                id="pad",
                kind="adhesive",
                parts=["base", "part"],
                axis_point=[0, 0, 0.005],
                axis_dir=[0, 0, 1],
                params={
                    "physics": {
                        k: dict(
                            value=v,
                            status="datasheet",
                            source="Synthetic qualified adhesive coupon",
                        )
                        for k, v in {
                            "area": 0.001,
                            "shear_strength": 1e5,
                            "peel_strength": 2e4,
                            "radius": 0.02,
                        }.items()
                    }
                },
            )
        ]
    tmp_path.joinpath("robot.yaml").write_text(
        yaml.safe_dump(
            dict(
                robot={"name": "two plates"},
                components={
                    "base": {"type": "plate"},
                    "part": {
                        "type": "plate",
                        "mount": "base",
                        "pose": {"xyz": [0, 0, 0.005]},
                    },
                },
                mechanical=mechanical,
            )
        )
    )
    return Project.load(tmp_path)


def vehicle_for(p: Project, case: str) -> RobotDynamics:
    scenario = next(s for s in all_scenarios(p) if s.id == case)
    vm = build_assembly_model(p)
    configure_model(vm, scenario, p)
    v = RobotDynamics(p, vm)
    v.load_case = LoadCase(v, scenario, p, 1)
    return v


def run_steps(v: RobotDynamics, n: int = 1000) -> None:
    for _ in range(n):
        v.step()
    assert not v.data.warning[mujoco.mjtWarning.mjWARN_BADQACC].number


def test_shigley_metric_torque_proof_strip_and_punching_hand_calculation():
    # Shigley ch.8 metric stress area and T=KFd: M3x0.5, 0.5 Nm,
    # K=.2 -> 833.333 N; At=5.030 mm²; slip at mu=.2 -> 166.667 N.
    c = screw_capacities(0.003, 0.0005, 0.5, 0.2, 0.2, 970e6, 0.006, 100e6, 0.0055, 0.003, 100e6)
    assert c["preload"] == pytest.approx(833.333333)
    assert c["slip"] == pytest.approx(166.6666667)
    assert c["proof_load"] == pytest.approx(5.0308324435e-6 * 970e6, rel=1e-6)
    assert c["thread_strip"] == pytest.approx(math.pi * (0.003 - 0.64952 * 0.0005) * 0.006 * 0.5 * 100e6)
    assert c["pull_through"] == pytest.approx(math.pi * 0.0055 * 0.003 * 100e6)
    assert c["axial"] == pytest.approx(833.333333)


def test_lame_press_fit_hand_calculation():
    p, f = interference_capacity(0.01, 0.02, 0.02, 10e-6, 200e9, 70e9, 0.3, 0.33, 0.2)
    expected = 0.001 / ((1 - 0.3) / 200e9 + ((4 + 1) / (4 - 1) + 0.33) / 70e9)
    assert p == pytest.approx(expected)
    assert f == pytest.approx(0.2 * expected * math.pi * 0.01 * 0.02)
    assert interference_capacity(0.01, 0.02, 0.02, 0, 200e9, 70e9, 0.3, 0.33, 0.2) == (
        0,
        0,
    )


def test_snap_beam_hand_calculation():
    assert snap_capacity(2e9, 0.01, 0.001, 0.02, 0.001, 30e6) == pytest.approx(0.625)


@pytest.mark.parametrize("face", FACES)
def test_screw_nut_survives_every_gravity_orientation(tmp_path, face):
    p = fixture_project(tmp_path)
    v = vehicle_for(p, "gravity_" + face)
    run_steps(v)
    assert not v.joints.events
    assert not [d for d in v.joints.diagnostics if d.severity.value == "error"]
    assert all(s.peak_utilization < 1 for s in v.joints.states.values())


def test_missing_nut_zero_capacity_and_inverted_release(tmp_path):
    p = fixture_project(tmp_path, nut=False)
    assert any(d.code == "joint_capacity_zero" for d in compile_connections(p).diagnostics)
    v = vehicle_for(p, "gravity_inverted")
    run_steps(v)
    assert v.joints.events and all(e["capacity"] == 0 for e in v.joints.events if e["event"] == "joint_failed")
    assert any(d.code == "part_detached" for d in v.joints.diagnostics)
    assert abs(v.body_pose("part")[0][2] - v.body_pose("base")[0][2]) > 0.05


def test_tape_holds_then_peels_at_measured_force(tmp_path):
    p = fixture_project(tmp_path, connection="tape")
    v = vehicle_for(p, "gravity_upright")
    run_steps(v, 200)
    assert not v.joints.events
    part = v.body_ids["part"]
    v.data.xfrc_applied[part, 2] = 40
    run_steps(v, 200)
    assert v.joints.events
    event = v.joints.events[0]
    assert event["mode"] == "axial" and event["capacity"] == pytest.approx(20)
    assert event["load"] > event["capacity"]


@pytest.mark.parametrize("face,detaches", [("upright", False), ("inverted", True)])
def test_resting_part_contacts_only(tmp_path, face, detaches):
    p = fixture_project(tmp_path, connection="resting")
    v = vehicle_for(p, "gravity_" + face)
    assert not v.vm.joint_bindings
    run_steps(v, 600)
    assert any(d.code == "part_detached" for d in v.joints.diagnostics) == detaches


def test_junker_transverse_slip_reduces_unlocked_preload_only():
    unlocked = locked = 1.0
    for _ in range(100):
        unlocked = loosen_preload(unlocked, 20, 10 * unlocked, 0.00002, 0.001, False)
        locked = loosen_preload(locked, 20, 10 * locked, 0.00002, 0.001, True)
    assert unlocked == pytest.approx(math.exp(-2))
    assert locked == 1
    assert loosen_preload(1, 5, 10, 0.001, 0.001, False) == 1
    assert loosen_preload(1, 20, 10, 0, 0.001, False) == 1


def test_generated_load_cases_are_mandatory_generic_and_overridable(tmp_path):
    p = fixture_project(tmp_path)
    p.spec.requirements.drop_height = 0.3
    p.spec.requirements.top_speed = 2
    cases = all_scenarios(p)
    assert len(cases) == 24
    assert len([s for s in cases if s.id.startswith("drop_")]) == 14
    assert all(s.initial["joint_load"]["height"] == 0.3 for s in cases if s.id.startswith("drop_"))
    assert next(s for s in cases if s.id == "wall_impact").initial["joint_load"]["speed"] == 2
    assert len([s for s in cases if s.id.startswith("vibration_")]) == 3


def test_each_component_and_hardware_has_mass_and_a_body(tmp_path):
    p = fixture_project(tmp_path)
    vm = build_assembly_model(p)
    m = mujoco.MjModel.from_xml_string(vm.xml)
    assert set(vm.bodies) == {"base", "part", "fastener:S", "nut:S"}
    assert all(m.body(name).mass > 0 for name in vm.bodies)
    assert all(m.body(name).geomnum > 0 for name in vm.bodies)
    assert all(len(members) <= 1 for members in vm.bodies.values())


def test_estimates_are_exhaustively_reported(tmp_path):
    cm = compile_connections(fixture_project(tmp_path))
    assert cm.estimated
    expected = {f"{c.id}.{k}" for c in cm.connections for k, v in c.coefficients.items() if v["status"] == "estimated"}
    assert set(cm.estimated) == expected
    assert all(v["source"] for v in cm.estimated.values())


def test_joint_layer_is_in_every_authored_scenario_and_records_release(tmp_path):
    from r3_model.spec import Scenario
    from r3_sim.scenario import run_scenario

    p = fixture_project(tmp_path, nut=False)
    p.spec.simulation.scenarios.append(Scenario(id="future_mission", duration=0.3))
    outcome = run_scenario(p, "future_mission", render=False)
    assert not outcome.passed
    assert any(d.code == "joint_capacity_zero" for d in outcome.diagnostics)
    assert any(d.code == "joint_failed" for d in outcome.diagnostics)
    assert outcome.summary["connections"]
    recording = np.load(outcome.out_dir / "recording.npz")
    events = [json.loads(e) for e in recording["events"] if e]
    assert any(e.get("event") == "joint_failed" for e in events)
    assert recording["snapshot_qpos"].shape[1] > 7
    from r3_orchestrator.api.projects import _body_frames

    frames = _body_frames(
        outcome.out_dir / "vehicle.xml",
        recording["snapshot_t"],
        recording["snapshot_qpos"],
        30,
        False,
    )
    assert {"base", "part", "fastener:S"} <= set(frames["bodies"])
    assert frames["component_bodies"]["fastener:S"] == "fastener:S"


def test_generic_passive_assembly_can_run_mandatory_cases(tmp_path):
    from r3_sim.scenario import run_scenario
    from r3_orchestrator.readiness import _sim

    p = fixture_project(tmp_path)
    assert _sim(p) is None
    outcome = run_scenario(p, "gravity_inverted", render=False)
    assert outcome.passed, [d.message for d in outcome.diagnostics if d.severity.value == "error"]


@pytest.mark.parametrize("locked", [False, True])
def test_mujoco_vibration_slip_loosens_only_unlocked_screw(tmp_path, locked):
    p = fixture_project(tmp_path)
    f = p.spec.mechanical.fasteners[0]
    f.torque = 0.05
    if not locked:
        # A plain nut with the same dimensions and material as the qualified
        # locknut; only the nylon locking feature is absent.
        plain = p.catalog.fastener(f.nut).model_copy(deep=True)
        plain.id = "plain-test-nut"
        plain.locking = None
        p.catalog.fasteners[plain.id] = plain
        f.nut = plain.id
    v = vehicle_for(p, "gravity_upright")
    part = v.body_ids["part"]
    for k in range(1000):
        v.data.xfrc_applied[part, 0] = 30 * math.sin(2 * math.pi * 40 * k * 0.001)
        v.step()
    fractions = [s.preload_fraction for s in v.joints.states.values()]
    if locked:
        assert fractions == [1.0] * len(fractions)
        assert not any(e["cause"] == "transverse-slip self-loosening" for e in v.joints.events)
    else:
        assert min(fractions) < 0.99


def test_equality_wrench_reports_physical_torque_units(tmp_path):
    p = fixture_project(tmp_path, connection="tape")
    v = vehicle_for(p, "gravity_upright")
    part = v.body_ids["part"]
    for k in range(300):
        v.data.xfrc_applied[part, 5] = 0.3 * min(1, k / 100)
        v.step()
    assert v.joints.states["pad"].peak_torque_nm == pytest.approx(0.3, rel=0.05)
    assert not v.joints.states["pad"].failed
    v.data.xfrc_applied[part, 5] = 3
    run_steps(v, 100)
    assert any(
        e["mode"] == "torsion" and e["load"] > e["capacity"] for e in v.joints.events if e["event"] == "joint_failed"
    )


def test_recorded_parts_play_without_mujoco_mesh_files(tmp_path):
    from r3_model.spec import Scenario
    from r3_sim.scenario import run_scenario
    from r3_orchestrator.api.projects import decode_recording

    p = fixture_project(tmp_path, nut=False)
    p.spec.simulation.scenarios.append(Scenario(id="release_recording", duration=0.1))
    outcome = run_scenario(p, "release_recording", render=False)
    (outcome.out_dir / "vehicle.xml").unlink()
    npz = outcome.out_dir / "recording.npz"
    decoded = decode_recording(str(npz), npz.stat().st_mtime_ns, 30, False)
    assert "part" in decoded["frames"]["bodies"]
    assert len(decoded["frames"]["bodies"]["part"]) > 1
    assert any(e.get("event") == "joint_failed" for e in decoded["events"])


def drivetrain_project(tmp_path: Path) -> Project:
    """Synthetic captive-bearing chassis for drivetrain and HAL tests.

    Mechanical design qualification uses the separate weak/strong fixtures
    above. This bench keeps rated captures intact while testing drive equations.
    """
    catalog = tmp_path / "catalog" / "components"
    catalog.mkdir(parents=True)
    catalog.joinpath("bench.yaml").write_text(
        yaml.safe_dump(
            {
                "components": [
                    {
                        "id": "bench-base",
                        "name": "Bench base",
                        "kind": "structure",
                        "mechanical": {
                            "shape": "box",
                            "size": [0.32, 0.13, 0.004],
                            "mass": 1.4,
                            "material": "Aluminum 6061-T6",
                        },
                    },
                    {
                        "id": "bench-controller",
                        "name": "6 V bench controller",
                        "kind": "computer",
                        "mechanical": {
                            "shape": "box",
                            "size": [0.02, 0.02, 0.01],
                            "mass": 0.02,
                            "material": "Aluminum 6061-T6",
                        },
                        "pins": {
                            "PWM": {"role": "pwm_out"},
                            "6V": {"role": "power_in", "voltage_nominal": 6, "voltage_min": 4.5, "voltage_max": 6.5},
                            "GND": {"role": "ground"},
                        },
                        "connectors": {
                            "J1": {
                                "type": "header-1x3-2.54",
                                "position": [0, 0, 0],
                                "direction": [0, 0, 1],
                                "pins": {"1": "PWM", "2": "6V", "3": "GND"},
                            }
                        },
                        "behavior": {
                            "model": "load.computer",
                            "params": {"power_idle": 0.1, "power_active": 0.2, "brownout_voltage": 4.5, "boot_time": 0},
                        },
                    },
                ]
            }
        )
    )
    components = {"base": {"type": "bench-base", "pose": {"xyz": [0.13, 0, 0.04]}}}
    for name, type_id, position in [
        ("battery", "lipo-2s-5000-25c", [0.07, 0, 0.06]),
        ("motor", "motor-540-brushed", [0.2, 0.02, 0.09]),
        ("esc", "esc-brushed-60a", [0.2, -0.04, 0.06]),
        ("controller", "bench-controller", [0.1, 0, 0.12]),
    ]:
        components[name] = {"type": type_id, "mount": "base", "pose": {"xyz": position}}
    coefficients = lambda values: {
        k: {"value": v, "status": "datasheet", "source": "Synthetic rated captive-bearing/structural-bond fixture"}
        for k, v in values.items()
    }
    interfaces = [
        {
            "id": "mount_" + name,
            "kind": "adhesive",
            "parts": ["base", name],
            "axis_dir": [0, 0, 1],
            "params": {
                "physics": coefficients({"area": 0.001, "shear_strength": 1e7, "peel_strength": 1e7, "radius": 0.03})
            },
        }
        for name in ("battery", "motor", "esc", "controller")
    ]
    joints = []
    for name, x, y in [("fl", 0.26, 0.1), ("fr", 0.26, -0.1), ("rl", 0, 0.1), ("rr", 0, -0.1)]:
        cid = "wheel_" + name
        components[cid] = {"type": "wheel-110x45", "mount": "base", "pose": {"xyz": [x, y, 0.055]}}
        joints.append(
            {
                "id": "spin_" + name,
                "type": "continuous",
                "parent": "base",
                "child": cid,
                "origin": [x, y, 0.055],
                "axis": [0, 1, 0],
                "damping": 0.00002,
                "interface": "capture_" + name,
            }
        )
        interfaces.append(
            {
                "id": "capture_" + name,
                "kind": "revolute_fit",
                "parts": ["base", cid],
                "axis_point": [x, y, 0.055],
                "axis_dir": [0, 1, 0],
                "params": {"physics": coefficients({"retention_force": 10000, "shear_force": 10000, "radius": 0.006})},
            }
        )
    harnesses = [
        {
            "id": "power",
            "connectors": {
                "A": {"type": "integral", "mates": "battery.J1"},
                "B": {"type": "integral", "mates": "esc.J_PWR"},
            },
            "wires": [
                {"id": "P", "from": "A.1", "to": "B.1", "awg": 12},
                {"id": "G", "from": "A.2", "to": "B.2", "awg": 12},
            ],
        },
        {
            "id": "motor_lead",
            "connectors": {
                "A": {"type": "integral", "mates": "esc.J_MOT"},
                "P": {"type": "bullet-3.5-female", "mates": "motor.J_POS"},
                "N": {"type": "bullet-3.5-female", "mates": "motor.J_NEG"},
            },
            "wires": [
                {"id": "MP", "from": "A.1", "to": "P.1", "awg": 14},
                {"id": "MN", "from": "A.2", "to": "N.1", "awg": 14},
            ],
        },
        {
            "id": "signal",
            "connectors": {
                "A": {"type": "integral", "mates": "esc.J_SIG"},
                "B": {"type": "servo-3-female", "mates": "controller.J1"},
            },
            "wires": [
                {"id": "S", "from": "A.1", "to": "B.1", "awg": 22},
                {"id": "V", "from": "A.2", "to": "B.2", "awg": 22},
                {"id": "SG", "from": "A.3", "to": "B.3", "awg": 22},
            ],
        },
    ]
    spec = {
        "robot": {"name": "Captive-bearing drive bench", "mobility": "mobile"},
        "components": components,
        "mechanical": {
            "joints": joints,
            "interfaces": interfaces,
            "transmissions": [
                {
                    "id": "drive",
                    "type": "differential",
                    "actuator": "motor",
                    "joints": ["spin_rl", "spin_rr"],
                    "ratio": 9.066,
                    "efficiency": 0.88,
                }
            ],
        },
        "electrical": {"harnesses": harnesses},
        "software": {
            "bindings": {
                "esc_pwm": {
                    "interface": "pwm",
                    "computer": "controller",
                    "pins": ["controller.J1.1"],
                    "device": "pwmchip0",
                    "channel": 2,
                    "expects": ["esc.J_SIG.1"],
                }
            }
        },
        "simulation": {
            "scenarios": [
                {
                    "id": "full_throttle",
                    "duration": 10,
                    "initial": {"battery_soc": 1, "mode": "manual"},
                    "expect": {"min_top_speed_mps": 8, "brownouts": 0},
                }
            ]
        },
    }
    tmp_path.joinpath("robot.yaml").write_text(yaml.safe_dump(spec))
    return Project.load(tmp_path)


def arm_project(tmp_path: Path, nut: bool = True) -> Project:
    """Two-link arm on a bolted mounting plate, without any locomotion model."""
    folder = tmp_path / "catalog" / "components"
    folder.mkdir(parents=True)
    folder.joinpath("arm.yaml").write_text(
        yaml.safe_dump(
            {
                "components": [
                    {
                        "id": "foundation",
                        "name": "Foundation plate",
                        "kind": "structure",
                        "mechanical": {
                            "shape": "box",
                            "size": [0.08, 0.06, 0.005],
                            "mass": 0.2,
                            "material": "Aluminum 6061-T6",
                        },
                    },
                    {
                        "id": "mount",
                        "name": "Mounting plate",
                        "kind": "structure",
                        "mechanical": {
                            "shape": "box",
                            "size": [0.06, 0.04, 0.005],
                            "mass": 0.1,
                            "material": "Aluminum 6061-T6",
                        },
                    },
                    {
                        "id": "arm-link",
                        "name": "Arm link",
                        "kind": "structure",
                        "mechanical": {
                            "shape": "box",
                            "size": [0.1, 0.02, 0.01],
                            "mass": 0.05,
                            "material": "Aluminum 6061-T6",
                        },
                    },
                ]
            }
        )
    )
    physics = {
        k: {"value": v, "status": "datasheet", "source": "Synthetic captive-bearing rating"}
        for k, v in {"retention_force": 1000, "shear_force": 1000, "radius": 0.003}.items()
    }
    spec = {
        "robot": {"name": "Two-link stationary arm", "mobility": "stationary"},
        "components": {
            "foundation": {"type": "foundation", "pose": {"xyz": [0, 0, 0.01]}},
            "mount": {"type": "mount", "mount": "foundation", "pose": {"xyz": [0, 0, 0.015]}},
            "upper": {"type": "arm-link", "mount": "mount", "pose": {"xyz": [0.05, 0, 0.03]}},
            "forearm": {"type": "arm-link", "mount": "upper", "pose": {"xyz": [0.15, 0, 0.03]}},
        },
        "mechanical": {
            "fasteners": [
                {
                    "id": "mount_screw",
                    "type": "shcs-m3",
                    "length": 0.02,
                    "position": [0, 0, 0.02],
                    "axis": [0, 0, -1],
                    "clamps": ["foundation", "mount"],
                    "grip": 0.01,
                    **({"nut": "nut-m3-nyloc"} if nut else {}),
                }
            ],
            "joints": [
                {
                    "id": "shoulder",
                    "type": "revolute",
                    "parent": "mount",
                    "child": "upper",
                    "origin": [0, 0, 0.035],
                    "axis": [0, 1, 0],
                    "limits": [-0.6, 0.6],
                    "interface": "shoulder_capture",
                },
                {
                    "id": "elbow",
                    "type": "revolute",
                    "parent": "upper",
                    "child": "forearm",
                    "origin": [0.1, 0, 0.035],
                    "axis": [0, 1, 0],
                    "limits": [-0.6, 0.6],
                    "interface": "elbow_capture",
                },
            ],
            "interfaces": [
                {
                    "id": name,
                    "kind": "revolute_fit",
                    "parts": parts,
                    "axis_point": point,
                    "axis_dir": [0, 1, 0],
                    "params": {"physics": physics},
                }
                for name, parts, point in [
                    ("shoulder_capture", ["mount", "upper"], [0, 0, 0.035]),
                    ("elbow_capture", ["upper", "forearm"], [0.1, 0, 0.035]),
                ]
            ],
        },
    }
    tmp_path.joinpath("robot.yaml").write_text(yaml.safe_dump(spec))
    return Project.load(tmp_path)


@pytest.mark.parametrize("nut", [True, False])
def test_non_vehicle_arm_uses_shared_assembly_and_releases_unretained_mount(tmp_path, nut):
    p = arm_project(tmp_path, nut)
    vm = build_assembly_model(p)
    assert not hasattr(vm, "wheels") and "tire_" not in vm.xml
    assert "joint_fixture_weld" in vm.xml
    case = next(s for s in all_scenarios(p) if s.id == "gravity_inverted")
    configure_model(vm, case, p)
    robot = RobotDynamics(p, vm)
    robot.load_case = LoadCase(robot, case, p, 1)
    assert robot.drag_area == 0
    run_steps(robot, 600)
    failures = [e for e in robot.joints.events if e["event"] == "joint_failed"]
    if nut:
        assert not failures
        assert not [d for d in robot.joints.diagnostics if d.severity.value == "error"]
    else:
        assert failures and any(d.code == "joint_capacity_zero" for d in vm.diagnostics)
        assert any(d.code == "part_detached" for d in robot.joints.diagnostics)


def test_stationary_wall_impact_skips_with_diagnostic_and_drop_releases_base(tmp_path):
    from r3_sim.scenario import run_scenario

    p = arm_project(tmp_path)
    result = run_scenario(p, "wall_impact", render=False)
    assert any(d.code == "joint_impact_not_applicable" for d in result.diagnostics)
    vm = build_assembly_model(p)
    configure_model(vm, next(s for s in all_scenarios(p) if s.id == "drop_upright"), p)
    assert "joint_fixture_weld" not in vm.xml

@pytest.mark.parametrize("face,detaches", [("upright", False), ("inverted", True)])
def test_unretained_payload_in_a_real_pocket(tmp_path, face, detaches):
    p = fixture_project(tmp_path, connection="resting")
    from r3_catalog.schema import Solid
    base = p.catalog.component("plate").mechanical
    # Four walls are part of the same pocket body. The payload has clearance,
    # a supporting floor, and no lid, retainer or equality constraint.
    solids = [dict(kind="box", name="floor", position=[0,0,.0025], size=[.08,.08,.005])]
    for axis in range(2):
        for sign in (-1,1):
            center=[0.,0.,.015];center[axis]=sign*.034
            size=[.068,.068,.02];size[axis]=.004
            solids.append(dict(kind="box",name=f"wall_{axis}_{sign}",position=center,size=size))
    # Give the pocket a distinct catalog entry so the payload remains a plate.
    pocket=p.catalog.component("plate").model_copy(deep=True)
    pocket.id="pocket";pocket.mechanical.solids=[Solid.model_validate(v) for v in solids]
    p.catalog.components["pocket"]=pocket;p.spec.components["base"].type="pocket"
    v=vehicle_for(p,"gravity_"+face)
    assert not v.vm.joint_bindings
    run_steps(v,600)
    assert any(d.code=="part_detached" for d in v.joints.diagnostics)==detaches


def test_moving_carrier_preserves_part_mass_com_and_inertia(tmp_path):
    p=arm_project(tmp_path, nut=True)
    vm=build_assembly_model(p)
    m=mujoco.MjModel.from_xml_string(vm.xml);d=mujoco.MjData(m)
    mujoco.mj_forward(m,d)
    from r3_sim.assembly import catalog_mass_properties
    for cid in vm.moving_joints:
        part=catalog_mass_properties(p,cid)
        ids=[m.body(cid).id,m.body("mount:"+cid).id]
        masses=m.body_mass[ids]
        com=np.average(d.xipos[ids],axis=0,weights=masses)
        assert masses.sum()==pytest.approx(part.mass,rel=1e-10)
        np.testing.assert_allclose(com,part.com+[0,0,.0005],atol=1e-10)
        inertia=np.zeros((3,3))
        for bid in ids:
            r=d.xipos[bid]-com;rot=d.ximat[bid].reshape(3,3)
            inertia+=rot@np.diag(m.body_inertia[bid])@rot.T+m.body_mass[bid]*((r@r)*np.eye(3)-np.outer(r,r))
        np.testing.assert_allclose(inertia,part.inertia,rtol=1e-7,atol=1e-12)


def test_anonymous_strength_is_a_structured_blocking_error(tmp_path):
    p=fixture_project(tmp_path,connection="tape")
    p.spec.mechanical.interfaces[0].params["physics"]["shear_strength"]=1e9
    cm=compile_connections(p)
    assert {"joint_coefficient_invalid","joint_capacity_zero"}<={d.code for d in cm.diagnostics}


def test_unretained_cad_subpart_cannot_escape_detachment_checks(tmp_path):
    p=fixture_project(tmp_path,connection="resting")
    # A separate CAD part cannot inherit its owner's rigidity without a join.
    cad={"parts":{"loose_coupon":{"owner":"base","mass":.01,"com":[0,0,.05],
          "inertia":[1e-7,1e-7,1e-7,0,0,0],
          "bbox_min":[-.005,-.005,.045],"bbox_max":[.005,.005,.055]}}}
    (p.build_dir("cad")/"cad_model.json").write_text(json.dumps(cad))
    v=vehicle_for(p,"gravity_upright")
    assert "loose_coupon" in v.vm.bodies
    run_steps(v,100)
    assert any(d.code=="part_detached" and "loose_coupon" in d.subjects for d in v.joints.diagnostics)


def test_missing_fastener_strength_never_inherits_anonymous_defaults(tmp_path):
    p=fixture_project(tmp_path)
    p.catalog.fasteners["shcs-m3"].joint_physics={}
    model=compile_connections(p)
    assert any(d.code=="joint_capacity_zero" for d in model.diagnostics)
    assert model.estimated and any("Missing joint input" in v["source"] for v in model.estimated.values())


def test_slender_links_keep_positive_inertia_without_changing_assembly_mass(tmp_path):
    p=arm_project(tmp_path)
    p.catalog.component("arm-link").mechanical.size=[1,.0003,.0003]
    vm=build_assembly_model(p)
    model=mujoco.MjModel.from_xml_string(vm.xml)
    assert np.all(model.body_inertia[model.body_mass > 0] > 0)
    assert any(key.startswith("solver.carrier_mass_fraction:") for key in vm.connections.estimated)
    assert sum(model.body_mass)==pytest.approx(vm.total_mass,rel=1e-10)


def test_early_software_shutdown_cannot_qualify(tmp_path: Path) -> None:
    """A future scenario must not pass by closing its HAL before the load ends."""
    import sys
    from r3_model.spec import Scenario
    from r3_sim.scenario import run_scenario

    project = fixture_project(tmp_path)
    project.spec.simulation.scenarios.append(Scenario(id="early_exit", duration=0.2))
    outcome = run_scenario(project, "early_exit", software_cmd=[sys.executable, "-c",
        "from r3_hal import open_hal; hal = open_hal('fixture'); hal.close()"], render=False)
    assert not outcome.passed
    assert "scenario_incomplete" in {diagnostic.code for diagnostic in outcome.diagnostics}
    assert outcome.summary["metrics"]["sim_time_s"] < 0.2
    assert (project.root / "build/sim/early_exit/recording.npz").exists()


@pytest.mark.parametrize("nut,tail,expected", [(True, 0.02, 0.08), (False, 0.02, 0.02), (False, None, 0.08)])
def test_failed_case_replay_tail_never_qualifies_a_prefix(tmp_path, nut, tail, expected):
    from r3_model.spec import Scenario
    from r3_sim.scenario import run_scenario

    project = fixture_project(tmp_path, nut=nut)
    project.spec.simulation.joint_failure_replay_duration = tail
    project.spec.simulation.scenarios.append(Scenario(id="tail", duration=0.08, expect={"brownouts": 0}))
    outcome = run_scenario(project, "tail", render=False)
    assert outcome.summary["metrics"]["sim_time_s"] == pytest.approx(expected)
    if nut:
        assert outcome.passed
    else:
        assert not outcome.passed
        assert "joint_capacity_zero" in {d.code for d in outcome.diagnostics}
    if expected < 0.08:
        assert "scenario_incomplete" in {d.code for d in outcome.diagnostics}
        assert not any(check["pass"] for check in outcome.summary["checks"])
        assert any(event["event"] == "joint_failure_stop" for event in outcome.summary["events"])
    else:
        assert outcome.summary["metrics"]["simulation_complete"]
