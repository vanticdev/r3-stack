"""Known export pacing is shared across jobs on the same method/path only."""
from datetime import datetime, timedelta
from email.message import Message
import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from r3_model.pulled import PullError
from r3_onshape.native import NativeRateLimitError
from test_cad_translation_resume import client, payload

PATH = '/native/api/v17/partstudios/d/doc/v/version/e/studio/translations'


def rejection(retry='120'):
    headers = Message()
    if retry is not None:
        headers['Retry-After'] = retry
    return HTTPError('http://127.0.0.1:8766' + PATH, 429, 'limited', headers,
                     io.BytesIO(b'{"message":"Too many requests"}'))


def first_rejection(tmp_path, retry='120'):
    native = client(tmp_path)
    native._opener = Mock()
    native._opener.open.side_effect = rejection(retry)
    with pytest.raises(NativeRateLimitError) as caught:
        native.step('doc', 'version', 'studio', 'first')
    assert native._opener.open.call_count == 1
    return native, caught.value


def reply(data):
    return io.BytesIO(data)


def test_changing_format_or_part_does_not_repeat_a_held_endpoint(tmp_path):
    _, original = first_rejection(tmp_path)
    restarted = client(tmp_path)
    restarted._opener = Mock()
    restarted._opener.open.side_effect = AssertionError('Must not send a held request')
    with pytest.raises(NativeRateLimitError) as caught:
        restarted.gltf('doc', 'version', 'studio', 'different-part')
    assert caught.value.details['resume_at'] == original.details['resume_at']
    assert caught.value.details['request_sent'] is False
    restarted._opener.open.assert_not_called()


def test_known_hold_is_checked_before_persisting_submission_intent(tmp_path, monkeypatch):
    first_rejection(tmp_path)
    import r3_onshape.translation as translation
    save = translation._save
    def fail_on_submission(path, state):
        if state['phase'] == 'submitting':
            raise SystemExit('A worker loss here would poison an unsent job')
        save(path, state)
    monkeypatch.setattr(translation, '_save', fail_on_submission)
    restarted = client(tmp_path)
    restarted._opener = Mock()
    with pytest.raises(NativeRateLimitError):
        restarted.gltf('doc', 'version', 'studio', 'different-part')
    restarted._opener.open.assert_not_called()
    assert all(json.loads(p.read_text())['phase'] != 'submitting'
               for p in (tmp_path / 'jobs').glob('*/job.json'))


def test_unknown_reset_does_not_get_a_guessed_deadline_in_another_job(tmp_path):
    first_rejection(tmp_path, retry=None)
    restarted = client(tmp_path)
    restarted._opener = Mock()
    restarted._opener.open.side_effect = AssertionError('Unknown reset is held')
    with pytest.raises(NativeRateLimitError) as caught:
        restarted.gltf('doc', 'version', 'studio', 'different-part')
    assert caught.value.details.get('resume_at') is None
    restarted._opener.open.assert_not_called()


def test_shared_hold_expires_and_one_new_job_can_be_submitted(tmp_path, monkeypatch):
    _, original = first_rejection(tmp_path)
    from r3_onshape import backoff
    after = datetime.fromisoformat(original.details['resume_at']) + timedelta(seconds=1)
    class After(datetime):
        @classmethod
        def now(cls, tz=None): return after
    monkeypatch.setattr(backoff, 'datetime', After)
    monkeypatch.setattr('r3_onshape.translation.datetime', After)
    restarted = client(tmp_path)
    restarted._opener = Mock()
    restarted._opener.open.side_effect = [reply(payload()), reply(b'unchanged native file')]
    assert restarted.gltf('doc', 'version', 'studio', 'different-part') == b'unchanged native file'
    assert restarted.gltf('doc', 'version', 'studio', 'different-part') == b'unchanged native file'
    calls = restarted._opener.open.call_args_list
    assert len(calls) == 2
    assert [c.args[0].get_method() for c in calls] == ['POST', 'GET']
    assert all('/features' not in c.args[0].full_url for c in calls)


def test_an_observed_endpoint_hold_does_not_become_a_global_outage(tmp_path):
    first_rejection(tmp_path)
    restarted = client(tmp_path)
    restarted._opener = Mock()
    restarted._opener.open.side_effect = [reply(b'known queued status'), reply(b'other response')]
    assert restarted._request('GET', '/native/api/v17/translations/known-job') == b'known queued status'
    assert restarted._request('GET', PATH) == b'other response'
    assert restarted._opener.open.call_count == 2


def test_completed_pinned_file_stays_available_during_endpoint_backoff(tmp_path):
    native = client(tmp_path)
    native._opener = Mock()
    native._opener.open.side_effect = [reply(payload()), reply(b'unchanged native file'), rejection()]
    assert native.gltf('doc', 'version', 'studio', 'cached') == b'unchanged native file'
    with pytest.raises(NativeRateLimitError):
        native.step('doc', 'version', 'studio', 'another')
    restarted = client(tmp_path)
    restarted._opener = Mock()
    assert restarted.gltf('doc', 'version', 'studio', 'cached') == b'unchanged native file'
    restarted._opener.open.assert_not_called()


def test_shared_backoff_directory_cannot_escape_cache(tmp_path):
    native = client(tmp_path)
    native.export_jobs.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (native.export_jobs / 'resource-backoffs').symlink_to(outside, target_is_directory=True)
    native._opener = Mock()
    with pytest.raises(PullError) as caught:
        native.step('doc', 'version', 'studio', 'part')
    assert caught.value.code == 'cad_native_backoff_invalid'
    native._opener.open.assert_not_called()
    assert list(outside.iterdir()) == []


