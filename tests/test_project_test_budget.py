"""Project execution resources do not replace test outcomes or physical checks."""
import logging

import pytest
from pydantic import ValidationError

from r3_github.scaffold import create_project
from r3_model.project import Project
from r3_model.spec import ProjectTesting
from r3_orchestrator import project_tests, spec_ops


@pytest.mark.parametrize('value', [0, -1, float('inf'), float('nan'), True])
def test_test_budget_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        ProjectTesting(timeout=value)
    assert ProjectTesting().timeout == 300


def test_budget_is_authorable_and_the_real_runner_preserves_failures(tmp_path, monkeypatch, caplog):
    root = tmp_path / 'synthetic-instrument'
    create_project(root, template='blank', platform_ref='test')
    current = spec_ops.get_spec(root)
    patched = spec_ops.patch_spec(root, {'testing': {'timeout': '30 s'}}, current['sha256'])
    assert patched['ok'], patched
    project = Project.load(root)
    assert project.spec.testing.timeout == 30
    directory = root / 'tests'
    directory.mkdir(exist_ok=True)
    source = directory / 'test_instrument.py'
    source.write_text('import time\ndef test_instrument():\n    time.sleep(.03)\n    assert 2 + 2 == 4\n')
    monkeypatch.setattr(project_tests, '_PROGRESS_INTERVAL', .02)
    with caplog.at_level(logging.INFO, logger=project_tests.__name__):
        passed = project_tests.run(project)
    assert passed.ok
    assert passed.metrics['timeout_s'] == 30
    assert passed.metrics['executed'] == 1
    assert passed.metrics['cases']['tests.test_instrument::test_instrument']['status'] == 'passed'
    assert any('Project tests still running' in r.message for r in caplog.records)
    source.write_text('def test_physical_limit():\n    assert 2 < 1\n')
    failed = project_tests.run(Project.load(root))
    assert not failed.ok and failed.metrics['failed'] == 1
    assert failed.metrics['cases']['tests.test_instrument::test_physical_limit']['status'] == 'failed'
    assert any(d.code == 'project_tests_failed' for d in failed.diagnostics)


def test_expired_budget_is_incomplete_and_kills_the_test_process(tmp_path, monkeypatch):
    root = tmp_path / 'synthetic-instrument'
    create_project(root, template='blank', platform_ref='test')
    directory = root / 'tests'
    directory.mkdir(exist_ok=True)
    (directory / 'test_wait.py').write_text('import time\ndef test_wait():\n    time.sleep(30)\n')
    original, processes = project_tests.subprocess.Popen, []
    def capture(command, *args, **kwargs):
        process = original(command, *args, **kwargs)
        if command[1:3] == ['-m', 'pytest']:
            processes.append(process)
        return process
    monkeypatch.setattr(project_tests.subprocess, 'Popen', capture)
    result = project_tests.run(Project.load(root), timeout=.01)
    assert not result.ok
    assert result.metrics == {'timeout_s': .01}
    assert not (root / 'build/tests/junit.xml').exists()
    error = next(d for d in result.diagnostics if d.code == 'project_tests_timeout')
    assert error.data['timeout_s'] == .01
    assert 'testing.timeout' in error.hint
    assert len(processes) == 1 and processes[0].poll() is not None


def test_invalid_direct_override_never_starts_a_test_process(tmp_path, monkeypatch):
    root = tmp_path / 'synthetic-instrument'
    create_project(root, template='blank', platform_ref='test')
    original = project_tests.subprocess.Popen
    def unexpected(command, *args, **kwargs):
        if command[1:3] == ['-m', 'pytest']:
            pytest.fail('invalid budget must not spawn a test process')
        return original(command, *args, **kwargs)
    monkeypatch.setattr(project_tests.subprocess, 'Popen', unexpected)
    result = project_tests.run(Project.load(root), timeout=float('inf'))
    assert not result.ok
    assert result.diagnostics[0].code == 'project_tests_invalid'
