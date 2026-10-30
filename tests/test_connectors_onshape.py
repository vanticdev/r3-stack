"""Onshape import of vendor/KiCad models (standard Import -> Insert -> Transform), against a fake transport."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from r3_connectors.kicad import kicad_available
from r3_connectors.stage import run
from r3_model.project import Project
from r3_onshape.client import Transport, multipart_body
from r3_onshape.imports import import_models, plan_imports

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bench"
pytestmark = pytest.mark.skipif(not kicad_available(), reason="KiCad 3D library not installed")


class FakeOnshape(Transport):
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.instances: list[dict] = []
        self.translations = 0

    def upload(self, path, file_path, fields, timeout=600.0):
        body, content_type = multipart_body(file_path, fields)
        assert content_type.startswith("multipart/form-data; boundary=") and b'name="file"' in body
        self.calls.append(("UPLOAD", path, dict(fields)))
        self.translations += 1
        return {"id": f"t{self.translations}", "requestState": "ACTIVE"}

    def request(self, method, path, query=None, body=None, accept="application/json", timeout=120.0):
        self.calls.append((method, path, body))
        if path.startswith("/translations/"):
            n = path.rsplit("t", 1)[1]
            return json.dumps({"requestState": "DONE", "resultElementIds": [f"ps{n}"]}).encode()
        if path.endswith("/instances"):
            self.instances.append({"id": f"i{len(self.instances)}", "elementId": body["elementId"]})
            return b"{}"
        if method == "GET" and "/assemblies/" in path:
            return json.dumps({"rootAssembly": {"instances": list(self.instances)}}).encode()
        return b"{}"


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    shutil.copytree(BENCH, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns("build", "__pycache__", "_vendor"))
    project = Project.load(root)
    run(project, render=False)
    return project


def test_plan_places_models_with_rigid_transforms(project):
    items, skipped = plan_imports(project)
    names = {i.name.split(" ")[0] for i in items}
    skipped_names = {s["name"] for s in skipped}
    # The Pi comes from its official STEP when downloadable; the HAT's board.step needs the KiCad stage
    # (not run in this copy); harness-to-harness XT60 halves need the router's pair placement (not run).
    assert "pi" in names or "pi" in skipped_names
    assert "controller.J_BATT" in skipped_names
    for item in items:
        r = item.transform[:3, :3]
        assert np.allclose(r.T @ r, np.eye(3), atol=1e-9) and np.linalg.det(r) == pytest.approx(1.0)
        assert item.step.suffix.lower() in (".step", ".stp")
    assert all(s["reason"] for s in skipped)
    assert any("proxy" in s["reason"] for s in skipped)


def test_import_uses_standard_import_insert_transform(project):
    items, _ = plan_imports(project)
    if not items:
        pytest.skip("no importable model (official Pi STEP not downloadable offline)")
    api = FakeOnshape()
    report = import_models(api, project, "d1", "w1", "a1")
    assert len(report.imported) == len(items) > 0
    uploads = [c for c in api.calls if c[0] == "UPLOAD"]
    assert len(uploads) == len(items) and all(c[1] == "/translations/d/d1/w/w1" and c[2]["formatName"] == "STEP" for c in uploads)
    transforms = [c for c in api.calls if c[1].endswith("/occurrencetransforms")]
    assert len(transforms) == len(items)
    for (_, _, body), item in zip(transforms, items):
        assert body["isRelative"] is False and np.allclose(body["transform"], item.transform.ravel())
    # Direct-CAD policy: nothing touches Feature Studios or FeatureScript.
    assert not any("featurescript" in c[1] or "featurestudio" in c[1] for c in api.calls)
