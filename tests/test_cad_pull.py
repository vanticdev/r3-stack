"""Robot-independent native pull fixtures with real synthetic STEP/STL geometry."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from r3_model.pulled import PullError, load_pulled
from r3_onshape.pull import describe, download, mass_properties, target


@pytest.fixture
def native_snapshot():
    cs = {'origin': [0, 0, 0], 'xAxis': [1, 0, 0], 'zAxis': [0, 0, 1]}
    part = {'documentId': 'doc', 'elementId': 'studio', 'partId': 'body', 'material': {'name': 'Synthetic material', 'density': 1000},
            'mateConnectors': [{'name': 'origin', 'mateConnectorCS': cs}]}
    return {'microversion': 'revision', 'definition': {'parts': [part], 'rootAssembly': {'instances': [], 'features': []}},
            'rows': [{'kind': 'occurrence', 'instance_type': 'Part', 'occurrence_path': ['instance'], 'name': 'body',
                      'reference': {**part, 'documentVersion': 'version'}, 'transform': np.eye(4).ravel().tolist()}]}


@pytest.fixture
def geometry(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    import trimesh

    path = tmp_path / 'synthetic.step'
    writer = STEPControl_Writer()
    writer.Transfer(BRepPrimAPI_MakeBox(10, 20, 30).Shape(), STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_RetDone
    mesh = trimesh.creation.box([.01, .02, .03])
    mesh.apply_translation([.005, .01, .015])
    return path.read_bytes(), mesh.export(file_type='stl')


def test_step_volume_integrals_in_si(geometry, tmp_path):
    step, _ = geometry
    path = tmp_path / 'box.step'
    path.write_bytes(step)
    props = mass_properties(path, 1000)
    assert props['volume'] == pytest.approx(6e-6)
    assert props['mass'] == pytest.approx(.006)
    assert props['com'] == pytest.approx([.005, .01, .015])
    assert props['inertia'][0] == pytest.approx(.006 * (.02**2 + .03**2) / 12)


def test_no_microversion_or_workspace_step_fallback():
    with pytest.raises(PullError) as error:
        target('https://cad.onshape.com/documents/doc/m/micro/e/assembly')
    assert error.value.code == 'cad_pull_version_required'
    with pytest.raises(PullError) as error:
        target('https://cad.onshape.com/documents/doc/w/workspace/e/assembly')
    assert error.value.code == 'cad_pull_version_required'
    assert '/v/version/' in target('https://cad.onshape.com/documents/doc/w/workspace/e/assembly', 'version')['url']


def install(tmp_path, geometry, native_snapshot):
    pin = target('https://cad.onshape.com/documents/doc/v/version/e/assembly')
    manifest = describe(native_snapshot, pin, {'body': ['instance']})
    step, stl = geometry

    class FakeTransport:
        def stl(self, *args):
            assert args[:4] == ('doc', 'version', 'studio', 'body')
            return stl

        def step(self, *args):
            assert args[:4] == ('doc', 'version', 'studio', 'body')
            return step

        def capture(self, url):
            return b'\x89PNG\r\n\x1a\nfixture', {'microversion': 'revision'}

    folder = tmp_path / 'cad' / 'pulled'
    folder.mkdir(parents=True)
    download(FakeTransport(), manifest, folder, pin['url'])
    checksum = hashlib.sha256((folder / 'pull.json').read_bytes()).hexdigest()
    (tmp_path / 'r3.lock.yaml').write_text(yaml.safe_dump({'cad_pull': {'manifest_sha256': checksum}}))
    return folder, manifest


def test_fake_native_pull_is_complete_immutable_and_loadable(tmp_path, geometry, native_snapshot):
    folder, manifest = install(tmp_path, geometry, native_snapshot)
    model = load_pulled(tmp_path)
    assert model.version_id == 'version'
    assert set(model.parts) == set(model.poses) == {'body'}
    assert model.parts['body'].mass == pytest.approx(.006)
    assert np.array_equal(model.parts['body'].mate_connectors['origin'], np.eye(4))
    assert all(not p.stat().st_mode & 0o222 for p in folder.rglob('*') if p.is_file())


@pytest.mark.parametrize('mutation', ['geometry', 'hash', 'frame', 'writable', 'missing', 'symlink'])
def test_integrity_errors(tmp_path, geometry, native_snapshot, mutation):
    folder, manifest = install(tmp_path, geometry, native_snapshot)
    path = folder / 'parts/body.stl'
    if mutation in ('geometry', 'writable'):
        path.chmod(0o644)
        if mutation == 'geometry':
            path.write_bytes(b'changed')
            path.chmod(0o444)
    elif mutation == 'missing':
        path.unlink()
    elif mutation == 'symlink':
        path.unlink()
        other = tmp_path / 'external.stl'
        other.write_bytes(geometry[1])
        other.chmod(0o444)
        path.symlink_to(other)
    else:
        path = folder / 'pull.json'
        path.chmod(0o644)
        if mutation == 'hash':
            manifest['parts']['body']['sha256']['stl'] = 'changed'
        else:
            manifest['parts']['body']['mate_connectors']['origin'][0][0] = 2
        path.write_text(json.dumps(manifest))
        path.chmod(0o444)
    with pytest.raises(PullError) as error:
        load_pulled(tmp_path)
    assert error.value.code in ('cad_pull_modified', 'cad_pull_missing')


def test_missing_native_occurrence_cannot_be_omitted(native_snapshot):
    second = copy.deepcopy(native_snapshot['rows'][0])
    second['occurrence_path'] = ['other']
    native_snapshot['rows'].append(second)
    with pytest.raises(PullError, match='exactly the same'):
        describe(native_snapshot, target('https://cad.onshape.com/documents/doc/v/version/e/assembly'), {'body': ['instance']})


def test_native_mates_are_pulled_not_recomputed(native_snapshot):
    other = copy.deepcopy(native_snapshot['rows'][0])
    other.update(occurrence_path=['other'], name='other')
    other['transform'][3] = .1
    native_snapshot['rows'].append(other)
    cs = {'origin': [.1, 0, 0], 'xAxis': [1, 0, 0], 'zAxis': [0, 0, 1]}
    native_snapshot['definition']['rootAssembly']['features'] = [{'id': 'pivot', 'featureData': {'mateType': 'REVOLUTE',
        'mateLimits': {'limitAxialZMin': -.5, 'limitAxialZMax': .5},
        'matedEntities': [{'matedOccurrence': ['instance'], 'matedCS': cs}, {'matedOccurrence': ['other'], 'matedCS': cs}]}}]
    manifest = describe(native_snapshot, target('https://cad.onshape.com/documents/doc/v/version/e/assembly'), {})
    assert manifest['mates'][0]['limits'] == [-.5, .5]
    assert manifest['parts']['body']['mate_connectors']['pivot:a'][0][3] == .1
    assert manifest['parts']['other']['mate_connectors']['pivot:b'][0][3] == 0
    assert manifest['poses']['other'][0][3] == .1


def test_missing_material_does_not_use_catalog_estimate(native_snapshot):
    native_snapshot['definition']['parts'][0].pop('material')
    with pytest.raises(PullError) as error:
        describe(native_snapshot, target('https://cad.onshape.com/documents/doc/v/version/e/assembly'), {})
    assert error.value.code == 'cad_pull_material_unavailable'


@pytest.fixture
def project(tmp_path):
    import shutil
    from r3_model.project import Project

    root = tmp_path / 'project'
    shutil.copytree(Path(__file__).parent / 'fixtures/native-pull', root)
    return Project.load(root)


def fake_transport(geometry, snapshot):
    step, stl = geometry

    class FakeTransport:
        def assembly(self, url):
            assert '/v/version/' in url
            return copy.deepcopy(snapshot)

        def stl(self, *args):
            return stl

        def step(self, *args):
            return step

        def capture(self, url):
            return b'\x89PNG\r\n\x1a\nfixture', {'microversion': snapshot['microversion']}

    return FakeTransport()


def test_complete_pull_refreshes_split_description_and_integrity(project, geometry, native_snapshot):
    from r3_model.description import check_pulled
    from r3_model.project import Project
    from r3_onshape.pull import pull
    from r3_orchestrator.spec_ops import get_spec, patch_spec

    result = pull(project, transport=fake_transport(geometry, native_snapshot))
    assert result['ok'], result
    assert result['version_id'] == 'version' and result['parts'] == 1
    model = load_pulled(project.root)
    check_pulled(project.root, model)
    reloaded = Project.load(project.root)
    assert reloaded.spec.components['body'].pulled.mass == pytest.approx(.006)
    assert reloaded.spec.components['body'].pulled.material == 'Synthetic material'
    assert (project.root / 'parts/body.yaml').read_text().count('pulled:') == 1
    assert '$include' in (project.root / 'robot.yaml').read_text()
    document = get_spec(project.root)
    changed = patch_spec(project.root, {'components': {'body': {'pulled': {'mass': 100}}}}, document['sha256'])
    assert not changed['ok'] and changed['code'] == 'spec_read_only'


def test_failed_description_refresh_restores_complete_previous_pull(project, geometry, native_snapshot, monkeypatch):
    from r3_onshape.pull import pull
    from r3_model.description import check_pulled

    transport = fake_transport(geometry, native_snapshot)
    assert pull(project, transport=transport)['ok']
    manifest_path = project.root / 'cad/pulled/pull.json'
    before_manifest = manifest_path.read_bytes()
    before_lock = (project.root / 'r3.lock.yaml').read_bytes()
    before_part = (project.root / 'parts/body.yaml').read_bytes()

    def reject(*args):
        raise PullError('cad_pull_invalid', 'synthetic description validation failure')
    monkeypatch.setattr('r3_model.description.refresh_pulled', reject)
    result = pull(project, transport=transport)
    assert result['ok'] is False and result['code'] == 'cad_pull_invalid'
    assert manifest_path.read_bytes() == before_manifest
    assert (project.root / 'r3.lock.yaml').read_bytes() == before_lock
    assert (project.root / 'parts/body.yaml').read_bytes() == before_part
    assert not (project.root / 'cad/.pulled-previous').exists()
    assert not list((project.root / 'cad').glob('.pull-*'))
    check_pulled(project.root, load_pulled(project.root))
