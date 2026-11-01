"""Native exports survive long jobs, interrupted clients and provider loss."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from r3_model.pulled import PullError
from r3_onshape.native import NativeCadTransport, NativeRateLimitError


def client(folder):
    runtime = folder / 'runtime'
    runtime.mkdir(exist_ok=True)
    (runtime / 'bridge.json').write_text(json.dumps({'port': 8766, 'token': 'synthetic-only'}))
    return NativeCadTransport(runtime, export_jobs=folder / 'jobs')


def payload(state='DONE', **kwargs):
    return json.dumps({'id': 'job', 'requestState': state, 'resultDocumentId': 'result-doc',
                       'resultExternalDataIds': ['file'], **kwargs}).encode()


@pytest.fixture(autouse=True)
def no_real_wait(monkeypatch):
    monkeypatch.setattr('r3_onshape.translation.time.sleep', lambda _: None)


def test_gltf_waits_past_the_direct_download_deadline(tmp_path, monkeypatch):
    moments = iter([0, 100, 200])
    monkeypatch.setattr('r3_onshape.translation.time.monotonic', lambda: next(moments))
    native = client(tmp_path)
    native._request = Mock(side_effect=[payload('ACTIVE'), payload('ACTIVE'), payload(), b'original-file'])
    assert native.gltf('doc', 'version', 'studio', 'part') == b'original-file'
    calls = native._request.call_args_list
    assert [c.args[0] for c in calls] == ['POST', 'GET', 'GET', 'GET']
    assert all('/native/api/v17/' in c.args[1] and '/features' not in c.args[1] for c in calls)
    assert calls[1].args[1] == calls[2].args[1] == '/native/api/v17/translations/job'


@pytest.mark.parametrize('format', ['step', 'gltf'])
def test_pending_job_resumes_after_client_restart(tmp_path, monkeypatch, format):
    moments = iter([0, 1900])
    monkeypatch.setattr('r3_onshape.translation.time.monotonic', lambda: next(moments))
    native = client(tmp_path)
    native._request = Mock(return_value=payload('ACTIVE'))
    with pytest.raises(PullError) as pending:
        getattr(native, format)('doc', 'version', 'studio', 'part')
    assert pending.value.code == 'cad_pull_translation_pending'
    assert pending.value.details['translation_id'] == 'job'
    assert Path(pending.value.details['export_state']).is_file()
    native._request.assert_called_once()
    monkeypatch.setattr('r3_onshape.translation.time.monotonic', lambda: 0)
    resumed = client(tmp_path)
    resumed._request = Mock(side_effect=[payload(), b'unchanged-file'])
    assert getattr(resumed, format)('doc', 'version', 'studio', 'part') == b'unchanged-file'
    assert [c.args[0] for c in resumed._request.call_args_list] == ['GET', 'GET']


@pytest.mark.parametrize('failure', ['connection', 'invalid_json'])
def test_uncertain_submission_is_never_replayed(tmp_path, failure):
    native = client(tmp_path)
    native._request = Mock(side_effect=PullError('cad_native_unavailable', 'unknown outcome')) if failure == 'connection' else Mock(return_value=b'not JSON')
    with pytest.raises(PullError): native.step('doc', 'version', 'studio', 'part')
    native._request.assert_called_once()
    restarted = client(tmp_path)
    restarted._request = Mock()
    with pytest.raises(PullError) as error: restarted.step('doc', 'version', 'studio', 'part')
    assert error.value.code == 'cad_native_export_unknown'
    restarted._request.assert_not_called()


def test_known_job_in_error_response_resumes_without_another_post(tmp_path):
    native = client(tmp_path)
    native._request = Mock(side_effect=PullError('cad_native_request_failed', 'still active',
        {'http_status': 504, 'translation_id': 'job'}))
    with pytest.raises(PullError): native.gltf('doc', 'version', 'studio', 'part')
    restarted = client(tmp_path)
    restarted._request = Mock(side_effect=[payload(), b'original'])
    assert restarted.gltf('doc', 'version', 'studio', 'part') == b'original'
    assert all(c.args[0] == 'GET' for c in restarted._request.call_args_list)


def test_poll_rate_limit_retains_job_and_original_resume_time(tmp_path, monkeypatch):
    native = client(tmp_path)
    limited = NativeRateLimitError('300', 'GET', '/native/api/v17/translations/job')
    native._request = Mock(side_effect=[payload('ACTIVE'), limited])
    with pytest.raises(PullError): native.step('doc', 'version', 'studio', 'part')
    restarted = client(tmp_path)
    restarted._request = Mock()
    with pytest.raises(PullError) as error: restarted.step('doc', 'version', 'studio', 'part')
    assert error.value.details['resume_at'] == limited.details['resume_at']
    restarted._request.assert_not_called()

    after = datetime.fromisoformat(limited.details['resume_at']) + timedelta(seconds=1)
    class AfterReset(datetime):
        @classmethod
        def now(cls, tz=None): return after
    monkeypatch.setattr('r3_onshape.translation.datetime', AfterReset)
    restarted._request = Mock(side_effect=[payload(), b'original'])
    assert restarted.step('doc', 'version', 'studio', 'part') == b'original'
    assert all(c.args[0] == 'GET' for c in restarted._request.call_args_list)


def test_terminal_export_failure_is_not_resubmitted(tmp_path):
    native = client(tmp_path)
    native._request = Mock(return_value=payload('FAILED', failureReason='synthetic invalid body'))
    with pytest.raises(PullError) as error: native.gltf('doc', 'version', 'studio', 'part')
    assert error.value.code == 'cad_pull_translation_failed'
    assert error.value.details['failure_reason'] == 'synthetic invalid body'
    restarted = client(tmp_path)
    restarted._request = Mock()
    with pytest.raises(PullError): restarted.gltf('doc', 'version', 'studio', 'part')
    restarted._request.assert_not_called()
    restarted._request = Mock(side_effect=[payload(), b'new-version-file'])
    assert restarted.gltf('doc', 'new-version', 'studio', 'part') == b'new-version-file'
    assert restarted._request.call_args_list[0].args[0] == 'POST'


def test_completed_immutable_file_is_reused_and_modification_rejected(tmp_path):
    native = client(tmp_path)
    native._request = Mock(side_effect=[payload(), b'original'])
    assert native.step('doc', 'version', 'studio', 'part') == b'original'
    restarted = client(tmp_path)
    restarted._request = Mock()
    assert restarted.step('doc', 'version', 'studio', 'part') == b'original'
    restarted._request.assert_not_called()
    file, = (tmp_path / 'jobs').glob('*/*.step')
    file.chmod(0o644); file.write_bytes(b'changed'); file.chmod(0o444)
    with pytest.raises(PullError) as error: restarted.step('doc', 'version', 'studio', 'part')
    assert error.value.code == 'cad_native_export_cache_modified'
    assert file.read_bytes() == b'changed'
    restarted._request.assert_not_called()


def test_cached_job_cannot_change_source_selection(tmp_path):
    native = client(tmp_path)
    native._request = Mock(return_value=payload('FAILED'))
    with pytest.raises(PullError): native.step('doc', 'version', 'studio', 'part')
    state, = (tmp_path / 'jobs').glob('*/job.json')
    data = json.loads(state.read_text()); data['request']['body']['partIds'] = 'other'
    state.chmod(0o644); state.write_text(json.dumps(data)); state.chmod(0o444)
    restarted = client(tmp_path); restarted._request = Mock()
    with pytest.raises(PullError) as error: restarted.step('doc', 'version', 'studio', 'part')
    assert error.value.code == 'cad_native_export_cache_invalid'
    restarted._request.assert_not_called()
