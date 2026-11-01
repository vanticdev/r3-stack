"""Native frame IDs drive cable placement; no convention names or authored poses."""
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from r3_connectors.connection_points import connection_points
from r3_connectors.frames import Frame
from r3_connectors.pins import native_mating_frame
from r3_connectors.stage import ConnectorStage
from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_model.pulled import PullError, load_pulled
from r3_model.spec import HarnessConnector, Splice
from r3_wiring.router import HarnessRouter

KEY = 'F_synthetic_native_port'


@pytest.fixture
def project(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from pulled_fixture import write_pull

    root = tmp_path
    catalog = root / 'catalog/components'
    catalog.mkdir(parents=True)
    entries = {
        'components': [{'id': 'fixture-body', 'name': 'Synthetic contact coupon', 'kind': 'cable',
                        'mechanical': {'size': [.003, .004, .005], 'mass': .02}}],
        'connectors': [{'id': 'fixture-contact', 'family': 'Synthetic fixture', 'gender': 'none',
                        'mates_with': ['fixture-contact'], 'pin_count': 1,
                        'current_rating_per_pin': 1, 'voltage_rating': 12, 'contact_resistance': .01,
                        'awg_min': 20, 'awg_max': 26, 'body_size': [.003, .004, .005]}],
    }
    (catalog / 'fixture.yaml').write_text(yaml.safe_dump({'components': entries['components']}))
    (root / 'catalog/connectors.yaml').write_text(yaml.safe_dump({'connectors': entries['connectors']}))
    data = {'robot': {'name': 'native-frame-fixture', 'mobility': 'stationary'},
            'components': {f'lead.{name}': {'type': 'fixture-body'} for name in ('P', 'Q', 'S')},
            'electrical': {'harnesses': [{'id': 'lead', 'connectors': {
                'P': {'type': 'fixture-contact', 'mates': 'lead.Q', 'mate_connector': KEY},
                'Q': {'type': 'fixture-contact', 'mates': 'lead.P', 'mate_connector': KEY}},
                'splices': {'S': {'position': [.8, .7, .6], 'mate_connector': KEY}}, 'wires': []}]}}
    (root / 'robot.yaml').write_text(yaml.safe_dump(data))
    local = Frame.from_axes([.001, .002, .003], [0, 1, 0], [1, 0, 0]).matrix()
    pose = Frame.from_axes([.2, .3, .4], [0, 1, 0], [0, 0, 1]).matrix()
    poses = {'lead.P': pose, 'lead.Q': pose.copy(), 'lead.S': pose.copy()}
    # This fixture checks frame selection, not contact-fit qualification.
    shapes = {name: BRepPrimAPI_MakeBox(3, 4, 5).Shape() for name in poses}
    write_pull(root, shapes, poses=poses, connectors={name: {KEY: local} for name in poses})
    return Project.load(root), pose @ local


def router(project):
    value = HarnessRouter.__new__(HarnessRouter)
    value.project, value.spec, value.catalog, value.graph = project, project.spec, project.catalog, project.graph
    value.kin = SimpleNamespace(body_of=lambda ref: 'root')
    value.diagnostics = []
    return value


def test_schema_accepts_native_keys_but_not_local_frame_matrices():
    assert HarnessConnector(type='fixture-contact', mate_connector=KEY).mate_connector == KEY
    assert Splice(position=[0, 0, 0], mate_connector=KEY).mate_connector == KEY
    with pytest.raises(ValueError):
        HarnessConnector(type='fixture-contact', mate_connector=np.eye(4).tolist())
    with pytest.raises(ValueError):
        HarnessConnector(type='integral', mate_connector=KEY)


def test_native_ids_reach_router_connector_checks_and_inventory(project):
    project, expected = project
    routed = router(project)
    nodes = routed._resolve_nodes()['lead']
    assert not routed.diagnostics
    np.testing.assert_allclose(nodes['P'].frame.matrix(), expected)
    np.testing.assert_allclose(nodes['S'].frame.matrix(), expected)
    assert nodes['P'].native and nodes['S'].native
    # The authored splice hint cannot move an installed native sleeve.
    np.testing.assert_allclose(nodes['S'].exit, expected[:3, 3])

    result = StageResult(stage='connectors')
    stage = ConnectorStage(project, result)
    conn = project.spec.electrical.harnesses[0].connectors['P']
    frame, source = stage._harness_frame('lead', 'P', conn)
    assert source == 'pulled' and not [d for d in result.diagnostics if d.severity == 'error']
    np.testing.assert_allclose(frame.matrix(), expected)

    point = next(p for p in connection_points(project)['connection_points'] if p['component'] is None)
    np.testing.assert_allclose(point['origin'], expected[:3, 3])
    np.testing.assert_allclose(point['exit_direction'], expected[:3, 2])


def test_explicit_missing_key_never_falls_back_to_a_named_frame():
    selected = np.eye(4)
    selected[:3, 3] = [.001, .002, .003]
    part = SimpleNamespace(name='fixture', mate_connectors={'origin': np.eye(4), KEY: selected})
    np.testing.assert_array_equal(native_mating_frame(part, KEY), selected)
    with pytest.raises(PullError) as error:
        native_mating_frame(part, 'F_missing')
    assert error.value.code == 'cad_pull_invalid'
    assert error.value.details['mate_connector'] == 'F_missing'
    assert KEY in error.value.details['available_connectors']
    np.testing.assert_array_equal(native_mating_frame(part), np.eye(4))


def test_unnamed_ids_require_explicit_selection_and_remain_strict(project):
    project, _ = project
    model = load_pulled(project.root)
    part = model.parts['lead.P']
    with pytest.raises(PullError, match='mate_connector'):
        native_mating_frame(part)
    conn = project.spec.electrical.harnesses[0].connectors['P']
    conn.mate_connector = 'F_missing'
    with pytest.raises(PullError):
        router(project)._resolve_nodes()
    result = StageResult(stage='connectors')
    frame, source = ConnectorStage(project, result)._harness_frame('lead', 'P', conn)
    assert frame is None and source == 'unplaced'
    assert any(d.code == 'cad_pull_invalid' for d in result.diagnostics)
    with pytest.raises(PullError):
        connection_points(project)
