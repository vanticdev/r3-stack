"""Shared operations used unchanged by CLI, MCP and HTTP."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .bank import ModelBank
from .fetch import acquire, convert, fetch, validate
from .ingest import slug
from .schema import ModelEntry, Source


def search(query: str = '', category: str | None = None, manufacturer: str | None = None,
           specs: dict[str, list[float | None]] | None = None, limit: int = 30, *, project: str | Path | None = None) -> dict[str, Any]:
    return {'ok': True, 'models': ModelBank(project=project).search(query, category=category, manufacturer=manufacturer, specs=specs, limit=limit)}


def show(model_id: str, *, project: str | Path | None = None) -> dict[str, Any]:
    return {'ok': True, 'model': ModelBank(project=project).get(model_id).model_dump(mode='json')}


def add(name: str, category: str, mpn: str = '', manufacturer: str = '', url: str = '', onshape: str = '',
        model_id: str = '', license: str = 'unspecified; verify redistribution terms', format: str = 'step',
        member: str = '', catalog_component: str | None = None, *, project: str | Path | None = None) -> dict[str, Any]:
    bank = ModelBank(project=project)
    identifier = model_id or slug(f'{manufacturer}-{mpn or name}')
    if any(e.id == identifier for e in bank.entries()):
        raise ValueError(f'model already exists: {identifier}')
    if bool(url) == bool(onshape):
        raise ValueError('provide exactly one of url or onshape')
    if onshape:
        match = re.search(r'/documents/([a-f0-9]{24})/v/([a-f0-9]{24})/e/([a-f0-9]{24})', onshape)
        if not match:
            raise ValueError('Onshape URL must point to an immutable public version (/v/, not /w/)')
        # Public document metadata cannot be queried under the native-only
        # contract. Record an explicitly unqualified pointer; the agent must
        # inspect/import the immutable version with native tools before pull.
        did, vid, eid = match.groups()
        source = Source(kind='onshape_public', url=onshape, did=did, vid=vid, eid=eid, license=license)
        entry = ModelEntry(id=identifier, name=name, category=category, mpn=mpn, manufacturer=manufacturer,
                           catalog_component=catalog_component, sources=[source])
    else:
        source = Source(kind='local' if url.startswith('file:') else 'vendor_step', url=url, format=format, license=license, member=member)
        path, source = acquire(source, pin=True)
        geometry = convert(path, source)
        entry = ModelEntry(id=identifier, name=name, category=category, mpn=mpn, manufacturer=manufacturer,
                           catalog_component=catalog_component, sources=[source])
        entry.validation = validate(entry, geometry['bbox_m'])
    bank.put(entry)
    return {'ok': True, 'model': entry.model_dump(mode='json')}


def fetch_model(model_id: str, *, project: str | Path | None = None) -> dict[str, Any]:
    return fetch(ModelBank(project=project).get(model_id))


def validate_model(model_id: str, *, project: str | Path | None = None) -> dict[str, Any]:
    bank = ModelBank(project=project)
    entry = bank.get(model_id)
    result = fetch(entry)
    if not result['ok']:
        return result
    entry.validation = validate(entry, result['bbox_m'])
    bank.put(entry, replace=True)
    return {'ok': entry.validation.status != 'mismatch', 'id': model_id, 'validation': entry.validation.model_dump(),
            'code': 'model_datasheet_mismatch' if entry.validation.status == 'mismatch' else 'model_validation',
            'message': entry.validation.notes}


def guard(call: Any) -> dict[str, Any]:
    try:
        return call()
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        code = getattr(error, 'code', None)
        return {'ok': False, 'code': code if isinstance(code, str) else 'model_bank_error', 'message': str(error),
                **getattr(error, 'details', {})}
