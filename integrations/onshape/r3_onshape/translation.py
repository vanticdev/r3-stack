"""Resumable native export jobs. Never replay a submission with unknown outcome."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Iterator
from urllib.parse import quote

from r3_model.pulled import PullError


def project_export_cache(root: Path) -> Path:
    root = Path(root).resolve()
    build = (root / 'build').resolve()
    folder = (build / 'cad/native-exports').resolve()
    if root not in build.parents or build not in folder.parents:
        raise PullError('cad_export_invalid', 'Native export jobs must stay inside the project build directory')
    return folder


def _save(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(state, stream, indent=2, allow_nan=False)
            stream.write('\n')
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    temporary.chmod(0o444)
    temporary.replace(path)


@contextmanager
def _locked(folder: Path | None) -> Iterator[Path | None]:
    if folder is None:
        yield None
        return
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'job.lock'
    if path.is_symlink():
        raise PullError('cad_native_export_cache_invalid', 'Native export lock must not be a symlink')
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PullError('cad_native_export_busy', 'This pinned export is already running; wait for that request') from None
        try:
            yield folder / 'job.json'
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def translated_file(client: Any, endpoint: str, body: dict[str, Any]) -> bytes:
    """Submit once, poll the original job, and retain unchanged completed bytes.

    The caller supplies only native routes and immutable version/part selectors.
    Cached job/output files are platform outputs, never authored geometry.
    """
    request = {'endpoint': endpoint, 'body': body, 'companion': getattr(client, 'base', '')}
    identity = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    cache = getattr(client, 'export_jobs', None)
    folder = Path(cache) / identity if cache is not None else None
    with _locked(folder) as path:
        state: dict[str, Any] = {'schema': 'r3/native-export@1', 'request': request, 'phase': 'new'}
        if path is not None and path.exists():
            if path.is_symlink():
                raise PullError('cad_native_export_cache_invalid', 'Native export state must not be a symlink')
            try:
                state = json.loads(path.read_text())
            except (ValueError, OSError) as error:
                raise PullError('cad_native_export_cache_invalid',
                                'Native export state cannot be read; retain it for diagnosis',
                                {'export_state': str(path)}) from error
            if (not isinstance(state, dict) or state.get('schema') != 'r3/native-export@1'
                    or state.get('request') != request or state.get('phase') not in
                    {'new', 'submitting', 'unknown', 'rejected', 'submitted', 'complete'}):
                raise PullError('cad_native_export_cache_invalid', 'Native export state differs from the requested pin/options')
        if state.get('file'):
            filename = state['file']
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise PullError('cad_native_export_cache_invalid', 'Invalid cached export filename')
            target = folder / filename
            if target.is_symlink() or not target.is_file() or target.stat().st_mode & 0o222:
                raise PullError('cad_native_export_cache_modified', 'Cached native export is missing or writable; retain it for diagnosis')
            data = target.read_bytes()
            if hashlib.sha256(data).hexdigest() != state.get('sha256'):
                raise PullError('cad_native_export_cache_modified', 'Cached native export SHA-256 changed; retain it for diagnosis')
            return data
        if state['phase'] == 'complete' or state['phase'] == 'submitted' and not state.get('job'):
            raise PullError('cad_native_export_cache_invalid', 'Native export state is missing its completed file or submitted job')

        def response(raw: bytes) -> dict[str, Any]:
            try:
                value = json.loads(raw)
            except (ValueError, TypeError):
                value = None
            if not isinstance(value, dict):
                raise PullError('cad_native_response_invalid', 'Native export returned invalid JSON; inspect the existing operation before resubmitting',
                                {'export_request': request, 'export_state': str(path) if path else None})
            return value

        def native_request(method: str, resource: str, payload: Any = None) -> bytes:
            previous = state.get('last_error') or {}
            if previous.get('http_status') == 429:
                try:
                    resume = datetime.fromisoformat(previous['resume_at'])
                    expired = resume.tzinfo is not None and datetime.now(timezone.utc) >= resume
                except (KeyError, TypeError, ValueError):
                    expired = False
                if not expired:
                    raise PullError(previous['code'], previous['message'], previous['details'])
            try:
                data = client._request(method, resource, payload) if payload is not None else client._request(method, resource)
            except PullError as error:
                error.details.update(export_request=request, export_state=str(path) if path else None)
                if state.get('job'):
                    error.details.setdefault('translation_id', state['job']['id'])
                state['last_error'] = {'code': error.code, 'message': str(error), 'details': error.details,
                                       'http_status': error.details.get('http_status'), 'resume_at': error.details.get('resume_at')}
                _save(path, state)
                raise
            state.pop('last_error', None)
            return data

        job = state.get('job')
        if not job:
            if state['phase'] in {'submitting', 'unknown'}:
                raise PullError('cad_native_export_unknown',
                    'Previous export submission has unknown outcome; inspect it before another submission',
                    {'export_request': request, 'export_state': str(path)})
            state['phase'] = 'submitting'
            _save(path, state)  # survive provider/worker loss before receiving the ID
            try:
                job = response(native_request('POST', endpoint, body))
            except PullError as error:
                error.details.update(export_request=request, export_state=str(path) if path else None)
                # A definite HTTP rejection created no export. Everything else
                # may have reached the server and must never be replayed.
                status = error.details.get('http_status')
                state['phase'] = 'rejected' if status in {400, 401, 403, 404, 405, 422, 429} else 'unknown'
                known = error.details.get('translation')
                if not known and error.details.get('translation_id'):
                    known = {'id': error.details['translation_id'], 'requestState': 'ACTIVE'}
                if isinstance(known, dict) and isinstance(known.get('id'), str):
                    state.update(phase='submitted', job=known)
                state['error'] = {'code': error.code, 'message': str(error), **error.details}
                _save(path, state)
                raise
            if not isinstance(job, dict) or not isinstance(job.get('id'), str) or not job['id']:
                state['phase'] = 'unknown'
                _save(path, state)
                raise PullError('cad_native_export_unknown', 'Native submission returned no job ID; inspect before resubmitting')
            state.update(phase='submitted', job=job)
            _save(path, state)
        if not isinstance(job, dict) or not isinstance(job.get('id'), str) or not job['id']:
            raise PullError('cad_native_export_cache_invalid', 'Cached native job has no valid translation ID')
        details = {'translation_id': job['id'], 'format': body['formatName'], 'export_request': request,
                   'export_state': str(path) if path else None}
        deadline = time.monotonic() + 1800
        while job.get('requestState') not in {'DONE', 'FAILED'}:
            if time.monotonic() > deadline:
                raise PullError('cad_pull_translation_pending',
                    f"{body['formatName']} export {job['id']} is still active; call the same R3 operation to resume it, not resubmit", details)
            time.sleep(5)
            job = response(native_request('GET', '/native/api/v17/translations/' + quote(job['id'], safe='')))
            if not isinstance(job, dict) or job.get('id') != details['translation_id']:
                raise PullError('cad_native_response_invalid', 'Native poll returned a different translation ID', details)
            state['job'] = job
            _save(path, state)
        if job['requestState'] == 'FAILED':
            raise PullError('cad_pull_translation_failed', f"{body['formatName']} export failed: {job.get('failureReason')}",
                            {**details, 'failure_reason': job.get('failureReason')})
        files = job.get('resultExternalDataIds') or []
        if len(files) != 1 or not job.get('resultDocumentId'):
            raise PullError('cad_pull_translation_failed', 'Expected one selected-part file; refusing an ambiguous export', details)
        resource = '/native/api/v17/documents/d/' + quote(job['resultDocumentId'], safe='') + '/externaldata/' + quote(files[0], safe='')
        data = native_request('GET', resource)
        if not data:
            raise PullError('cad_export_empty', 'Native export returned an empty file', details)
        if folder is not None:
            digest = hashlib.sha256(data).hexdigest()
            filename = digest + '.' + body['formatName'].lower()
            target = folder / filename
            if target.is_symlink() or target.exists() and target.read_bytes() != data:
                raise PullError('cad_native_export_cache_modified', 'Existing native export differs from its checksum', details)
            if not target.exists():
                target.write_bytes(data)
            target.chmod(0o444)
            state.update(phase='complete', file=filename, sha256=digest)
            _save(path, state)
        return data
