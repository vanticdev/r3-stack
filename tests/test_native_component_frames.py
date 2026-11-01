"""Logical electrical ports select native frames, never catalog placements."""
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from r3_connectors.frames import Frame
from r3_connectors.pins import component_connector_frame
from r3_model.project import Project
from r3_model.pulled import PullError
from r3_model.spec import OnshapeSelection
from r3_wiring.router import HarnessRouter, RouterOptions

KEY = 'F_synthetic_native_contact'


def project(tmp_path, key='port', *, bind=True):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from pulled_fixture import write_pull

    folder = tmp_path / 'catalog'
    (folder / 'components').mkdir(parents=True)
    data = {'components': [
        {'id': 'fixture-body', 'name': 'Synthetic contact coupon', 'kind': 'structure',
         'mechanical': {'size': [.01, .01, .01], 'mass': .01}, 'pins': {'S': {'role': 'passive'}},
         'connectors': {'port': {'type': 'fixture-contact', 'position': [.08, .07, .06],
                                'direction': [0, 0, 1], 'x': [1, 0, 0], 'pins': {'1': 'S'}}}},
        {'id': 'fixture-half', 'name': 'Synthetic cable half', 'kind': 'cable',
         'mechanical': {'size': [.01, .01, .01], 'mass': .01}}],
        'connectors': [{'id': 'fixture-contact', 'family': 'Synthetic fixture', 'gender': 'none',
                        'mates_with': ['fixture-contact'], 'pin_count': 1,
                        'current_rating_per_pin': 1, 'voltage_rating': 12, 'contact_resistance': .01,
                        'awg_min': 20, 'awg_max': 26, 'body_size': [.01, .01, .01]}]}
    (folder / 'components/fixture.yaml').write_text(yaml.safe_dump({'components': data['components']}))
    (folder / 'connectors.yaml').write_text(yaml.safe_dump({'connectors': data['connectors']}))
    spec = {'robot': {'name': 'component-native-frame-fixture', 'mobility': 'stationary'},
            'components': {name: {'type': 'fixture-body'} for name in ('alpha', 'beta')},
            'electrical': {'harnesses': [{'id': 'lead', 'connectors': {
                'P': {'type': 'fixture-contact', 'mates': 'alpha.port', 'mate_connector': KEY},
                'Q': {'type': 'fixture-contact', 'mates': 'beta.port', 'mate_connector': KEY}}, 'wires': []}]}}
    if key != 'port' and bind:
        for comp in spec['components'].values():
            comp['onshape'] = {'mate_connectors': {'port': key}}
    spec['components'].update({f'lead.{name}': {'type': 'fixture-half'} for name in ('P', 'Q')})
    (tmp_path / 'robot.yaml').write_text(yaml.safe_dump(spec))
    poses, connectors, expected = {}, {}, {}
    for i, (name, half) in enumerate((('alpha', 'P'), ('beta', 'Q'))):
        pose = Frame.from_axes([.2 + i * .1, .3, .4], [0, 1, 0], [0, 0, 1]).matrix()
        local = Frame.from_axes([.001 + i * .002, .002, .003], [0, 1, 0], [1, 0, 0]).matrix()
        poses[name], connectors[name] = pose, {key: local}
        expected[name] = pose @ local
        poses[f'lead.{half}'] = Frame.from_matrix(expected[name]).mated().matrix()
        connectors[f'lead.{half}'] = {KEY: np.eye(4)}
    shapes = {name: BRepPrimAPI_MakeBox(10, 10, 10).Shape() for name in poses}
    write_pull(tmp_path, shapes, poses=poses, connectors=connectors)
    return Project.load(tmp_path), expected


def test_named_native_frame_overrides_catalog_position_and_clocking(tmp_path):
    value, expected = project(tmp_path)
    for name in ('alpha', 'beta'):
        frame = component_connector_frame(value, f'{name}.port')
        np.testing.assert_allclose(frame.matrix(), expected[name])
        endpoint = value.graph.endpoints[f'{name}.port']
        np.testing.assert_allclose(endpoint.position, expected[name][:3, 3])
        np.testing.assert_allclose(endpoint.direction, expected[name][:3, 2])
        site = value.graph.connection_sites(f'{name}.port')[0]
        position, rotation = value.graph.component_pose(name)
        np.testing.assert_allclose(position + rotation @ site.frame.origin, expected[name][:3, 3])


