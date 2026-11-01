"""Synthetic native effective mate frames are part-local, including offsets."""
import numpy as np
import pytest

from r3_model.pulled import PullError
from r3_onshape.pull import describe, target

KEY = 'F_fixture_native_port'


def cs(frame):
    return {'origin': frame[:3, 3].tolist(), 'xAxis': frame[:3, 0].tolist(), 'zAxis': frame[:3, 2].tolist()}


def fixture(nested=False, explicit_id=False):
    parent = np.array([[0., -1., 0., .5], [1., 0., 0., .25], [0., 0., 1., .125], [0., 0., 0., 1.]])
    relative = np.array([[1., 0., 0., .125], [0., 0., -1., .25], [0., 1., 0., .5], [0., 0., 0., 1.]])
    poses = {'alpha': parent, 'beta': parent @ relative}
    effective = {'alpha': np.eye(4), 'beta': np.linalg.inv(relative)}
    prefix = ['sub'] if nested else []
    entities = [{'matedOccurrence': [name], 'matedCS': cs(effective[name]), 'mateConnectorCS': cs(np.eye(4)),
                 **({'mateConnectorId': KEY} if explicit_id else {})} for name in poses]
    feature = {'id': 'native-fixed', 'featureType': 'mate', 'featureData': {
        'mateType': 'FASTENED', 'mateLimits': {}, 'matedEntities': entities}}
    part = {'documentId': 'doc', 'elementId': 'studio', 'partId': 'body',
            'material': {'name': 'Synthetic fixture material', 'density': 1000},
            'mateConnectors': [{'featureId': KEY, 'mateConnectorCS': cs(np.eye(4))}]}
    root = {'documentId': 'doc', 'documentVersion': 'version', 'elementId': 'assembly',
            'instances': [], 'features': [] if nested else [feature]}
    rows = [{'kind': 'occurrence', 'instance_type': 'Part', 'occurrence_path': [*prefix, name], 'name': name,
             'reference': {**part, 'documentVersion': 'version'}, 'transform': pose.ravel().tolist()}
            for name, pose in poses.items()]
    subs = []
    if nested:
        sub = {'documentId': 'doc', 'documentVersion': 'version', 'elementId': 'subassembly',
               'instances': [], 'features': [feature]}
        root['instances'] = [{'id': 'sub', 'type': 'Assembly', 'documentId': 'doc',
                              'documentVersion': 'version', 'elementId': 'subassembly'}]
        rows.append({'kind': 'occurrence', 'instance_type': 'Assembly', 'occurrence_path': ['sub'],
                     'transform': parent.ravel().tolist()})
        subs = [sub]
    snapshot = {'definition': {'parts': [part], 'rootAssembly': root, 'subAssemblies': subs}, 'rows': rows}
    return snapshot, poses, effective, {name: [*prefix, name] for name in poses}


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('explicit_id', [False, True])
def test_effective_local_frames_preserve_native_offsets_and_raw_connector(nested, explicit_id):
    snapshot, poses, effective, selectors = fixture(nested, explicit_id)
    result = describe(snapshot, target('https://cad.onshape.com/documents/doc/v/version/e/assembly'), selectors)
    mate = result['mates'][0]
    world = []
    for name, key in (mate['a'], mate['b']):
        np.testing.assert_array_equal(result['poses'][name], poses[name])
        np.testing.assert_array_equal(result['parts'][name]['mate_connectors'][KEY], np.eye(4))
        np.testing.assert_array_equal(result['parts'][name]['mate_connectors'][key], effective[name])
        world.append(poses[name] @ np.array(result['parts'][name]['mate_connectors'][key]))
    np.testing.assert_array_equal(world[0], world[1])
    np.testing.assert_array_equal(mate['axis'], world[0][:3, 2])
    assert len(result['parts']) == 2


def test_raw_native_connector_disagreement_still_fails():
    snapshot, _, _, selectors = fixture(explicit_id=True)
    entity = snapshot['definition']['rootAssembly']['features'][0]['featureData']['matedEntities'][1]
    entity['mateConnectorCS']['origin'][0] = .0625
    with pytest.raises(PullError) as error:
        describe(snapshot, target('https://cad.onshape.com/documents/doc/v/version/e/assembly'), selectors)
    assert error.value.code == 'cad_pull_connector_mismatch'


def test_unsolved_native_constraint_is_not_repaired_by_normalization():
    snapshot, poses, _, selectors = fixture()
    snapshot['definition']['rootAssembly']['features'][0]['featureData']['matedEntities'][1]['matedCS']['origin'][0] += .0625
    result = describe(snapshot, target('https://cad.onshape.com/documents/doc/v/version/e/assembly'), selectors)
    a, b = result['mates'][0]['a'], result['mates'][0]['b']
    fa = poses[a[0]] @ np.array(result['parts'][a[0]]['mate_connectors'][a[1]])
    fb = poses[b[0]] @ np.array(result['parts'][b[0]]['mate_connectors'][b[1]])
    assert np.linalg.norm(fa[:3, 3] - fb[:3, 3]) == .0625
