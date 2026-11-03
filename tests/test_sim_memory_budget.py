"""Arena storage is configurable without changing contact or physics checks."""
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest
from pydantic import ValidationError

from r3_model.spec import Simulation
from r3_sim.assembly import build_assembly_model
from r3_sim import stage
from r3_wiring.mujoco_cables import CablePlan, emit_mjcf, plan_cables
from test_cad_pulled_consumers import project, pull


@pytest.mark.parametrize('value', [0, -1, 1.5, True, '16'])
def test_arena_budget_requires_positive_integer_mib(value):
    with pytest.raises(ValidationError):
        Simulation(mujoco_memory_mib=value)
    assert Simulation().mujoco_memory_mib is None


def test_native_model_budget_changes_storage_only(project):
    pull(project)
    project.spec.robot.mobility = 'mobile'
    default = build_assembly_model(project)
    assert ET.fromstring(default.xml).find('size') is None
    project.spec.simulation.mujoco_memory_mib = 16
    configured = build_assembly_model(project)
    root = ET.fromstring(configured.xml)
    assert root.find('size').attrib == {'memory': str(16 * 1024 ** 2)}
    models = [mujoco.MjModel.from_xml_string(a.xml) for a in (default, configured)]
    assert models[1].narena == 16 * 1024 ** 2
    for name in ('body_mass', 'body_inertia', 'geom_size', 'geom_contype', 'geom_conaffinity', 'eq_data'):
        np.testing.assert_array_equal(getattr(models[0], name), getattr(models[1], name))
    assert models[0].opt.timestep == models[1].opt.timestep
    assert models[0].opt.tolerance == models[1].opt.tolerance
    assert models[0].nconmax == models[1].nconmax  # no discarded-contact workaround
    states = []
    for model in models:
        data = mujoco.MjData(model)
        contacted = False
        for _ in range(2000):
            mujoco.mj_step(model, data)
            contacted |= data.ncon > 0
            assert not any(w.number for w in data.warning)
        assert contacted  # settled/sleeping bodies may have no active final contact rows
        states.append((data.qpos.copy(), data.qvel.copy()))
    np.testing.assert_array_equal(states[0][0], states[1][0])
    np.testing.assert_array_equal(states[0][1], states[1][1])


def test_standalone_cable_world_receives_the_same_project_budget(project):
    pull(project)
    project.spec.simulation.mujoco_memory_mib = 16
    plan = plan_cables(project, {'wires': {}, 'harnesses': {}})
    assert plan.arena_memory_bytes == 16 * 1024 ** 2
    root = ET.fromstring('<mujoco><worldbody><body name="fixture"><geom size=".01"/></body></worldbody></mujoco>')
    body = root.find('worldbody/body')
    empty = CablePlan([], [], arena_memory_bytes=plan.arena_memory_bytes)
    emit_mjcf(root, empty, lambda _: body, lambda _: np.zeros(3), lambda _: 'fixture')
    assert mujoco.MjModel.from_xml_string(ET.tostring(root, encoding='unicode')).narena == plan.arena_memory_bytes
    root.find('size').set('memory', '1024')
    with pytest.raises(ValueError, match='budgets differ'):
        emit_mjcf(root, empty, lambda _: body, lambda _: np.zeros(3), lambda _: 'fixture')


def test_arena_exhaustion_stays_failed_and_does_not_retry_other_scenarios(project, monkeypatch):
    pull(project)
    calls = []
    def exhausted(_project, scenario, *args, **kwargs):
        calls.append(scenario)
        raise mujoco.FatalError('mj_stackAlloc: out of memory, narena = 1048576')
    monkeypatch.setattr(stage, 'run_scenario', exhausted)
    result = stage.run(project, scenarios=['gravity_upright', 'gravity_inverted'], render=False)
    assert not result.ok
    assert calls == ['gravity_upright']
    assert result.metrics['scenarios_total'] == 2
    assert result.metrics['scenarios_completed'] == 0
    assert result.metrics['scenarios_passed'] == 0
    assert result.metrics['gravity_upright'] == {'passed': False, 'completed': False}
    error = next(d for d in result.diagnostics if d.code == 'simulation_arena_exhausted')
    assert error.severity.value == 'error'
    assert 'simulation.mujoco_memory_mib' in error.hint


@pytest.mark.parametrize('message', ['invalid actuator', 'invalid definition: narena = 1024'])
def test_other_native_fatal_errors_are_not_reclassified(project, monkeypatch, message):
    pull(project)
    def broken(*args, **kwargs):
        raise mujoco.FatalError(message)
    monkeypatch.setattr(stage, 'run_scenario', broken)
    with pytest.raises(mujoco.FatalError, match='invalid'):
        stage.run(project, scenarios=['gravity_upright'], render=False)


def test_agent_spec_patch_can_set_and_remove_arena_budget_without_touching_cad(project):
    import hashlib
    from r3_model.project import Project
    from r3_orchestrator import spec_ops

    pull(project)
    manifest = project.root / 'cad/pulled/pull.json'
    before = hashlib.sha256(manifest.read_bytes()).hexdigest()
    read = spec_ops.get_spec(project.root)
    changed = spec_ops.patch_spec(project.root, {'simulation': {'mujoco_memory_mib': 32}}, read['sha256'])
    assert changed['ok'], changed
    assert changed['files_changed'] == ['robot.yaml']
    assert Project.load(project.root).spec.simulation.mujoco_memory_mib == 32
    read = spec_ops.get_spec(project.root)
    invalid = spec_ops.patch_spec(project.root, {'simulation': {'mujoco_memory_mib': -1}}, read['sha256'])
    assert not invalid['ok'] and invalid['code'] == 'spec_invalid'
    assert spec_ops.get_spec(project.root)['sha256'] == read['sha256']
    removed = spec_ops.patch_spec(project.root, {'simulation': {'mujoco_memory_mib': None}}, read['sha256'])
    assert removed['ok'], removed
    assert Project.load(project.root).spec.simulation.mujoco_memory_mib is None
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == before
