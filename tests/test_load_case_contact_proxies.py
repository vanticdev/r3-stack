"""Dedicated load fixtures must include real environment contact proxies."""
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from r3_sim.load_cases import LoadCase


@pytest.mark.parametrize('kind,retained', [('drop', True), ('impact', True), ('impact', False)])
def test_fixture_positions_the_contact_pad_at_the_requested_surface_gap(kind, retained):
    model = mujoco.MjModel.from_xml_string('''<mujoco>
      <option timestep=".00025"/>
      <worldbody>
        <geom name="joint_test_ground" type="plane" size="1 1 .1" contype="0" conaffinity="3"/>
        <geom name="joint_impact_wall" type="plane" pos="1 0 0" zaxis="-1 0 0" size="1 1 .1" contype="0" conaffinity="3"/>
        <body name="fixture" pos="0 0 .1">
          <freejoint/>
          <inertial pos=".1 0 0" mass="1" diaginertia=".01 .01 .01"/>
          <geom name="frame" type="box" pos="0 0 .1" size=".01 .01 .01" contype="1" conaffinity="1"/>
          <geom name="contact_pad" type="sphere" pos=".1 0 0" size=".03" contype="2" conaffinity="0"/>
        </body>
        <body name="payload" pos=".1 0 .1">
          <freejoint/>
          <inertial pos="0 0 0" mass=".1" diaginertia=".001 .001 .001"/>
        </body>
      </worldbody>
      <equality><weld name="retention" body1="fixture" body2="payload"/></equality>
    </mujoco>''')
    data = mujoco.MjData(model)
    data.eq_active[0] = retained
    references = model.eq_data.copy()
    mass, inertia = model.body_mass.copy(), model.body_inertia.copy()
    vehicle = SimpleNamespace(model=model, data=data, reset_assembly_reference=lambda: None)
    load = dict(kind=kind, normal=[0, 0, 1], height=.1, speed=.2)
    case = LoadCase(vehicle, SimpleNamespace(initial={'joint_load': load}), None, 1)
    np.testing.assert_array_equal(model.eq_data, references)
    np.testing.assert_array_equal(model.body_mass, mass)
    np.testing.assert_array_equal(model.body_inertia, inertia)
    assert bool(data.eq_active[0]) == retained
    pad = model.geom('contact_pad').id
    ground = model.geom('joint_test_ground').id
    clearance = mujoco.mj_geomDistance(model, data, pad, ground, 10, None)
    assert clearance == pytest.approx(.1 if kind == 'drop' else 0, abs=1e-9)
    if kind == 'impact':
        wall = model.geom('joint_impact_wall').id
        gap = mujoco.mj_geomDistance(model, data, pad, wall, 10, None)
        assert gap == pytest.approx(.2 * model.opt.timestep, abs=1e-9)
        for _ in range(10):
            mujoco.mj_step(model, data)
            case.observe()
            assert not any(w.number for w in data.warning)
        assert case.impact_peak_force > 0
        assert case.impact_first_time <= .002
    else:
        contacted = False
        for _ in range(8000):
            mujoco.mj_step(model, data)
            contacted |= any(ground in contact.geom and pad in contact.geom for contact in data.contact)
            assert not any(w.number for w in data.warning)
        assert contacted
        assert mujoco.mj_geomDistance(model, data, pad, ground, 10, None) >= -.001
        assert np.linalg.norm(data.qvel) < .01
