"""A version-pinned working model from loaded Onshape meshes and native physics."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import numpy as np

from r3_model.pulled import MESH_SCHEMA, PullError
from .native_mass import properties
from .viewer_mesh import capture, cached


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
    preview = capture(project, viewer_url, tab_id, native)
    preview = cached(project)
    if preview is None:
        raise PullError('cad_viewer_missing', 'Viewer snapshot disappeared before the pull could read it')
    if preview['microversion'] != manifest.get('microversion'):
        raise PullError('cad_pull_view_stale', 'Loaded viewer differs from the pinned version; create/select a version of the current assembly')
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
        if item['body_id'] != ref['part_id'] or not item['full_element_id'].startswith(ref['element_id'] + '-'):
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
                    geometry_qualification='display_mesh', mesh_quality=mesh.metadata['bodies'], **mass[name])
    manifest.update(schema=MESH_SCHEMA, geometry_qualification='display_mesh',
                    mesh_source='onshape-loaded-viewer',
                    qualification_note='Working mesh model. Analytic fits and manufacturing mesh quality require separate qualification.')
