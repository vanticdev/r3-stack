"""A synthetic, unrelated native mesh can render and run real MuJoCo without STEP."""
from copy import deepcopy
import hashlib
import json

import numpy as np
import pytest
import trimesh

from r3_model.project import Project
from r3_model.pulled import PullError, load_pulled
from r3_model.pulled_view import cad_view
from r3_onshape.pull import pull
from r3_model.mesh_io import load_viewer_obj
from test_cad_pulled_consumers import project
from test_cad_pull import native_snapshot
from test_native_viewer_capture import FakeViewer, URL


class NativeViewer(FakeViewer):
    def __init__(self, snapshot):
        super().__init__()
        self.part.update(name='coupon', body_id='body', full_element_id='e' * 24 + '-source')
        self.pose = np.eye(4)
        self.raw = trimesh.creation.box([.01, .02, .03]).export(file_type='obj').encode()
        self.handle = hashlib.sha256(self.raw).hexdigest() + '.obj'
        self.provenance.update(mesh_artifact=self.handle, occurrence_transform=self.pose.ravel(order='F').tolist())
        self.definition = deepcopy(snapshot)
        row = self.definition['rows'][0]
        row['name'] = 'coupon'
        row['reference'].update(documentId='a' * 24, elementId='e' * 24)
        self.definition['definition']['parts'][0].update(documentId='a' * 24, elementId='e' * 24)
        self.exports = 0

    def assembly(self, url):
        return deepcopy(self.definition)

    def capture(self, url):
        return b'\x89PNG\r\n\x1a\nfixture', {'microversion': 'revision'}

    def step(self, *args):
        self.exports += 1
        raise AssertionError('STEP must not be requested')

    stl = gltf = step


def install(project, native_snapshot):
    native = NativeViewer(native_snapshot)
    result = pull(project, url=URL, version_id='version', transport=native, mesh_source='viewer', tab_id=12)
    assert result['ok'], result
    assert native.exports == 0
    return native


def test_mesh_pull_has_native_mass_immutable_provenance_and_no_step(project, native_snapshot):
    native = install(project, native_snapshot)
    model = load_pulled(project.root)
    part = model.parts['coupon']
    assert part.step is None and part.stl is None and part.mesh.suffix == '.obj'
    assert part.geometry_qualification == 'display_mesh'
    assert part.mesh.read_bytes() == native.raw
    assert part.mass == pytest.approx(.006)
    assert all(not p.stat().st_mode & 0o222 for p in (project.root / 'cad/pulled').rglob('*') if p.is_file())
    view = cad_view(Project.load(project.root), mass_required=True)
    assert view['parts']['coupon']['geometry_qualification'] == 'display_mesh'
    assert view['parts']['coupon']['volume'] == pytest.approx(6e-6)
    assert view['parts']['coupon']['mass'] == pytest.approx(.006)


def test_real_mujoco_uses_native_mesh_and_mass_without_step(project, native_snapshot):
    install(project, native_snapshot)
    from r3_sim.assembly import build_assembly_model
    import mujoco
    assembly = build_assembly_model(Project.load(project.root))
    model = mujoco.MjModel.from_xml_string(assembly.xml)
    state = mujoco.MjData(model)
    for _ in range(10):
        mujoco.mj_step(model, state)
    assert model.nmesh > 0
    assert assembly.total_mass == pytest.approx(.006)
    assert any(d.code == 'cad_geometry_estimated' for d in assembly.diagnostics)
    assert np.isfinite(state.qpos).all() and state.time > 0


