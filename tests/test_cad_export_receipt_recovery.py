"""An explicit export retry inspects its own saved receipt, never recreates a write."""
import io
import json
from email.message import Message
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from r3_model.pulled import PullError
from test_cad_translation_resume import client

PATH='/native/api/v17/partstudios/d/doc/v/version/e/studio/translations'
JOB={'id':'saved-job','requestState':'FAILED','failureReason':'Submission outcome unknown',
     'submissionOutcome':'unknown'}


def lost_reply(tmp_path):
    native=client(tmp_path)
    native._opener=Mock()
    native._opener.open.side_effect=HTTPError('http://127.0.0.1:8766'+PATH,502,'lost reply',Message(),io.BytesIO(json.dumps({
        'message':'Frame disappeared; inspect saved export receipt', 'translationId':JOB['id'],'translation':JOB}).encode()))
    with pytest.raises(PullError) as first:native.step('doc','version','studio','part')
    assert first.value.details['translation_id']==JOB['id']
    assert native._opener.open.call_count==1
    return native


def test_retry_inspects_saved_receipt_and_downloads_unchanged_file(tmp_path):
    first=lost_reply(tmp_path)
    native=client(tmp_path)
    native._opener=Mock()
    done={**JOB,'requestState':'DONE','submissionOutcome':'confirmed','resultDocumentId':'doc',
          'resultExternalDataIds':['native-file']}
    native._opener.open.side_effect=[io.BytesIO(json.dumps(done).encode()),io.BytesIO(b'unchanged exported STEP payload')]
    assert native.step('doc','version','studio','part')==b'unchanged exported STEP payload'
    calls=native._opener.open.call_args_list
    assert [c.args[0].get_method() for c in calls]==['GET','GET']
    assert parse_qs(urlsplit(calls[0].args[0].full_url).query)=={'inspectUnknown':['true']}
    assert '/translations/saved-job' in calls[0].args[0].full_url
    assert '/externaldata/native-file' in calls[1].args[0].full_url
    native._opener.open.reset_mock()
    assert native.step('doc','version','studio','part')==b'unchanged exported STEP payload'
    native._opener.open.assert_not_called()


def test_no_matching_receipt_stays_unresolved_without_poll_loop_or_new_post(tmp_path):
    lost_reply(tmp_path)
    native=client(tmp_path);native._opener=Mock()
    def pending(request,**kwargs):
        assert request.get_method()=='GET'
        assert parse_qs(urlsplit(request.full_url).query)=={'inspectUnknown':['true']}
        return io.BytesIO(json.dumps({**JOB,'requestState':'ACTIVE'}).encode())
    native._opener.open.side_effect=pending
    for count in (1,2):
        with pytest.raises(PullError) as unresolved:native.step('doc','version','studio','part')
        assert unresolved.value.code=='cad_native_export_unresolved'
        assert unresolved.value.details['translation_id']==JOB['id']
        assert native._opener.open.call_count==count
    record,=(tmp_path/'jobs').glob('*/job.json')
    assert json.loads(record.read_text())['job']['id']==JOB['id']


def test_receipt_inspection_rejects_a_different_job_identity(tmp_path):
    lost_reply(tmp_path)
    native=client(tmp_path);native._opener=Mock()
    native._opener.open.return_value=io.BytesIO(json.dumps({'id':'different-job','requestState':'DONE'}).encode())
    with pytest.raises(PullError) as wrong:native.step('doc','version','studio','part')
    assert wrong.value.code=='cad_native_response_invalid'
    assert native._opener.open.call_count==1



def test_provider_loss_after_post_uses_saved_client_receipt_and_no_new_post(tmp_path):
    native=client(tmp_path);native.export_receipts=True;native._opener=Mock()
    sent=[]
    def dies(request,**kwargs):
        sent.append(request)
        record,=(tmp_path/'jobs').glob('*/job.json')
        state=json.loads(record.read_text())
        assert state['phase']=='submitting'
        assert state['submission_receipt']['id']==request.headers['X-onshape-native-receipt']
        raise SystemExit('provider exited after submission')
    native._opener.open.side_effect=dies
    with pytest.raises(SystemExit):native.step('doc','version','studio','part')
    record,=(tmp_path/'jobs').glob('*/job.json');state=json.loads(record.read_text())
    receipt=state['submission_receipt']
    restarted=client(tmp_path);restarted.export_receipts=True;restarted._opener=Mock()
    done={**JOB,'requestState':'DONE','submissionOutcome':'confirmed','resultDocumentId':'doc','resultExternalDataIds':['file']}
    lookup={'receiptId':receipt['id'],'request_sha256':receipt['request_sha256'],'translation':JOB}
    restarted._opener.open.side_effect=[io.BytesIO(json.dumps(lookup).encode()),
        io.BytesIO(json.dumps(done).encode()),io.BytesIO(b'unchanged native bytes')]
    assert restarted.step('doc','version','studio','part')==b'unchanged native bytes'
    calls=restarted._opener.open.call_args_list
    assert [c.args[0].get_method() for c in calls]==['GET','GET','GET']
    assert '/exportreceipts/'+receipt['id'] in calls[0].args[0].full_url
    assert len(sent)==1 and sent[0].get_method()=='POST'


