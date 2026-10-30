"""A measured camera pattern updates its fixture, cap seats and proxy together."""
import copy

import pytest
import yaml

from r3_catalog import load_catalog
from r3_catalog.patterns import resolve_patterns
from r3_model.project import Project
from r3_onshape.geometry import Hole
from r3_onshape.parts import component_ops


def test_one_source_pattern_updates_fixture_and_cap_seat(tmp_path):
    source = {'id': 'measured-camera', 'kind': 'camera', 'name': 'Camera',
              'mechanical': {'size': ['38 mm', '38 mm', '1.6 mm'], 'mass': .015,
                             'proxy_holes_from': ['mount']},
              'interfaces': {
                  'mount': {'kind': 'hole_pattern', 'z': [0, 0, -1],
                            'params': {'holes': [['15 mm', '15 mm', '2.5 mm', 'clearance', '1.6 mm']],
                                       'pattern_confirmed': False}},
                  'cap': {'kind': 'adhesive_pad', 'params': {'hole_seat': {'interface': 'mount', 'index': 0}}}}}
    fixture = {'id': 'camera-fixture', 'kind': 'structure', 'name': 'Fixture',
               'mechanical': {'size': ['40 mm', '40 mm', '8 mm'], 'mass': .01, 'fabricated': True},
               'interfaces': {'camera': {'kind': 'hole_pattern', 'origin': [0, 0, '8 mm'],
                                        'params': {'holes_from': 'measured-camera.mount', 'hole_kind': 'tapped',
                                                   'hole_diameter': '2.5 mm', 'hole_depth': '7 mm'}}}}
    folder = tmp_path / 'catalog' / 'components'
    folder.mkdir(parents=True)
    path = folder / 'camera.yaml'
    path.write_text(yaml.safe_dump({'components': [source, fixture]}))
    robot = {'schema': 'r3/robot@1', 'robot': {'name': 'Camera fixture'},
             'components': {'fixture': {'type': 'camera-fixture'}, 'camera': {'type': 'measured-camera'}},
             'assembly': {'root': 'fixture', 'joins': [{'id': 'mount', 'a': 'fixture.camera',
                           'b': 'camera.mount', 'clock': '0 deg', 'fasteners': {'type': 'M2.5x8'}}]}}
    (tmp_path / 'robot.yaml').write_text(yaml.safe_dump(robot))
    for xy in [(15, 15), (14, 16)]:
        changed = copy.deepcopy(source)
        changed['interfaces']['mount']['params']['holes'][0][:2] = [f'{v} mm' for v in xy]
        path.write_text(yaml.safe_dump({'components': [changed, fixture]}))
        project = Project.load(tmp_path)
        camera = project.catalog.component('measured-camera')
        mount = project.catalog.component('camera-fixture').interfaces['camera']
        assert mount.params['holes'][0][:2] == [f'{v} mm' for v in xy]
        assert camera.interfaces['cap'].origin == pytest.approx([xy[0]/1000, -xy[1]/1000, .0016])
        ops = component_ops(project, 'measured-camera', {}, None)
        hole = next(op for op in ops if isinstance(op, Hole))
        assert hole.point == pytest.approx([xy[0]/1000, -xy[1]/1000, 0])
        assert any(d.code == 'mount_pattern_estimated' and 'camera' in d.subjects
                   for d in project.assembly.diagnostics)


def test_pattern_reference_cycle_is_rejected():
    catalog = load_catalog()
    camera = catalog.component('arducam-b0445-imx296')
    camera.interfaces['mount'].params['holes_from'] = 'arducam-b0445-imx296.mount'
    with pytest.raises(ValueError, match='reference cycle'):
        resolve_patterns(catalog)
