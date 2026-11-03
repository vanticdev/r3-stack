"""Reuse native compilation, never altered triangles or old simulated outcomes."""
import hashlib

import mujoco
import numpy as np
import pytest
import trimesh

from r3_sim import compiled_models as cache
from test_cad_pulled_consumers import project
from test_cad_pull import native_snapshot


@pytest.fixture
def native_surface(tmp_path, monkeypatch):
    mesh = tmp_path / 'synthetic-coupon.stl'
    mesh.write_bytes(trimesh.creation.box([.02, .02, .02]).export(file_type='stl'))
    xml = f'''<mujoco><option timestep=".001"/>
      <worldbody><geom name="floor" type="plane" size="1 1 .1"/>
      <body name="coupon" pos="0 0 .03"><freejoint/>
      <inertial mass="1" pos="0 0 0" diaginertia=".001 .001 .001"/>
      <flexcomp name="surface" type="mesh" file="{mesh}" rigid="true" radius="0"/>
      </body></worldbody></mujoco>'''
    calls, original = [], cache._compile
    def compile_source(xml):
        calls.append(xml)
        return original(xml)
    monkeypatch.setattr(cache, '_compile', compile_source)
    return mesh, xml, calls


def test_native_cache_hit_preserves_triangles_contacts_motion_and_independent_models(tmp_path, native_surface):
    mesh, xml, calls = native_surface
    original = hashlib.sha256(mesh.read_bytes()).hexdigest()
    models = cache.CompiledModels(tmp_path / 'cache')
    cold, warm = models.load(xml), models.load(xml)
    assert len(calls) == 1
    assert cold is not warm and cold.nflex == warm.nflex == 1
    for name in ('body_mass', 'body_inertia', 'geom_contype', 'eq_data', 'flex_elem', 'flex_vert0'):
        np.testing.assert_array_equal(getattr(cold, name), getattr(warm, name))
    assert cold.opt.timestep == warm.opt.timestep
    assert cold.opt.tolerance == warm.opt.tolerance
    states = []
    for model in (cold, warm):
        data, contact = mujoco.MjData(model), False
        for _ in range(1000):
            mujoco.mj_step(model, data)
            contact |= data.ncon > 0
        assert contact and not any(w.number for w in data.warning)
        assert .009 <= data.qpos[2] <= .011
        states.append((data.qpos.copy(), data.qvel.copy()))
    np.testing.assert_array_equal(states[0][0], states[1][0])
    np.testing.assert_array_equal(states[0][1], states[1][1])
    cold.body_mass[1] = 99
    assert models.load(xml).body_mass[1] == 1
    assert hashlib.sha256(mesh.read_bytes()).hexdigest() == original


def test_geometry_xml_and_native_runtime_changes_invalidate_the_cache(tmp_path, native_surface, monkeypatch):
    mesh, xml, calls = native_surface
    models = cache.CompiledModels(tmp_path / 'cache')
    models.load(xml)
    mesh.write_bytes(trimesh.creation.box([.03, .02, .02]).export(file_type='stl'))
    models.load(xml)
    changed = xml.replace('mass="1"', 'mass="2"')
    assert models.load(changed).body_mass[1] == 2
    monkeypatch.setattr(cache, '_runtime_identity', lambda: {'native-library': 'changed'})
    models.load(changed)
    assert len(calls) == 4


def test_corruption_and_receipt_symlinks_recompile_without_touching_other_files(tmp_path, native_surface):
    _, xml, calls = native_surface
    folder = tmp_path / 'cache'
    models = cache.CompiledModels(folder)
    models.load(xml)
    binary = next(folder.glob('*.mjb'))
    binary.write_bytes(b'corrupted')
    models.load(xml)
    receipt = next(folder.glob('*.json'))
    receipt.write_text('null')
    models.load(xml)
    sentinel = tmp_path / 'unrelated.json'
    sentinel.write_text('unrelated')
    receipt.unlink()
    receipt.symlink_to(sentinel)
    models.load(xml)
    assert len(calls) == 4 and sentinel.read_text() == 'unrelated'
    assert not receipt.is_symlink()


