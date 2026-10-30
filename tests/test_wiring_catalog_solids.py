"""Fresh-build routing preserves holes in authored fabricated supports."""
import numpy as np
import pytest
import yaml

from r3_model.project import Project
from r3_wiring.obstacles import build_obstacles
from r3_wiring.geometry import MeshShape


def test_catalog_support_preserves_opening_and_pose_without_cad_export(tmp_path):
    folder = tmp_path / 'catalog' / 'components'
    folder.mkdir(parents=True)
    catalog = {'components': [{
        'id': 'instrument-bridge', 'kind': 'structure', 'name': 'Instrument bridge',
        'mechanical': {'shape': 'custom', 'size': ['60 mm', '20 mm', '40 mm'],
                       'mass': .02, 'fabricated': True, 'density': 1200,
                       'solids': [
                           {'kind': 'box', 'size': ['60 mm', '20 mm', '40 mm'],
                            'position': [0, 0, '20 mm']},
                           {'kind': 'box', 'op': 'remove', 'size': ['40 mm', '30 mm', '30 mm'],
                            'position': [0, 0, '15 mm']}]}}]}
    (folder / 'bridge.yaml').write_text(yaml.safe_dump(catalog))
    spec = {'schema': 'r3/robot@1', 'robot': {'name': 'Optical fixture'},
            'components': {'bridge': {'type': 'instrument-bridge',
                           'pose': {'xyz': ['100 mm', '200 mm', '10 mm'],
                                    'rpy': [0, 0, '90 deg']}}}}
    (tmp_path / 'robot.yaml').write_text(yaml.safe_dump(spec))
    project = Project.load(tmp_path)
    obstacles, diagnostics = build_obstacles(project)
    shape = obstacles.by_component('bridge').shape
    assert isinstance(shape, MeshShape)
    # Local (0,0,15) is in the opening, (25,0,15) in a leg, and
    # (0,0,35) in the bridge. The 90-degree placement is respected.
    distances = shape.sdf(np.array([[.1, .2, .025], [.1, .225, .025], [.1, .2, .045]]))
    assert distances == pytest.approx([.015, -.005, -.005], abs=2e-5)
    assert any(d.code == 'cad_geometry_estimated' for d in diagnostics)