def test_native_id_selectors_reach_router_without_moving_hardware(tmp_path):
    value, expected = project(tmp_path, KEY)
    router = HarnessRouter.__new__(HarnessRouter)
    router.project, router.spec, router.catalog, router.graph = value, value.spec, value.catalog, value.graph
    router.kin, router.diagnostics = SimpleNamespace(body_of=lambda _: 'root'), []
    router.opt = RouterOptions()
    # This test isolates port/hardware frames; lead clearance has separate
    # native-mesh routing regressions and is unchanged by this resolver.
    router._lead = lambda *args, **kwargs: None
    nodes = router._resolve_nodes()['lead']
    assert not router.diagnostics
    for half, name in (('P', 'alpha'), ('Q', 'beta')):
        np.testing.assert_allclose(component_connector_frame(value, name + '.port').matrix(), expected[name])
        np.testing.assert_allclose(nodes[half].face, expected[name][:3, 3])
        np.testing.assert_allclose(nodes[half].frame.matrix(), Frame.from_matrix(expected[name]).mated().matrix())


def test_explicit_missing_reference_never_falls_back_to_catalog_or_name(tmp_path):
    value, _ = project(tmp_path)
    value.spec.components['alpha'].onshape.mate_connectors['port'] = 'F_missing'
    value.__dict__.pop('_graph', None)
    with pytest.raises(PullError) as error:
        component_connector_frame(value, 'alpha.port')
    assert error.value.code == 'cad_native_connector_missing'
    assert error.value.details['mate_connector'] == 'F_missing'
    assert any(d.code == 'cad_native_connector_missing' for d in value.graph.diagnostics)
    assert 'alpha.port' not in value.graph.endpoints
    assert value.graph.connected('C:alpha.S', 'H:lead.P.1')
    assert 'lead.P' not in value.graph.mating_sites


def test_selector_schema_accepts_only_nonempty_native_references():
    assert OnshapeSelection(mate_connectors={'port': KEY}).mate_connectors == {'port': KEY}
    for refs in ({'port': np.eye(4).tolist()}, {'port': ''}, {'': KEY}):
        with pytest.raises(ValueError):
            OnshapeSelection(mate_connectors=refs)


def test_unnamed_native_ports_require_a_reference(tmp_path):
    value, _ = project(tmp_path, KEY, bind=False)
    with pytest.raises(PullError) as error:
        component_connector_frame(value, 'alpha.port')
    assert error.value.code == 'cad_native_connector_missing'
    assert error.value.details['available_connectors'] == [KEY]


def test_native_pose_mirror_cannot_move_component_ports(tmp_path):
    value, expected = project(tmp_path)
    value.spec.components['alpha'].pose.xyz = [9, 8, 7]
    value.__dict__.pop('_graph', None)
    np.testing.assert_array_equal(component_connector_frame(value, 'alpha.port').matrix(), expected['alpha'])


def test_missing_pull_never_reverts_to_catalog_frames(tmp_path):
    value, _ = project(tmp_path)
    path = tmp_path / 'cad/pulled/pull.json'
    path.unlink()
    for name in ('_assembly', '_graph', '_pulled'):
        value.__dict__.pop(name, None)
    with pytest.raises(PullError) as error:
        component_connector_frame(value, 'alpha.port')
    assert error.value.code == 'cad_pull_missing'


def test_connector_stage_reports_missing_native_frame_without_crashing(tmp_path):
    from r3_connectors.stage import run
    value, _ = project(tmp_path, KEY, bind=False)
    result = run(value, render=False)
    assert not result.ok
    assert any(d.code == 'cad_native_connector_missing' for d in result.diagnostics)
    assert (tmp_path / 'build/connectors/result.json').is_file()
