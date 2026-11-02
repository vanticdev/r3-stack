"""The real project API exposes native inspection separately from qualified CAD."""
from starlette.testclient import TestClient

from r3_orchestrator.api.server import create_app
from r3_orchestrator.api.agents import AgentManager
from test_cad_pulled_consumers import project
from test_native_viewer_capture import FakeViewer, URL


def test_preview_endpoint_serves_exact_obj_without_qualifying_a_pull(project, monkeypatch):
    import r3_onshape.viewer_mesh as viewer
    native = FakeViewer()
    monkeypatch.setattr(viewer, 'NativeCadTransport', lambda: native)
    manager = AgentManager(executables={'codex': '/missing/codex', 'claude': '/missing/claude'})
    with TestClient(create_app([project.root], agents=manager, doctor=lambda: {})) as client:
        pid = project.root.name
        before = client.get(f'/api/projects/{pid}/cad').json()
        assert before['preview'] is None and not before['authority']['ready']
        response = client.post(f'/api/projects/{pid}/cad/preview', json={'url': URL, 'tab_id': 12})
        assert response.status_code == 200, response.text
        result = response.json()
        assert not result['authority']['ready'] and result['cad_model'] is None
        assert result['preview']['qualification'] == 'display_mesh'
        mesh = client.get(result['preview']['parts'][0]['mesh_url'])
        assert mesh.status_code == 200 and mesh.content == native.raw
        assert mesh.headers['content-type'].startswith('model/obj')
        assert not (project.root / 'cad/pulled').exists()
        assert '/api/projects/{pid}/cad/preview' in client.get('/api/openapi.json').json()['paths']


def test_preview_request_does_not_accept_unrelated_options_or_hide_tampering(project, monkeypatch):
    import r3_onshape.viewer_mesh as viewer
    monkeypatch.setattr(viewer, 'NativeCadTransport', FakeViewer)
    manager = AgentManager(executables={'codex': '/missing/codex', 'claude': '/missing/claude'})
    with TestClient(create_app([project.root], agents=manager, doctor=lambda: {})) as client:
        path = f'/api/projects/{project.root.name}/cad/preview'
        assert client.post(path, json={'url': URL, 'confirm': True}).status_code == 422
        result = client.post(path, json={'url': URL}).json()
        import json
        index = project.root / 'build/cad/viewer-mesh/latest.json'
        record = json.loads(index.read_text())
        (index.parent / record['parts'][0]['mesh']).write_bytes(b'modified')
        cad = client.get(f'/api/projects/{project.root.name}/cad').json()
        assert cad['preview'] is None and cad['preview_error']
        assert not cad['authority']['ready']
