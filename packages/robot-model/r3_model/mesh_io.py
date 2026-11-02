"""Read native mesh files without rounding coordinates or repairing geometry."""
import json
from pathlib import Path

import numpy as np
import trimesh


class ExportMeshError(ValueError):
    """An unchanged exported body failed the mesh contract."""

    def __init__(self, body: str, mesh: trimesh.Trimesh) -> None:
        counts = np.bincount(mesh.edges_unique_inverse)
        self.details = {
            'body': str(body), 'face_count': int(len(mesh.faces)),
            'finite_vertices': bool(np.isfinite(mesh.vertices).all()),
            'watertight': bool(mesh.is_watertight),
            'winding_consistent': bool(mesh.is_winding_consistent),
            'positive_volume': bool(mesh.volume > 0),
            'boundary_edges': int((counts == 1).sum()),
            'non_manifold_edges': int((counts > 2).sum()),
        }
        reasons = ', '.join(f'{key}={value}' for key, value in self.details.items() if key != 'body')
        super().__init__(f'Exported glTF body {body!r} is not a finite, closed positive-volume solid; {reasons}')


def _native_bodies(pieces: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    """Retain body index ranges for consumers that must not join touching bodies."""
    ranges, vertex, face = [], 0, 0
    for piece in pieces:
        ranges.append((vertex, vertex + len(piece.vertices), face, face + len(piece.faces)))
        vertex += len(piece.vertices)
        face += len(piece.faces)
    mesh = trimesh.util.concatenate(pieces)
    mesh.metadata['native_body_ranges'] = ranges
    return mesh


def load_exported_stl(path: str | Path) -> trimesh.Trimesh:
    """Join only identical STL vertices; leave every coordinate and facet intact.

    STL repeats vertices per triangle. Trimesh's default decimal-rounded merge
    can collapse distinct small features in SI units and manufacture open edges.
    Exact deduplication restores shared indices without welding nearby geometry,
    filling holes, changing winding, or discarding faces. Callers still validate.
    """
    mesh = trimesh.load_mesh(path, file_type="stl", process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError("Exported STL must contain one triangle mesh")
    vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    return trimesh.Trimesh(vertices=vertices, faces=inverse[mesh.faces],
                          face_normals=mesh.face_normals, metadata=mesh.metadata, process=False)


def load_exported_mesh(path: str | Path) -> trimesh.Trimesh:
    """Keep distinct native glTF bodies distinct, even at coincident edges.

    Composite-part STL is triangle soup with no body ownership. Globally joining
    its identical vertices can turn two closed bodies into a non-manifold mesh.
    Native glTF retains that ownership. Join exact coordinates only within each
    exported body, apply its exported node transform, and retain every facet.
    """
    path = Path(path)
    if path.suffix.lower() == '.stl':
        return load_exported_stl(path)
    if path.suffix.lower() == '.obj':
        return load_viewer_obj(path)
    if path.suffix.lower() != '.gltf':
        raise ValueError('Expected an exported STL or self-contained glTF file')
    document = json.loads(path.read_text())
    # Only the pinned file may supply bytes. Never follow external files/URLs
    # from an imported glTF, including texture references used by its loader.
    for section in ('buffers', 'images'):
        for resource in document.get(section, []):
            uri = resource.get('uri')
            if uri is not None and (not isinstance(uri, str) or not uri.startswith('data:')):
                raise ValueError('Exported glTF must embed all buffers and images')
    scene = trimesh.load_scene(path, file_type='gltf', process=False, merge_primitives=True)
    pieces, represented = [], set()
    for node in scene.graph.nodes_geometry:
        transform, key = scene.graph[node]
        body = scene.geometry[key]
        if not isinstance(body, trimesh.Trimesh):
            raise ValueError('Exported glTF body is not a triangle mesh')
        represented.add(key)
        vertices, inverse = np.unique(body.vertices, axis=0, return_inverse=True)
        piece = trimesh.Trimesh(vertices=trimesh.transform_points(vertices, transform),
                                faces=inverse[body.faces], process=False)
        if (not len(piece.faces) or not np.isfinite(piece.vertices).all()
                or not piece.is_watertight or not piece.is_winding_consistent or piece.volume <= 0):
            raise ExportMeshError(node, piece)
        pieces.append(piece)
    if not pieces or represented != set(scene.geometry):
        raise ValueError('Exported glTF contains missing or unreferenced body geometry')
    # concatenate preserves separate vertex indices across bodies. No welding,
    # hole filling, face removal, winding changes or local re-tessellation.
    return _native_bodies(pieces)


def load_viewer_obj(path: str | Path) -> trimesh.Trimesh:
    """Read unchanged native display bodies; report topology without repairing it.

    Only the numeric OBJ subset emitted by Onshape Native is accepted. No
    materials, external files, command directives or texture requests are read.
    Separate native bodies retain separate vertex indices at coincident edges.
    """
    path = Path(path)
    for line in path.read_text().splitlines():
        row = line.strip()
        if row and not row.startswith('#') and row.split()[0] not in {'v', 'f', 'o', 'g'}:
            raise ValueError('Native viewer OBJ must not reference external resources or unsupported directives')
    scene = trimesh.load_scene(path, file_type='obj', process=False, group_material=False,
                               maintain_order=True, skip_materials=True, split_objects=True)
    pieces, qualities = [], []
    for name, body in scene.geometry.items():
        if not isinstance(body, trimesh.Trimesh) or not len(body.faces) or not np.isfinite(body.vertices).all():
            raise ValueError('Native viewer OBJ has no finite triangles')
        vertices, inverse = np.unique(body.vertices, axis=0, return_inverse=True)
        piece = trimesh.Trimesh(vertices=vertices, faces=inverse[body.faces], process=False)
        qualities.append({'body': name, 'watertight': bool(piece.is_watertight),
                          'winding_consistent': bool(piece.is_winding_consistent),
                          'positive_volume': bool(piece.volume > 0)})
        pieces.append(piece)
    if not pieces:
        raise ValueError('Native viewer OBJ contains no mesh bodies')
    mesh = _native_bodies(pieces)
    mesh.metadata.update(geometry_qualification='display_mesh', bodies=qualities)
    return mesh