def test_mesh_printability_geometry_and_model_stage_do_not_require_step(project, native_snapshot):
    install(project, native_snapshot)
    from r3_fab.geometry import build_geometry
    printed = build_geometry(Project.load(project.root), 'coupon')
    assert printed.source == 'pulled_obj' and printed.mesh.is_volume
    np.testing.assert_allclose(printed.mesh.extents, [10, 20, 30])
    assert printed.holes == [] and any('pending' in note for note in printed.notes)
    from r3_orchestrator import assembly_stage, model_stage
    assembly = assembly_stage.run(Project.load(project.root))
    assert any(d.code == 'cad_geometry_estimated' for d in assembly.diagnostics)
    result = model_stage.run(Project.load(project.root))
    assert any(d.code == 'cad_geometry_estimated' for d in result.diagnostics)
    assert (project.root / 'build/model/graph.json').is_file()


@pytest.mark.parametrize('change', ['revision', 'pose', 'identity', 'mass'])
def test_inconsistent_native_evidence_never_installs(project, native_snapshot, change):
    native = NativeViewer(native_snapshot)
    if change == 'revision': native.definition['microversion'] = 'other'
    if change == 'pose': native.definition['rows'][0]['transform'][3] = .2
    if change == 'identity': native.part['body_id'] = 'other'
    if change == 'mass': native.definition['definition']['parts'][0]['native_mass_properties'] = None
    result = pull(project, url=URL, version_id='version', transport=native, mesh_source='viewer')
    assert not result['ok']
    assert not (project.root / 'cad/pulled/pull.json').exists()
    assert native.exports == 0


def test_analytic_checks_remain_pending_and_release_is_not_fabrication_ready(project, native_snapshot):
    install(project, native_snapshot)
    from r3_onshape.step_geometry import geometry_from_pulled
    with pytest.raises(PullError) as error:
        geometry_from_pulled(load_pulled(project.root))
    assert error.value.code == 'cad_analytic_geometry_pending'
    from r3_onshape.stage import run
    result = run(Project.load(project.root))
    assert any(d.code == 'cad_geometry_estimated' for d in result.diagnostics)
    payload = json.loads((project.root / 'build/cad/cad_model.json').read_text())
    assert payload['fit']['status'] == 'not_run' and payload['fit']['verified'] is False
    from r3_orchestrator.release import evaluate
    release = evaluate(Project.load(project.root))
    assert not release['fabrication_ready']
    item = next(item for item in release['items'] if item['id'] == 'stage.cad.geometry_qualification')
    assert item['status'] == 'not_run'
    assert item['evidence'][0]['estimated']


@pytest.mark.parametrize('kind', ['obj', 'provenance'])
def test_viewer_pull_integrity_does_not_accept_modified_or_writable_bytes(project, native_snapshot, kind):
    install(project, native_snapshot)
    model = load_pulled(project.root)
    if kind == 'obj':
        path = model.parts['coupon'].obj
    else:
        manifest = json.loads((project.root / 'cad/pulled/pull.json').read_text())
        path = project.root / 'cad/pulled' / manifest['parts']['coupon']['provenance']
    path.chmod(0o644)
    with pytest.raises(PullError) as error:
        load_pulled(project.root)
    assert error.value.code == 'cad_pull_modified'


def test_obj_bodies_are_separate_and_external_resources_are_rejected(tmp_path):
    mesh = trimesh.creation.box([.01, .02, .03])
    # Separate indexed native bodies can touch without merging their ownership.
    obj = mesh.export(file_type='obj')
    lines = obj.splitlines()
    first = 'o body_0\n' + obj
    offset = len(mesh.vertices)
    second = 'o body_1\n' + '\n'.join('f ' + ' '.join(str(int(i) + offset) for i in line.split()[1:]) if line.startswith('f ') else line for line in lines)
    path = tmp_path / 'native.obj'; path.write_text(first + '\n' + second)
    loaded = load_viewer_obj(path)
    assert len(loaded.metadata['bodies']) == 2
    assert len(loaded.vertices) == len(mesh.vertices) * 2
    assert loaded.is_watertight and loaded.volume == pytest.approx(mesh.volume * 2)
    path.write_text('mtllib ../secret.mtl\n' + first)
    with pytest.raises(ValueError, match='external'):
        load_viewer_obj(path)
