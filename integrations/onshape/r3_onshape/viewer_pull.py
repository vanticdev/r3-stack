"""A version-pinned working model from loaded Onshape meshes and native physics."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import numpy as np

from r3_model.pulled import MESH_SCHEMA, PullError
from .native_mass import properties
from .viewer_mesh import capture, cached


def _element_revision(native: Any, did: str, vid: str, eid: str, root_did: str,
                      cache: dict[tuple[str, str], list[dict]]) -> str:
    key = (did, vid)
    if key not in cache:
        options = {'query': {'linkDocumentId': root_did}} if did != root_did else {}
        values = native.api_request('GET', f'/api/v9/documents/d/{did}/v/{vid}/elements', **options)
        if not isinstance(values, list):
            raise PullError('cad_pull_incomplete', 'Pinned native element metadata is unavailable')
        cache[key] = values
    matches = [value for value in cache[key] if isinstance(value, dict) and value.get('id') == eid]
    if len(matches) != 1 or not isinstance(matches[0].get('microversionId'), str) or not matches[0]['microversionId']:
        raise PullError('cad_pull_incomplete', 'Pinned native element revision is missing or ambiguous',
                        {'document_id': did, 'version_id': vid, 'element_id': eid})
    return matches[0]['microversionId']


def download(project: Any, native: Any, manifest: dict[str, Any], directory: Path,
             tab_id: int | None = None, viewer_url: str = "") -> None:
    """Use only the loaded mesh route; no translation, STEP or export fallback."""
    did, eid = manifest['document_id'], manifest['element_id']
    if not viewer_url:
        source = project.spec.cad.source or ''
        if f'/documents/{did}/w/' in source and source.rstrip('/').endswith('/e/' + eid):
            viewer_url = source
        else:
            doc = native.api_request('GET', f'/api/v9/documents/{did}')
            wid = doc['defaultWorkspace']['id']
            viewer_url = f'https://cad.onshape.com/documents/{did}/w/{wid}/e/{eid}'
    # Validate all native mass records before downloading any geometry.
    mass = {name: properties(part.get('native_mass_properties')) for name, part in manifest['parts'].items()}
    # Display revisions belong to elements, not to the enclosing document.
    # Resolve them through the selected immutable document version. A display
    # element ID cannot be passed to createVersion as a document microversion.
    revisions: dict[tuple[str, str], list[dict]] = {}
    element_revision = _element_revision(native, did, manifest['version_id'], eid, did, revisions)
    sources = {}
    for name, part in manifest['parts'].items():
        ref = part['onshape']
        sources[name] = _element_revision(native, ref['document_id'], ref['version_id'], ref['element_id'], did, revisions)
    preview = capture(project, viewer_url, tab_id, native)
    preview = cached(project)
    if preview is None:
        raise PullError('cad_viewer_missing', 'Viewer snapshot disappeared before the pull could read it')
    if preview['microversion'] != element_revision:
        raise PullError('cad_pull_view_stale', 'Loaded viewer element differs from the pinned native assembly element',
                        {'viewer_element_microversion': preview['microversion'], 'pinned_element_microversion': element_revision,
                         'pinned_document_microversion': manifest.get('microversion'), 'tab_id': preview['tab_id'],
                         'hint': 'Create/select the intended document version normally, then refresh its workspace viewer. R3 checks its pinned element revision. Never use a viewer element revision as a document microversion or edit cached data.'})
    by_path: dict[str, list[dict]] = {}
    for item in preview['parts']:
        by_path.setdefault(item['occurrence_path'], []).append(item)
    root = Path(project.root) / 'build/cad/viewer-mesh'
    (directory / 'parts').mkdir()
    from r3_model.mesh_io import load_viewer_obj
    for name, part in manifest['parts'].items():
        ref = part['onshape']
        matches = by_path.get('/'.join(ref['occurrence_path']), [])
        if len(matches) != 1:
            raise PullError('cad_pull_incomplete', f'{name}: native occurrence is unloaded or ambiguous in the viewer')
        item = matches[0]
        if item['body_id'] != ref['part_id'] or item['full_element_id'] != ref['element_id'] + '-' + sources[name]:
            raise PullError('cad_pull_view_stale', f'{name}: viewer source identity differs from the pinned assembly')
        if not np.allclose(item['pose'], manifest['poses'][name], atol=1e-9, rtol=0):
            raise PullError('cad_pull_view_stale', f'{name}: viewer placement differs from the pinned native solution')
        stem = Path(part['stl']).with_suffix('')
        obj, proof = str(stem) + '.obj', str(stem) + '.viewer.json'
        raw = (root / item['mesh']).read_bytes()
        provenance = (root / item['provenance']).read_bytes()
        if (hashlib.sha256(raw).hexdigest() != item['mesh_sha256'] or
                hashlib.sha256(provenance).hexdigest() != item['provenance_sha256']):
            raise PullError('cad_pull_modified', f'{name}: cached native files changed during the pull')
        (directory / obj).write_bytes(raw)
        (directory / proof).write_bytes(provenance)
        mesh = load_viewer_obj(directory / obj)
        if len(mesh.metadata['bodies']) != item['body_count']:
            raise PullError('cad_pull_geometry_invalid', f'{name}: OBJ body ownership differs from native provenance')
        for kind in ('stl', 'step', 'gltf'):
            part.pop(kind, None)
        part.update(obj=obj, provenance=proof,
                    sha256={'obj': hashlib.sha256(raw).hexdigest(), 'provenance': hashlib.sha256(provenance).hexdigest()},
                    element_microversion=sources[name], geometry_qualification='display_mesh', mesh_quality=mesh.metadata['bodies'], **mass[name])
    manifest.update(schema=MESH_SCHEMA, geometry_qualification='display_mesh',
                    mesh_source='onshape-loaded-viewer', element_microversion=element_revision,
                    qualification_note='Working mesh model. Analytic fits and manufacturing mesh quality require separate qualification.')
