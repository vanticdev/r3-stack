"""Persist observed native pacing by exact method/path, independently of job bodies."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import fnmatch
import os
import json
from pathlib import Path
import tempfile
from typing import Any

from r3_model.pulled import PullError

SCHEMA = 'r3/native-backoff@1'


def _path(cache: Path, method: str, resource: str) -> Path:
    root = Path(cache).resolve()
    folder = root / 'resource-backoffs'
    if folder.is_symlink() or folder.resolve().parent != root:
        raise PullError('cad_native_backoff_invalid', 'Native backoff directory must stay inside the export cache')
    key = hashlib.sha256(json.dumps([method, resource]).encode()).hexdigest()
    path = folder / (key + '.json')
    if path.is_symlink():
        raise PullError('cad_native_backoff_invalid', 'Native backoff evidence must not be a symlink')
    return path


def active(cache: Path | None, method: str, resource: str) -> dict[str, Any] | None:
    if cache is None:
        return None
    path = _path(cache, method, resource)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('method') != method
                or value.get('resource') != resource or not isinstance(value.get('message'), str)
                or not isinstance(value.get('details'), dict) or value['details'].get('http_status') != 429):
            raise ValueError('Record differs from the observed resource')
        raw = value['details'].get('resume_at')
        if raw is not None:
            resume = datetime.fromisoformat(raw)
            if resume.tzinfo is None:
                raise ValueError('Reset must be timezone-aware')
            if datetime.now(timezone.utc) >= resume:
                return None
    except (ValueError, TypeError, OSError) as error:
        raise PullError('cad_native_backoff_invalid', 'Native backoff evidence is invalid; retain it for diagnosis',
                        {'backoff_state': str(path)}) from error
    return {**value, 'details': {**value['details'], 'backoff_state': str(path), 'request_sent': False}}


def remember(cache: Path | None, method: str, resource: str, message: str, details: dict[str, Any]) -> None:
    if cache is None:
        return
    path = _path(cache, method, resource)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Retain pacing evidence only: no export body, credentials or prior job ID.
    keys = ('http_status', 'retry_after', 'retry_after_seconds', 'resume_at', 'observed_at',
            'response_excerpt', 'rate_limit_remaining')
    evidence = {key: details[key] for key in keys if key in details}
    evidence.update(method=method, resource=resource)
    value = {'schema': SCHEMA, 'method': method, 'resource': resource, 'message': message, 'details': evidence}
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    temporary.chmod(0o444)
    temporary.replace(path)


def operator_active(method: str, resource: str) -> dict[str, Any] | None:
    """Honor only explicitly supplied operator scopes, never infer an account outage."""
    try:
        rows = json.loads(os.environ.get('R3_ONSHAPE_BACKOFFS', '[]'))
        if not isinstance(rows, list):
            raise ValueError('Expected a list')
        now = datetime.now(timezone.utc)
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Expected a scoped record')
            pattern, verb, raw = row['resource'], row['method'], row['until']
            if (verb not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'} or not isinstance(pattern, str)
                    or not pattern.startswith(('/api/', '/native/api/')) or any(c.isspace() for c in pattern)):
                raise ValueError('Invalid method or resource pattern')
            resume = datetime.fromisoformat(raw) if raw is not None else None
            if resume is not None and resume.tzinfo is None:
                raise ValueError('Reset must be timezone-aware')
            if resume is not None and now >= resume:
                continue
            if verb == method and fnmatch.fnmatchcase(resource, pattern):
                return {'method': method, 'resource': resource, 'hold_resource': pattern,
                        'resume_at': resume.isoformat() if resume is not None else None,
                        'source': 'operator_context', 'request_sent': False}
    except (KeyError, ValueError, TypeError) as error:
        raise PullError('cad_native_backoff_invalid', 'Invalid operator transport backoff context') from error
    return None