def test_changing_source_during_compile_fails_without_publishing(tmp_path, native_surface, monkeypatch):
    mesh, xml, calls = native_surface
    original = cache._compile
    def changed(xml):
        model = original(xml)
        mesh.write_bytes(trimesh.creation.box([.04, .02, .02]).export(file_type='stl'))
        return model
    monkeypatch.setattr(cache, '_compile', changed)
    with pytest.raises(RuntimeError, match='changed during compilation'):
        cache.CompiledModels(tmp_path / 'cache').load(xml)
    assert not list((tmp_path / 'cache').glob('*.mjb'))


def test_bounds_bypass_and_unrelated_directory_are_safe(tmp_path, native_surface, monkeypatch):
    _, xml, calls = native_surface
    folder = tmp_path / 'cache'
    models = cache.CompiledModels(folder, max_entries=1)
    models.load(xml)
    models.load(xml.replace('mass="1"', 'mass="2"'))
    models.load(xml)
    assert len(calls) == 3 and len(list(folder.glob('*.mjb'))) == 1
    tiny = tmp_path / 'tiny'
    cache.CompiledModels(tiny, max_bytes=1).load(xml)
    assert not list(tiny.glob('*.mjb'))
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    sentinel = unrelated / 'preserve'
    sentinel.write_text('unchanged')
    cache.CompiledModels(unrelated).load(xml)
    assert list(unrelated.iterdir()) == [sentinel]
    monkeypatch.setenv('R3_MUJOCO_MODEL_CACHE', '0')
    models.load(xml)
    assert len(calls) == 6


def test_relative_assets_use_the_official_compiler_without_cache(tmp_path, native_surface):
    mesh, xml, calls = native_surface
    relative = xml.replace('<mujoco>', f'<mujoco><compiler meshdir="{tmp_path}"/>').replace(str(mesh), mesh.name)
    models = cache.CompiledModels(tmp_path / 'cache')
    models.load(relative)
    models.load(relative)
    assert len(calls) == 2 and not (tmp_path / 'cache').exists()
    stripped = xml.replace('<mujoco>', f'<mujoco><compiler meshdir="{tmp_path}" strippath="true"/>')
    models.load(stripped)
    models.load(stripped)
    assert len(calls) == 4 and not (tmp_path / 'cache').exists()


def test_native_compiler_error_is_not_retried_as_a_cache_failure(tmp_path, native_surface, monkeypatch):
    _, xml, _ = native_surface
    calls = []
    def broken(xml):
        calls.append(xml)
        raise OSError('native input failure')
    monkeypatch.setattr(cache, '_compile', broken)
    with pytest.raises(OSError, match='native input failure'):
        cache.CompiledModels(tmp_path / 'cache').load(xml)
    assert len(calls) == 1


def test_interrupted_publication_is_cleaned_without_deleting_foreign_files(tmp_path, native_surface):
    _, xml, _ = native_surface
    folder = tmp_path / 'cache'
    models = cache.CompiledModels(folder)
    models.load(xml)
    orphan = folder / ('a' * 64 + '.mjb')
    orphan.write_bytes(b'orphan')
    pending = folder / '.native-model-interrupted'
    pending.write_bytes(b'incomplete')
    foreign = folder / 'keep-me.txt'
    foreign.write_text('unrelated')
    models.load(xml)
    assert not orphan.exists() and not pending.exists()
    assert foreign.read_text() == 'unrelated'


def test_production_runtime_reuses_compilation_but_repeats_real_floor_physics(project, native_snapshot, monkeypatch):
    from r3_sim.assembly import build_assembly_model
    from r3_sim.dynamics import RobotDynamics
    from test_native_collision_floor import install_open_display_mesh

    monkeypatch.setenv('R3_SIM_OVERLAP', '0')
    monkeypatch.setenv('R3_MUJOCO_THREADS', '0')
    current = install_open_display_mesh(project, native_snapshot)
    current.spec.robot.mobility = 'mobile'
    assembly = build_assembly_model(current)
    calls, original = [], cache._compile
    def compile_source(xml):
        calls.append(xml)
        return original(xml)
    monkeypatch.setattr(cache, '_compile', compile_source)
    states = []
    for _ in range(2):
        dynamics = RobotDynamics(current, assembly)
        touched = False
        for _ in range(500):
            dynamics.step()
            touched |= dynamics.data.ncon > 0
        assert touched and .014 <= dynamics.data.xpos[dynamics.root_body, 2] <= .016
        assert not dynamics.joints.detached
        states.append(dynamics.data.qpos.copy())
    assert len(calls) == 1
    np.testing.assert_array_equal(states[0], states[1])
