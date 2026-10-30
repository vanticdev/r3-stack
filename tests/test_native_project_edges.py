"""Generic native part identities and passive projects retain a valid path."""
from types import SimpleNamespace
import numpy as np
import pytest
from r3_model.spec import RobotSpec, Environment
from r3_model.pulled import PullError
from r3_sim.radio_scene import scene_for_project


def test_explicit_native_fastener_is_one_part_identity():
    spec = RobotSpec.model_validate({'robot': {'name': 'generic fixture'},
        'components': {'plate': {'type': 'generic'}, 'fastener:S': {'type': 'generic-hardware'}},
        'mechanical': {'fasteners': [{'id': 'S', 'type': 'generic', 'position': [0,0,0], 'length': .01, 'clamps': ['plate']}]}})
    assert spec.part_ids() == {'plate', 'fastener:S'}


def test_passive_project_needs_no_antenna_feed(monkeypatch):
    from r3_sim import radio_scene
    monkeypatch.setattr(radio_scene, 'antenna_component', lambda project: None)
    project = SimpleNamespace(spec=SimpleNamespace(simulation=SimpleNamespace(environment=Environment())))
    scene = scene_for_project(project, None, {})
    np.testing.assert_array_equal(scene.antenna_offset, np.zeros(3))
    assert scene.antenna_offset_source == 'not_applicable'


def test_declared_antenna_still_requires_native_feed(monkeypatch):
    from r3_sim import radio_scene
    monkeypatch.setattr(radio_scene, 'antenna_component', lambda project: 'radiator')
    monkeypatch.setattr('r3_model.spec_files.validate_pulled_spec', lambda root:
        SimpleNamespace(parts={'radiator': SimpleNamespace(mate_connectors={})}))
    project = SimpleNamespace(root=None, spec=SimpleNamespace(simulation=SimpleNamespace(environment=Environment())))
    with pytest.raises(PullError) as error:
        scene_for_project(project, None, {})
    assert error.value.code == 'cad_antenna_feed_missing'