def test_inconsistent_backoff_evidence_refuses_network(tmp_path):
    first_rejection(tmp_path)
    path, = (tmp_path / 'jobs/resource-backoffs').glob('*.json')
    value = json.loads(path.read_text())
    value['method'] = 'GET'
    path.chmod(0o644)
    path.write_text(json.dumps(value))
    restarted = client(tmp_path)
    restarted._opener = Mock()
    with pytest.raises(PullError) as caught:
        restarted.gltf('doc', 'version', 'studio', 'part')
    assert caught.value.code == 'cad_native_backoff_invalid'
    restarted._opener.open.assert_not_called()


def test_backoff_storage_error_never_masks_the_actual_http_429(tmp_path, monkeypatch):
    from r3_onshape import backoff
    def fail(*args):
        raise OSError('synthetic unavailable storage')
    monkeypatch.setattr(backoff, 'remember', fail)
    _, error = first_rejection(tmp_path)
    assert error.details['http_status'] == 429 and error.details['retry_after'] == '120'
    assert error.details['resume_at']
    assert 'synthetic unavailable storage' in error.details['backoff_persistence_error']


@pytest.mark.parametrize('unknown', [False, True])
def test_explicit_operator_scope_is_enforced_across_part_versions(tmp_path, monkeypatch, unknown):
    from datetime import timezone
    until = None if unknown else (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    pattern = '/native/api/v17/partstudios/**/translations'
    monkeypatch.setenv('R3_ONSHAPE_BACKOFFS', json.dumps([{'method': 'POST', 'resource': pattern, 'until': until}]))
    native = client(tmp_path)
    native._opener = Mock()
    with pytest.raises(PullError) as caught:
        native.gltf('different-doc', 'different-version', 'different-studio', 'part')
    assert caught.value.code == 'cad_native_backoff_active'
    assert caught.value.details['hold_resource'] == pattern
    assert caught.value.details['request_sent'] is False
    assert 'http_status' not in caught.value.details  # no fabricated server response
    native._opener.open.assert_not_called()


def test_expired_operator_scope_does_not_block_current_native_reads(tmp_path, monkeypatch):
    from datetime import timezone
    monkeypatch.setenv('R3_ONSHAPE_BACKOFFS', json.dumps([{'method': 'GET', 'resource': '/native/api/**',
        'until': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}]))
    native = client(tmp_path)
    native._opener = Mock()
    native._opener.open.return_value = reply(b'known status')
    assert native._request('GET', '/native/api/v17/translations/known') == b'known status'
    assert native._opener.open.call_count == 1


def test_bad_operator_reset_never_causes_a_probe(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_ONSHAPE_BACKOFFS', json.dumps([{'method': 'POST', 'resource': PATH,
        'until': '2026-11-02T20:00:00'}]))
    native = client(tmp_path)
    native._opener = Mock()
    with pytest.raises(PullError) as caught:
        native.step('doc', 'version', 'studio', 'part')
    assert caught.value.code == 'cad_native_backoff_invalid'
    native._opener.open.assert_not_called()


def test_agent_passes_the_same_holds_to_both_mcp_clients_without_mutating_native_config(tmp_path, monkeypatch):
    from r3_orchestrator.api import agents
    existing = [{'method': 'GET', 'resource': '/api/v*/parts/**', 'until': None}]
    supplied = [{'method': 'POST', 'resource': '/native/api/**/translations', 'until': None}]
    original = {'command': 'synthetic-native', 'env': {'KEEP_ME': 'fixture', 'ONSHAPE_NATIVE_BACKOFFS': json.dumps(existing)}}
    monkeypatch.setattr(agents, 'native_mcp_config', lambda: original)
    monkeypatch.setenv('R3_ONSHAPE_BACKOFFS', json.dumps(supplied))
    config = agents.mcp_config(tmp_path)['mcpServers']
    assert json.loads(config['r3']['env']['R3_ONSHAPE_BACKOFFS']) == supplied
    assert json.loads(config['onshape_native']['env']['ONSHAPE_NATIVE_BACKOFFS']) == existing + supplied
    assert json.loads(original['env']['ONSHAPE_NATIVE_BACKOFFS']) == existing
    assert config['onshape_native']['env']['KEEP_ME'] == 'fixture'



def test_redirect_429_is_recorded_for_the_actual_download_resource(tmp_path):
    original = '/native/api/v17/partstudios/d/doc/v/version/e/studio/stl'
    target = '/native/api/v17/documents/d/doc/externaldata/file'
    headers = Message()
    headers['Location'] = target
    redirect = HTTPError('http://127.0.0.1:8766' + original, 307, 'redirect', headers, io.BytesIO(b''))
    native = client(tmp_path)
    native._opener = Mock()
    native._opener.open.side_effect = [redirect, rejection()]
    with pytest.raises(NativeRateLimitError) as caught:
        native._request('GET', original)
    assert caught.value.details['resource'] == target
    assert native._opener.open.call_count == 2
    restarted = client(tmp_path)
    restarted._opener = Mock()
    with pytest.raises(NativeRateLimitError) as repeated:
        restarted._request('GET', target)
    assert repeated.value.details['request_sent'] is False
    restarted._opener.open.assert_not_called()
