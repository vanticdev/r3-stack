"""Serialized terminations retain the connector's exact rotated pin plane."""
import json
from types import SimpleNamespace

import numpy as np

from r3_connectors.frames import Frame
from r3_connectors.pins import PinEnd
from r3_wiring.export import routes_json


def test_rotated_pigtail_plane_survives_json_round_trip():
    angle = np.radians(10)
    frame = Frame.from_axes([.207215, .021830, .040],
                            [-np.sin(angle), np.cos(angle), 0],
                            [np.cos(angle), np.sin(angle), 0])
    root = frame.point([.002, 0, 0])
    end = PinEnd('battery.A', '1', root, frame.z, root, 'pigtail')
    path = np.vstack([root, root + .01 * frame.z, root + .02 * frame.z])
    wire = SimpleNamespace(wire_id='W', harness='battery', awg=12, path=path,
                           routed_length=.02, cut_length=.03,
                           bundle_radius=np.full(3, .002), ends=(end, None), fanout=(1, 0))
    result = SimpleNamespace(wires={'W': wire}, harnesses={})
    data = json.loads(json.dumps(routes_json(result, [])))['wires']['W']
    local = frame.inverse().point(data['path'][0])
    assert abs(local[1]) < 1e-12 and abs(local[2]) < 1e-12
    assert data['path'][0] == data['ends'][0]['position']
    assert np.linalg.norm(data['ends'][0]['direction']) == 1.0