def test_missing_client_receipt_never_authorizes_another_submission(tmp_path):
    native=client(tmp_path);native.export_receipts=True;native._opener=Mock()
    native._opener.open.side_effect=TimeoutError('reply lost')
    with pytest.raises(PullError):native.step('doc','version','studio','part')
    restarted=client(tmp_path);restarted.export_receipts=True;restarted._opener=Mock()
    def missing(request,**kwargs):
        assert request.get_method()=='GET' and '/exportreceipts/' in request.full_url
        raise HTTPError(request.full_url,404,'receipt missing',Message(),io.BytesIO(b'{"message":"missing"}'))
    restarted._opener.open.side_effect=missing
    with pytest.raises(PullError) as unresolved:restarted.step('doc','version','studio','part')
    assert unresolved.value.code=='cad_native_export_unknown'
    assert unresolved.value.details['receipt_id']
    assert restarted._opener.open.call_count==1


def test_receipt_for_a_different_request_is_rejected_without_poll_or_download(tmp_path):
    native=client(tmp_path);native.export_receipts=True;native._opener=Mock()
    native._opener.open.side_effect=TimeoutError('reply lost')
    with pytest.raises(PullError):native.step('doc','version','studio','part')
    record,=(tmp_path/'jobs').glob('*/job.json');state=json.loads(record.read_text());receipt=state['submission_receipt']
    restarted=client(tmp_path);restarted.export_receipts=True;restarted._opener=Mock()
    restarted._opener.open.return_value=io.BytesIO(json.dumps({'receiptId':receipt['id'],
        'request_sha256':'0'*64,'translation':JOB}).encode())
    with pytest.raises(PullError) as wrong:restarted.step('doc','version','studio','part')
    assert wrong.value.code=='cad_native_export_receipt_invalid'
    assert restarted._opener.open.call_count==1


def test_receipt_rate_rejection_keeps_deadline_and_requires_a_new_explicit_attempt(tmp_path,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from r3_onshape import translation,backoff
    native=client(tmp_path);native.export_receipts=True;native._opener=Mock()
    native._opener.open.side_effect=TimeoutError('reply lost')
    with pytest.raises(PullError):native.step('doc','version','studio','part')
    record,=(tmp_path/'jobs').glob('*/job.json');state=json.loads(record.read_text());receipt=state['submission_receipt']
    observed=datetime.now(timezone.utc);resume=observed+timedelta(seconds=120)
    rejection={'schema':'onshape/ui-resource-backoff@1','method':'POST','resource':'/api/documents/d/doc/v/version/translate',
        'http_status':429,'retry_after':'120','retry_after_seconds':120,'observed_at':observed.isoformat(),'resume_at':resume.isoformat()}
    failed={'id':'saved-job','requestState':'FAILED','failureReason':'Rate rejected','rejection':rejection}
    recovered={'receiptId':receipt['id'],'request_sha256':receipt['request_sha256'],'translation':failed}
    resumed=client(tmp_path);resumed.export_receipts=True;resumed._opener=Mock()
    resumed._opener.open.return_value=io.BytesIO(json.dumps(recovered).encode())
    with pytest.raises(PullError) as limited:resumed.step('doc','version','studio','part')
    assert limited.value.code=='cad_native_backoff_active'
    assert limited.value.details['http_status']==200 and limited.value.details['original_http_status']==429
    assert limited.value.details['resume_at']==resume.isoformat()
    assert json.loads(record.read_text())['phase']=='rejected'
    resumed._opener.open.reset_mock()
    with pytest.raises(PullError):resumed.step('doc','version','studio','part')
    resumed._opener.open.assert_not_called()
    after=resume+timedelta(seconds=1)
    class After(datetime):
        @classmethod
        def now(cls,tz=None):return after
    monkeypatch.setattr(translation,'datetime',After);monkeypatch.setattr(backoff,'datetime',After)
    new={'id':'new-job','requestState':'DONE','resultDocumentId':'doc','resultExternalDataIds':['file']}
    resumed._opener.open.side_effect=[io.BytesIO(json.dumps(new).encode()),io.BytesIO(b'unchanged bytes')]
    assert resumed.step('doc','version','studio','part')==b'unchanged bytes'
    sent=resumed._opener.open.call_args_list[0].args[0]
    assert sent.get_method()=='POST' and sent.headers['X-onshape-native-receipt']!=receipt['id']
    assert json.loads(record.read_text())['previous_receipts']==[receipt]
