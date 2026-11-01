"""Read a pinned assembly through the authenticated browser-session REST bridge."""
from __future__ import annotations

import base64
from typing import Any

from r3_model.pulled import PullError


def _identity(value: dict[str, Any]) -> tuple:
    return (value.get('documentId'), value.get('elementId'),
            value.get('documentMicroversion') or value.get('documentVersion'),
            value.get('fullConfiguration') or value.get('configuration') or '')


def assembly(transport: Any, url: str) -> dict[str, Any]:
    from .pull import target
    pin = target(url)
    did, vid, eid = (pin[k] for k in ('document_id', 'version_id', 'element_id'))
    query = {'includeMateFeatures': True, 'includeMateConnectors': True, 'includeNonSolids': True,
             'configuration': pin['configuration']}
    definition = transport.api_request('GET', f'/api/v9/assemblies/d/{did}/v/{vid}/e/{eid}', query=query)
    if not isinstance(definition, dict) or 'rootAssembly' not in definition:
        raise PullError('cad_pull_incomplete', 'REST assembly definition is missing')
    version = transport.api_request('GET', f'/api/v9/documents/d/{did}/versions/{vid}')
    microversion = version.get('microversion')
    if not microversion:
        raise PullError('cad_pull_incomplete', 'Pinned version has no microversion')
    definitions = {_identity(a): a for a in definition.get('subAssemblies', [])}
    root = definition['rootAssembly']
    occurrences = {tuple(o['path']): o for o in root.get('occurrences', [])}
    from .part_records import NativePartRecords
    parts = NativePartRecords(definition.get('parts', []))
    rows, metadata = [], {}

    def visit(current: dict[str, Any], parent: list[str], ancestors: set[tuple]) -> None:
        if len(parent) > 100:
            raise PullError('cad_pull_incomplete', 'Assembly nesting exceeds 100 levels')
        features = [f for f in current.get('features', []) if not f.get('suppressed')]
        if features:
            source_doc = current.get('documentId') or did
            source_version = current.get('documentVersion') or (vid if source_doc == did else None)
            source_element = current.get('elementId') or eid
            if not source_version:
                raise PullError('cad_pull_version_required', 'Subassembly mate validation requires a pinned source version')
            state = transport.api_request('GET', f'/api/v9/assemblies/d/{source_doc}/v/{source_version}/e/{source_element}/features',
                                          query={'configuration': current.get('fullConfiguration') or current.get('configuration') or ''})
            states = state.get('featureStates') or {}
            failed = [f['id'] for f in features if states.get(f['id'], {}).get('featureStatus') != 'OK']
            if failed:
                raise PullError('cad_pull_mate_invalid', f'Assembly features are not verified OK: {failed}')
        for instance in current.get('instances', []):
            if instance.get('suppressed'):
                continue
            path = [*parent, instance['id']]
            occurrence = occurrences.get(tuple(path))
            if not occurrence or occurrence.get('transform') is None:
                raise PullError('cad_pull_incomplete', f'No solved REST occurrence transform for {path}')
            kind = str(instance.get('type', '')).lower()
            row = {'kind': 'occurrence', 'instance_type': instance.get('type'), 'occurrence_path': path,
                   'name': instance.get('name'), 'reference': dict(instance), 'transform': occurrence['transform'],
                   'fixed': occurrence.get('fixed', instance.get('isFixed', False))}
            rows.append(row)
            if kind == 'assembly':
                key = _identity(instance)
                if key in ancestors or key not in definitions:
                    raise PullError('cad_pull_incomplete', f'Missing or cyclic REST subassembly {path}')
                visit(definitions[key], path, ancestors | {key})
            elif kind == 'part':
                key = (instance['documentId'], instance['elementId'], instance['partId'],
                       instance.get('fullConfiguration') or instance.get('configuration') or '')
                ref = parts.resolve(instance)
                version_id = instance.get('documentVersion') or ref.get('documentVersion') or (vid if key[0] == did else None)
                if not version_id:
                    raise PullError('cad_pull_version_required', f'External part {path} needs a pinned version for STEP')
                row['reference']['documentVersion'] = version_id
                cache = (key[0], version_id, key[1], key[3])
                if cache not in metadata:
                    values = transport.api_request('GET', f'/api/v9/parts/d/{key[0]}/v/{version_id}/e/{key[1]}',
                                                   query={'configuration': key[3]})
                    metadata[cache] = {p['partId']: p for p in values}
                value = metadata[cache].get(key[2])
                if not value or value.get('isMesh'):
                    raise PullError('cad_pull_incomplete', f'Native solid part metadata missing for {path}')
                ref['material'] = value.get('material')
                row['body_type'] = value.get('bodyType', 'solid')
                row['name'] = row['name'] or value.get('name')
            else:
                raise PullError('cad_pull_incomplete', f'Unsupported native instance type {kind!r} at {path}')

    visit(root, [], {_identity(root)})
    represented = {tuple(row['occurrence_path']) for row in rows}
    unexpected = [path for path in occurrences if path not in represented and not occurrences[path].get('suppressed')]
    if unexpected:
        raise PullError('cad_pull_incomplete', f'Unresolved REST occurrences: {unexpected}')
    return {'definition': definition, 'rows': rows, 'microversion': microversion, 'source': 'browser-session-rest'}


def capture(transport: Any, url: str) -> tuple[bytes, dict[str, Any]]:
    from .pull import target
    pin = target(url)
    did, vid, eid = (pin[k] for k in ('document_id', 'version_id', 'element_id'))
    version = transport.api_request('GET', f'/api/v9/documents/d/{did}/versions/{vid}')
    data = transport.api_request('GET', f'/api/v9/assemblies/d/{did}/v/{vid}/e/{eid}/shadedviews',
                                 query={'viewMatrix': '0.612372,0.612372,0,0,-0.353553,0.353553,0.707107,0,0.707107,-0.707107,0.707107,0',
                                        'outputWidth': 1200, 'outputHeight': 900, 'pixelSize': 0,
                                        'showAllParts': True, 'configuration': pin['configuration']})
    images = data.get('images') or []
    value = images[0] if images else None
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    try:
        image = base64.b64decode(value, validate=True)
    except (TypeError, ValueError):
        raise PullError('cad_pull_view_unavailable', 'REST shaded view returned no base64 PNG') from None
    if not image.startswith(b'\x89PNG\r\n\x1a\n'):
        raise PullError('cad_pull_view_unavailable', 'REST shaded view is not a PNG')
    return image, {'microversion': version.get('microversion'), 'version_id': vid, 'source': 'onshape-rest-shadedview', 'url': url}
