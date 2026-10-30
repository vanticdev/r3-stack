"""Regression tests for the owned IMU and a straight, independent failsafe stop."""
from pathlib import Path

import numpy as np
import pytest

from r3_kicad.arbiter import ArbiterParams, FailsafeArbiter, ArbiterState
from r3_model.project import Project
from r3_sim.evaluation import evaluate

BENCH = Path(__file__).resolve().parents[1] / 'tests/fixtures/bench'


def test_owned_imu_reachable_and_no_duplicate_address():
    project = Project.load(BENCH)
    assert project.spec.components['imu'].owned
    assert project.graph.connected('pi.J8.3', 'imu.SDA')
    assert project.graph.connected('pi.J8.5', 'imu.SCL')
    devices = [(cid, interface.address) for cid, component in project.spec.components.items()
               for interface in project.catalog.component(component.type).software if interface.protocol == 'mpu6050']
    assert devices == [('imu', 0x68)]
    pos, rotation = project.graph.component_pose('imu')
    assert rotation == pytest.approx(np.eye(3))
    assert pos == pytest.approx(project.spec.components["imu"].pose.xyz)


def test_yaw_gate_rejects_spin_across_pi_boundary_and_no_trigger():
    rec = {'t': np.arange(40)*0.1, 'x': np.zeros(40), 'y': np.zeros(40), 'speed': np.ones(40),
           'yaw': (np.linspace(3, 3+np.radians(200), 40)+np.pi) % (2*np.pi)-np.pi}
    check = evaluate({'max_failsafe_yaw_deg': 30}, rec, {'fault': {'t': 0}})[0]
    assert not check.passed and check.measured == pytest.approx(200)
    rec['yaw'] = np.linspace(0, np.radians(10), 40)
    assert evaluate({'max_failsafe_yaw_deg': 30}, rec, {'fault': {'t': 0}})[0].passed
    assert not evaluate({'max_failsafe_yaw_deg': 30}, rec, {})[0].passed


def test_modulated_arbiter_brake_never_rearms_reverse():
    params = ArbiterParams(timeout=.02, brake_pattern_us=(1478, 1484))
    arb = FailsafeArbiter(params)
    for t in np.arange(0, .1, .01):
        arb.heartbeat_edge(float(t))
        arb.output_pulse(float(t), 1500)
    arb.output_pulse(.1, 1800)
    values = [arb.output_pulse(float(t), 1800) for t in np.arange(.14, 2, .02)]
    assert arb.state is ArbiterState.FAILSAFE and set(values) == {1478, 1484}
    assert arb.esc.value == 'brake'
    assert arb.output_pulse(4, 1800) == 1500
    with pytest.raises(ValueError):
        FailsafeArbiter(ArbiterParams(brake_pattern_us=(1000, 1500)))
