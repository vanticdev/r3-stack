"""Purchased-part path: bank discovery, native STEP translation and public-version insertion.

No geometry is authored for a model found in the bank. If the measured model
cannot be aligned and validated, stop with a diagnostic instead of sculpting a
replacement. Only an explicitly reported missing-model proxy uses standard CAD.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from r3_catalog.modelbank import ModelBank, ModelEntry
from r3_catalog.modelbank.fetch import acquire, unpack, validate
from r3_model.diagnostics import Diagnostic


def diagnostics(project: Any, result: Any, bank: ModelBank | None = None) -> None:
    bank = bank or ModelBank(project=project.root)
    for cid, comp in project.spec.components.items():
        component = project.catalog.component(comp.type)
        if component.mechanical.fabricated or component.kind == 'pcb':
            continue
        entry = bank.resolve(component)
        if entry is None and component.model and component.model.source in ('vendor', 'kicad'):
            result.add(Diagnostic.error('model_bank_registration_required',
                                        f'{cid}: a catalog model exists; add it to the bank before native CAD import', subjects=[cid],
                                        data={'url': component.model.url, 'path': component.model.path}))
            continue
        if entry is None or not entry.available:
            result.add(Diagnostic.warning('cad_purchased_proxy', f'{cid}: {entry.proxy_reason if entry else "no exact bank model"}',
                                          subjects=[cid], data={'model_bank_id': entry.id if entry else None,
                                          'searched_sources': entry.searched_sources if entry else [], 'proxy': True}))
        elif entry.validation.status == 'mismatch':
            result.add(Diagnostic.error('model_datasheet_mismatch', f'{cid}: bank geometry disagrees with its datasheet',
                                        subjects=[cid], data={'model_bank_id': entry.id, **entry.validation.model_dump()}))
        elif entry.validation.status != 'passed':
            result.add(Diagnostic.warning('model_geometry_unverified', f'{cid}: source found; geometry and interface validation pending',
                                          subjects=[cid], data={'model_bank_id': entry.id, 'validation': entry.validation.status}))

    # Plain screws/nuts are purchased too; report one warning per size with all
    # instances as subjects, rather than silently treating generated geometry as sourced CAD.
    grouped: dict[str, list[str]] = {}
    mechanical = getattr(project.spec, 'mechanical', None)
    for fastener in getattr(mechanical, 'fasteners', []):
        if fastener.body:
            continue
        for tid, length, iid in ((fastener.type, fastener.length, fastener.part), (fastener.nut, None, fastener.nut_part)):
            if tid and iid:
                grouped.setdefault(fastener_model_id(tid, length), []).append(iid)
    by_id = {e.id: e for e in bank.entries()}
    for identifier, subjects in grouped.items():
        entry = by_id.get(identifier)
        if not entry or not entry.available:
            result.add(Diagnostic.warning('cad_purchased_proxy', f'{identifier}: exact supplier CAD is not pinned',
                                          subjects=subjects, data={'model_bank_id': identifier, 'quantity': len(subjects),
                                          'searched_sources': entry.searched_sources if entry else [], 'proxy': True}))


def fastener_model_id(type_id: str, length: float | None) -> str:
    return f'fastener-{type_id}' + (f'-{length * 1000:g}mm' if length else '')


def bank_entry(component: Any, project: Any = None) -> ModelEntry | None:
    return ModelBank(project=project.root if project else None).resolve(component)


def _interface_frame(entry: ModelEntry, connector: Any) -> dict[str, list[float]] | None:
    frame = entry.interface_frames.get(connector.name)
    if frame is None and connector.hole is None and connector.name == f'mc_{connector.interface}':
        # Bank records may use the authored interface name; mc_ is the native
        # feature label. Hole-seat connectors need their own measured evidence,
        # never the parent interface's frame. Numeric checks remain identical.
        frame = entry.interface_frames.get(connector.interface)
    return frame


def import_blockers(part: Any) -> list[dict[str, Any]]:
    """Offline prerequisites for the same frame checks used by native import.

    Do not invent geometry-derived frames from catalog envelopes. Missing
    evidence must be reviewed in the model bank.
    """
    from r3_connectors.frames import Frame

    entry = ModelEntry.model_validate(part.bank_model)
    blockers = []
    if entry.validation.status == 'mismatch':
        blockers.append({'code': 'model_datasheet_mismatch', 'reason': entry.validation.notes})
    if not entry.placement:
        blockers.append({'code': 'model_frame_unverified', 'reason': 'record the real model placement before inserting it'})
    else:
        placement = Frame.from_placement(type('Placement', (), entry.placement)()).matrix()
        if not np.allclose(placement[:3, :3], np.eye(3)):
            blockers.append({'code': 'model_frame_unverified', 'reason': 'nontrivial orientation requires a reviewed native Transform'})
    if entry.validation.status != 'passed' or not entry.interface_frames:
        blockers.append({'code': 'model_features_unverified', 'validation_status': entry.validation.status,
                         'reason': 'inspect real holes/faces and record interface_frames + key-feature evidence'})
    missing, mismatched = [], []
    for connector in part.connectors:
        frame = _interface_frame(entry, connector)
        if frame is None:
            missing.append(connector.name)
        elif not np.allclose(Frame.from_placement(type('Placement', (), frame)()).matrix(), connector.frame,
                             atol=entry.validation.tolerance_m):
            mismatched.append(connector.name)
    if missing:
        blockers.append({'code': 'model_features_unverified', 'missing_connectors': missing,
                         'reason': 'missing geometry-derived frames: ' + ', '.join(missing)})
    if mismatched:
        blockers.append({'code': 'model_datasheet_mismatch', 'mismatched_connectors': mismatched,
                         'reason': 'model interfaces differ from catalog: ' + ', '.join(mismatched)})
    return blockers


def import_studio(api: Any, did: str, wid: str, part: Any) -> Any:
    """Import a bank STEP, orient its actual bodies, validate, then add native mate connectors and mass.

    Translations and part-list/geometry endpoints do not consume the feature-list
    quota. Feature creation is still needed for frame alignment and mate connectors;
    a quota failure is propagated, never treated as an import success.
    """
    from . import features as fx
    from .studio import PartStudioBuilder, StudioBuildError, metadata_property_ids
    from .imports import _wait
    from r3_connectors.frames import Frame

    entry = ModelEntry.model_validate(part.bank_model)
    blockers = import_blockers(part)
    if blockers:
        raise StudioBuildError(part.name, blockers[0]['code'], blockers[0]['reason'])
    failures = []
    for source in sorted(entry.sources, key=lambda s: {'vendor_step': 0, 'kicad_3d': 1, 'database': 2, 'local': 3, 'onshape_public': 4}[s.kind]):
        if source.kind == 'onshape_public' or source.format not in ('step', 'stp', 'stp.zip', 'stp.rar'):
            continue
        try:
            path, _ = acquire(source)
            path = unpack(path, source)
            break
        except (OSError, ValueError) as error:
            failures.append(str(error))
    else:
        raise StudioBuildError(part.name, 'model_unavailable', '; '.join(failures))
    translation = api.upload(f'/translations/d/{did}/w/{wid}', path, {
        'flattenAssemblies': True, 'createComposite': True, 'allowFaultyParts': False,
        'storeInDocument': True, 'encodedFilename': path.name})
    info = translation if translation.get('requestState') == 'DONE' else _wait(api, translation['id'], 900, 2)
    elements = info.get('resultElementIds', [])
    if len(elements) != 1:
        candidates = api.json('GET', f'/documents/d/{did}/w/{wid}/elements')
        elements = [e['id'] for e in candidates if e['id'] in elements and e.get('elementType') == 'PARTSTUDIO']
        if len(elements) != 1:
            raise StudioBuildError(part.name, 'translation', f'expected one Part Studio, got {elements}')
    eid = elements[0]
    builder = PartStudioBuilder(api, did, wid, eid, part.studio)
    ids = builder._parts()
    if len(ids) != 1:
        raise StudioBuildError(part.name, 'translation', 'import must produce one rigid composite part; select a rigid vendor assembly')
    pid = next(iter(ids))
    placement = Frame.from_placement(type('Placement', (), entry.placement)()).matrix()
    # Native Transform: the user-editable operation a human applies to an imported model.
    if not np.allclose(placement, np.eye(4)):
        rotation = placement[:3, :3]
        if not np.allclose(rotation, np.eye(3)):
            raise StudioBuildError(part.name, 'model_frame_unverified', 'nontrivial orientation requires a reviewed native Transform')
        delta = placement[:3, 3]
        length = float(np.linalg.norm(delta))
        if length:
            parameters = [fx.qlist('entities', [fx.det(pid)]), fx.enum('transformType', 'TransformType', 'TRANSLATION_3D'),
                          fx.qty('dx', fx.mm(delta[0])), fx.qty('dy', fx.mm(delta[1])), fx.qty('dz', fx.mm(delta[2]))]
            builder._add(fx.feature('transform', 'Align vendor model to component frame', parameters), part.name, 'Reference geometry')
    box = api.json('GET', builder.base + '/boundingboxes')
    bbox = [box['high' + axis] - box['low' + axis] for axis in ('X', 'Y', 'Z')]
    verdict = validate(entry, bbox)
    if verdict.status == 'mismatch':
        raise StudioBuildError(part.name, 'model_datasheet_mismatch', json.dumps(verdict.model_dump()))
    # Geometry-derived interface evidence was checked before any upload. The
    # actual translated bounding box above remains an independent live check.
    builder.out.part_ids[part.key] = pid
    builder.connectors(part)
    builder.properties(part, metadata_property_ids(api, did, wid, eid, pid))
    api.json('POST', f'/elements/d/{did}/w/{wid}/e/{eid}', body={'name': part.studio})
    return builder.out


def insert_public(api: Any, did: str, wid: str, assembly: str, entry: ModelEntry, transform: np.ndarray) -> dict[str, Any]:
    """Insert → Other documents → Public, pinned to a version (no STEP round trip)."""
    source = next(s for s in entry.sources if s.kind == 'onshape_public' and not s.requires_login)
    document = api.json('GET', f'/documents/{source.did}')
    if document.get('isPublic') is not True:
        raise ValueError('bank Onshape document is no longer public')
    base = f'/assemblies/d/{did}/w/{wid}/e/{assembly}'
    before = {i['id'] for i in api.json('GET', base).get('rootAssembly', {}).get('instances', [])}
    body = {'documentId': source.did, 'versionId': source.vid, 'elementId': source.eid,
            'isWholePartStudio': not bool(source.part_id)}
    if source.part_id:
        body['partId'] = source.part_id
    api.json('POST', base + '/instances', body=body)
    new = [i for i in api.json('GET', base).get('rootAssembly', {}).get('instances', []) if i['id'] not in before]
    if not new:
        raise RuntimeError('public version insertion created no instances')
    api.json('POST', base + '/occurrencetransforms', body={'occurrences': [{'path': [i['id']]} for i in new],
                                                          'transform': transform.ravel().tolist(), 'isRelative': False})
    return {'model_bank_id': entry.id, 'source': source.model_dump(), 'instances': [i['id'] for i in new]}


def public_source(part: Any) -> Any:
    """An immutable source is reusable only with existing real-geometry MCs and verified mass."""
    if not part.bank_model:
        return None
    entry = ModelEntry.model_validate(part.bank_model)
    source = next((s for s in entry.sources if s.kind == 'onshape_public' and not s.requires_login and s.part_id), None)
    if source and entry.validation.status == 'passed' and entry.validation.key_features.get('datasheet_mass'):
        from r3_connectors.frames import Frame
        if not entry.placement or not np.allclose(Frame.from_axes(**entry.placement).matrix(), np.eye(4)):
            return None
        for connector in part.connectors:
            frame = _interface_frame(entry, connector)
            if connector.name not in entry.onshape_connectors or not frame:
                return None
            if not np.allclose(Frame.from_axes(**frame).matrix(), connector.frame, atol=entry.validation.tolerance_m):
                return None
        return source
    return None


def public_documents(parts: dict[str, Any]) -> list[Any]:
    from .organized import DocumentInfo, StudioRef
    docs = []
    for key, part in parts.items():
        source = public_source(part)
        if source is None:
            continue
        doc = DocumentInfo(did=source.did, wid='', vid=source.vid, name=part.name)
        doc.studios[part.studio] = StudioRef(part.studio, source.eid, {key: source.part_id},
                                           {key: part.bank_model['onshape_connectors']}, {})
        docs.append(doc)
    return docs
