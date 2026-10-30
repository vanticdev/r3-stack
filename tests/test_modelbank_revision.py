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
