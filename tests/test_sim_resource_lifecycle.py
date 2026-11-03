"""Native worlds and firmware resources must not survive completed experiments."""
import gc
import subprocess
import sys
import types
import weakref

import mujoco
import numpy as np
import pytest

from r3_model.spec import ProjectDeviceSpec, Scenario
from r3_sim.overlap import MujocoWorker
from r3_sim import scenario
from test_cad_pulled_consumers import project, pull


def native_worker(monkeypatch):
    monkeypatch.setenv('R3_SIM_OVERLAP', '1')
    model = mujoco.MjModel.from_xml_string('<mujoco><worldbody><body><freejoint/><geom size=".01" mass="1"/></body></worldbody></mujoco>')
    data = mujoco.MjData(model)
    worker = MujocoWorker(model, data)
    if not worker.enabled:
        pytest.skip('native overlap library unavailable')
    return model, data, worker


def test_close_finishes_pending_native_work_and_releases_its_thread(monkeypatch):
    model, data, worker = native_worker(monkeypatch)
    refs = [weakref.ref(v) for v in (model, data, worker)]
    thread = worker._thread
    worker.start('mj_step1')
    worker.wait()
    worker.start('mj_step2')
    worker.close()
    assert data.time == pytest.approx(model.opt.timestep)
    assert np.isfinite(data.qpos).all() and not any(w.number for w in data.warning)
    assert not thread.is_alive()
    worker.close()
    with pytest.raises(RuntimeError, match='closed'):
        worker.start('mj_step1')
    del worker, model, data
    gc.collect()
    assert all(ref() is None for ref in refs)


def test_worker_error_propagates_and_cannot_leave_close_waiting_forever(monkeypatch):
    _, _, worker = native_worker(monkeypatch)
    def broken(*args):
        raise ValueError('injected native-call failure')
    worker._lib = types.SimpleNamespace(mj_step1=broken)
    worker.start('mj_step1')
    with pytest.raises(ValueError, match='injected native-call failure'):
        worker.wait()
    worker.close()
    assert not worker._thread.is_alive()


@pytest.mark.parametrize('fail', [False, True])
def test_real_scenario_closes_native_worker_on_success_or_error(project, monkeypatch, fail):
    pull(project)
    project.spec.simulation.scenarios = [Scenario(id='observe', duration=.005)]
    folder = project.root / 'simulation'
    folder.mkdir()
    (folder / 'probe.py').write_text('''class Probe:
    def __init__(self, context): self.context = context
    def step(self, dt): pass
    def request(self, operation, payload): return None
def create_device(context): return Probe(context)
''')
    project.spec.simulation.devices = {'probe': ProjectDeviceSpec(component='coupon', source='simulation/probe.py')}
    monkeypatch.setenv('R3_SIM_OVERLAP', '1')
    threads, refs, programs, registries = [], [], [], []
    original = scenario.CoSimKernel
    def capture(*args, **kwargs):
        kernel = original(*args, **kwargs)
        if not kernel.vehicle._worker.enabled:
            pytest.skip('native overlap library unavailable')
        threads.append(kernel.vehicle._worker._thread)
        registries.append(kernel.project_devices)
        refs.extend([weakref.ref(kernel.vehicle.model), weakref.ref(kernel.vehicle.data)])
        if fail:
            def broken():
                raise RuntimeError('injected scenario failure')
            kernel.step = broken
        return kernel
    monkeypatch.setattr(scenario, 'CoSimKernel', capture)
    if fail:
        original_software = scenario._Software
        def running_program(*args, **kwargs):
            software = original_software(*args, **kwargs)
            software.proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            programs.append(software.proc)
            return software
        monkeypatch.setattr(scenario, '_Software', running_program)
    if fail:
        try:
            with pytest.raises(RuntimeError, match='injected scenario failure'):
                scenario.run_scenario(project, 'observe', render=False)
            assert programs and all(p.poll() is not None for p in programs)
        finally:
            for process in programs:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    else:
        outcome = scenario.run_scenario(project, 'observe', render=False)
        assert outcome.summary['metrics']['sim_time_s'] >= .005
        assert outcome.summary['metrics']['simulation_complete']
    gc.collect()
    assert threads and all(not thread.is_alive() for thread in threads)
    assert registries and not any(registries)
    assert all(ref() is None for ref in refs)


def test_discarded_standalone_runtime_stops_its_native_worker(project, monkeypatch):
    from r3_sim.assembly import build_assembly_model
    from r3_sim.dynamics import RobotDynamics
    pull(project)
    monkeypatch.setenv('R3_SIM_OVERLAP', '1')
    dynamics = RobotDynamics(project, build_assembly_model(project))
    if not dynamics._worker.enabled:
        pytest.skip('native overlap library unavailable')
    thread = dynamics._worker._thread
    ref = weakref.ref(dynamics.data)
    del dynamics
    gc.collect()
    assert not thread.is_alive() and ref() is None


def test_camera_close_releases_its_native_scratch_world(monkeypatch):
    from r3_sim.devices.camera import VirtualCamera
    model, _, worker = native_worker(monkeypatch)
    worker.close()
    camera = VirtualCamera('synthetic', 'port', 'view', {}, model, np.random.default_rng(1))
    camera._scratch = mujoco.MjData(model)
    ref = weakref.ref(camera._scratch)
    camera.close()
    assert camera._scratch is None and ref() is None
