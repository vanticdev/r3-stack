"""Dataset-only edits invalidate persisted evidence without reloading a Project."""
import os

import pytest

from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_orchestrator.api import _stage_summary
from r3_orchestrator.release import is_stale


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    catalog = tmp_path / 'bundled'
    bank = tmp_path / 'bank'
    local = tmp_path / 'project' / 'catalog'
    for folder in (catalog, bank, local):
        folder.mkdir(parents=True)
    monkeypatch.setattr('r3_catalog.DATA_DIR', catalog)
    monkeypatch.setenv('R3_MODEL_BANK', str(bank))
    root = local.parent
    (root / 'robot.yaml').write_text('robot: {name: revision-test}\ncomponents: {}\n')
    # No assembly/catalog needed to test file revisions.
    project = Project.load(root)
    return project, (catalog, bank, local)


@pytest.mark.parametrize('which', [0, 1, 2])
def test_content_changes_mark_evidence_stale(inputs, which):
    project, roots = inputs
    path = roots[which] / 'entry.yaml'
    path.write_text('revision: a\n')
    before = project.fingerprint()
    result = StageResult(stage='model', inputs=before)
    assert not _stage_summary('model', result, project.fingerprint())['stale']
    stat = path.stat()
    path.write_text('revision: b\n')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    after = project.fingerprint()
    assert before['robot_yaml_sha256'] == after['robot_yaml_sha256']
    assert before['catalog_modelbank_revision'] != after['catalog_modelbank_revision']
    assert is_stale(before, after)
    assert _stage_summary('model', result, after)['stale']
    path.unlink()
    assert project.fingerprint()['catalog_modelbank_revision'] != after['catalog_modelbank_revision']


def test_revision_ignores_cache_and_mtime(inputs):
    project, roots = inputs
    path = roots[1] / 'entry.yaml'
    path.write_text('name: model\n')
    before = project.fingerprint()
    path.touch()
    (roots[1] / 'index.sqlite').write_bytes(b'disposable')
    assert before == project.fingerprint()
    legacy = {k: v for k, v in before.items() if k != 'catalog_modelbank_revision'}
    assert is_stale(legacy, before)


@pytest.mark.parametrize('artifact', ['cad', 'assembly'])
def test_stale_fit_proof_cannot_suppress_interference(inputs, artifact):
    from types import SimpleNamespace
    from r3_orchestrator.model_stage import ModelChecker
    from r3_orchestrator.geometry import make_solid

    project, roots = inputs
    project.spec.components = {
        'a': SimpleNamespace(mount='world'), 'b': SimpleNamespace(mount='world')}
    before = project.fingerprint()
    proof = {'inputs': before, 'parts': {'a': {}, 'b': {}}, 'fit': {},
             'poses': {'a': {}, 'b': {}}, 'interference_method': 'exact'}
    project.read_build_json = lambda path: proof if path.startswith(artifact if artifact == 'cad' else 'model/') else None
    solids = {name: make_solid(name, 'box', [.01, .01, .01], 'z', [0, 0, 0], [0, 0, 0]) for name in ('a', 'b')}
    validator = object.__new__(ModelChecker)
    validator.project, validator.spec = project, project.spec
    validator.result = StageResult(stage='model')
    assert validator.check_interference(solids) == []
    (roots[1] / 'review.yaml').write_text('validation: changed\n')
    assert validator.check_interference(solids)
    assert any(d.code == 'component_interference' for d in validator.result.diagnostics)


def test_new_cad_proof_uses_revision_that_will_be_pinned(inputs):
    import json
    from types import SimpleNamespace
    from r3_onshape.organized import TOP_LEVEL, write_model

    project, _ = inputs
    project.lock['cad'] = {'revision': 'old'}
    document = SimpleNamespace(did='document', wid='workspace', vid='version', digest='library',
                               assemblies={TOP_LEVEL: 'assembly'}, studios={}, variable_studio='',
                               url=lambda *args: 'https://cad.onshape.com/documents/fixture')
    path = write_model(project, {'robot': document, 'library_doc': document, 'parts': {},
                                 'microversion': 'new'}, 'fixture', 'digest')
    project.lock['cad'] = {'revision': 'new'}
    assert not is_stale(json.loads(path.read_text())['inputs'], project.fingerprint())


def test_matching_cad_geometry_can_be_revalidated_after_dataset_edit(inputs, monkeypatch):
    import json
    from types import SimpleNamespace
    from r3_onshape import organized, stage

    project, roots = inputs
    project.spec.cad.provider = 'onshape'
    project.spec.assembly = SimpleNamespace()
    path = project.build_dir('cad') / 'cad_model.json'
    before = project.fingerprint()
    path.write_text(json.dumps({'inputs': before, 'source': {'generator_digest': 'matching'}, 'parts': {}}))
    (roots[1] / 'review.yaml').write_text('id: review\nname: changed\ncategory: other\n')
    assert is_stale(before, project.fingerprint())
    monkeypatch.setattr(organized, 'input_digest', lambda p: 'matching')
    result = stage.run(project)
    assert result.ok and any(d.code == 'cad_model_reused' for d in result.diagnostics)
    assert not is_stale(json.loads(path.read_text())['inputs'], project.fingerprint())
