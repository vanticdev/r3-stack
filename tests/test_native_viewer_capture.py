"""Synthetic loaded-viewer capture: no remote models, geometry authoring or exports."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from r3_model.pulled import PullError
from r3_onshape.viewer_mesh import capture, cached

URL = "https://cad.onshape.com/documents/" + "a" * 24 + "/w/" + "b" * 24 + "/e/" + "c" * 24


class FakeViewer:
    def __init__(self):
        self.calls = []
        self.raw = b"# synthetic opaque OBJ bytes, deliberately unchanged\n"
        self.handle = hashlib.sha256(self.raw).hexdigest() + ".obj"
        self.part = {"name": "Synthetic fixture", "loaded": True, "part_index": 0,
                     "body_id": "body", "full_element_id": "element-source", "occurrence_path": "instance"}
        self.pose = np.eye(4); self.pose[:3, 3] = [.2, -.1, .04]
        self.provenance = {"microversion": "revision", "part": self.part,
            "occurrence_transform": self.pose.ravel(order="F").tolist(), "mesh_artifact": self.handle}
        self.snapshot = {"kind": "viewer-mesh-list", "url": URL, "microversion": "revision",
                         "tab_id": 12, "parts": [self.part]}
        self.stale = False

    def local(self, tool, args):
        assert tool == "viewer_mesh"
        self.calls.append(deepcopy(args))
        if args["action"] == "list":
            return {"snapshot": "a" * 64 + ".json", "microversion": "changed" if self.stale and len(self.calls) > 1 else "revision", "tab_id": 12}
        provenance = json.dumps(self.provenance).encode()
        return {"artifact": self.handle, "provenance": hashlib.sha256(provenance).hexdigest() + ".json",
                "units": "meter", "coordinate_frame": "part-local", "exact_cad": False,
                "body_count": 1, "occurrence_transform": self.pose.ravel(order="F").tolist()}

    def artifact(self, handle):
        return self.snapshot

    def artifact_bytes(self, handle):
        return self.raw if handle.endswith(".obj") else json.dumps(self.provenance).encode()


def project(tmp_path):
    return SimpleNamespace(root=tmp_path, spec=SimpleNamespace(cad=SimpleNamespace(source=URL)))


def test_capture_preserves_native_bytes_pose_and_revision_without_installing_a_pull(tmp_path):
    native = FakeViewer()
    data = capture(project(tmp_path), transport=native)
    assert data["qualification"] == "display_mesh" and not data["exact_cad"]
    assert not data["closed_solid_verified"]
    assert np.array_equal(data["parts"][0]["pose"], native.pose)
    assert data["microversion"] == "revision"
    path = tmp_path / "build/cad/viewer-mesh" / data["parts"][0]["mesh"]
    assert path.read_bytes() == native.raw
    assert cached(project(tmp_path)) == data
    assert [c["action"] for c in native.calls] == ["list", "download", "list"]
    assert not (tmp_path / "cad").exists()


def test_unloaded_parts_stay_visible_as_missing(tmp_path):
    native = FakeViewer()
    native.snapshot["parts"].append({"name": "Unloaded fixture", "part_index": 1, "loaded": False})
    result = capture(project(tmp_path), transport=native)
    assert result["total"] == 2 and len(result["parts"]) == 1
    assert result["unavailable"] == [{"name": "Unloaded fixture", "part_index": 1}]


@pytest.mark.parametrize("change", ["revision", "identity", "pose", "bytes"])
def test_bad_provenance_or_bytes_never_publish_a_preview(tmp_path, change):
    native = FakeViewer()
    if change == "revision": native.provenance["microversion"] = "different"
    if change == "identity": native.provenance["part"] = {**native.part, "body_id": "other"}
    if change == "pose": native.provenance["occurrence_transform"] = np.eye(4).ravel().tolist()
    if change == "bytes": native.raw = b"modified"
    with pytest.raises(PullError):
        capture(project(tmp_path), transport=native)
    assert cached(project(tmp_path)) is None


def test_revision_change_retains_previous_preview(tmp_path):
    native = FakeViewer()
    previous = capture(project(tmp_path), transport=native)
    native.calls.clear(); native.stale = True
    with pytest.raises(PullError) as error:
        capture(project(tmp_path), transport=native)
    assert error.value.code == "cad_viewer_stale"
    assert cached(project(tmp_path)) == previous


def test_cache_cannot_escape_project_or_accept_modified_files(tmp_path):
    native = FakeViewer()
    data = capture(project(tmp_path), transport=native)
    (tmp_path / "build/cad/viewer-mesh" / data["parts"][0]["mesh"]).write_bytes(b"changed")
    with pytest.raises(PullError) as error:
        cached(project(tmp_path))
    assert error.value.code == "cad_viewer_modified"


def test_symlinked_cache_root_is_rejected(tmp_path):
    root = tmp_path / "project"; root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (root / "build").symlink_to(outside)
    with pytest.raises(PullError):
        capture(project(root), transport=FakeViewer())
    assert not list(outside.iterdir())


def test_malformed_cache_is_a_structured_error(tmp_path):
    p = project(tmp_path)
    capture(p, transport=FakeViewer())
    (tmp_path / 'build/cad/viewer-mesh/latest.json').write_text('{broken')
    with pytest.raises(PullError) as error:
        cached(p)
    assert error.value.code == 'cad_viewer_invalid'


def test_project_version_source_resolves_to_its_current_workspace_for_inspection(tmp_path):
    p = project(tmp_path)
    p.spec.cad.source = URL.replace('/w/' + 'b' * 24, '/v/' + 'd' * 24)
    native = FakeViewer()
    reads = []
    def api_request(method, endpoint):
        reads.append((method, endpoint))
        return {'defaultWorkspace': {'id': 'b' * 24}}
    native.api_request = api_request
    result = capture(p, transport=native)
    assert result['url'] == URL
    assert reads == [('GET', '/api/v9/documents/' + 'a' * 24)]


def test_refresh_remembers_the_verified_viewer_tab(tmp_path):
    p = project(tmp_path)
    native = FakeViewer()
    capture(p, tab_id=12, transport=native)
    native.calls.clear()
    capture(p, transport=native)
    assert native.calls[0]['tab_id'] == 12


def test_agent_preview_errors_retain_their_diagnostic(monkeypatch):
    # The public tool should not turn a known source/bridge error into a
    # protocol exception, nor submit an export as an implicit alternative.
    from r3_orchestrator import mcp_server
    import r3_onshape.viewer_mesh as viewer
    def fail(*args):
        raise PullError('cad_viewer_stale', 'CAD changed')
    monkeypatch.setattr(viewer, 'capture', fail)
    monkeypatch.setattr(mcp_server.api, 'load_project', lambda *_: object())
    result = mcp_server.r3_cad_preview(project='synthetic')
    assert result == {'ok': False, 'code': 'cad_viewer_stale', 'message': 'CAD changed'}
