"""Quota stops, offline planning and acknowledged-prefix resume without live Onshape."""
from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from r3_model.diagnostics import StageResult
from r3_onshape.client import OnshapeError, Transport, _HttpTransport
from r3_onshape.journal import RequestJournal
from r3_onshape.quota import quota_diagnostic, update_features


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_http_quota_never_retries(method, monkeypatch):
    class API(_HttpTransport):
        base = "https://example.invalid"
        calls = 0
        def _open(self, *args):
            self.calls += 1
            raise urllib.error.HTTPError("url", 429, "quota", {"Retry-After": "120"}, io.BytesIO(b"spent"))
    monkeypatch.setattr("r3_onshape.client.time.sleep", lambda _: pytest.fail("quota retry"))
    api = API()
    before = datetime.now(timezone.utc).timestamp()
    with pytest.raises(OnshapeError) as raised:
        api.request(method, "/partstudios/d/D/w/W/e/E/features")
    assert api.calls == 1 and raised.value.retry_after == "120"
    assert before + 119 <= datetime.fromisoformat(raised.value.reset_at).timestamp() <= before + 122
    stage = StageResult(stage="cad")
    quota_diagnostic(raised.value, stage)
    assert stage.diagnostics[0].code == "onshape_quota_blocked"
    assert stage.diagnostics[0].data["reset_at"] == raised.value.reset_at


def test_retry_after_http_date():
    error = OnshapeError(429, "quota", {"Retry-After": "Thu, 29 Oct 2026 21:00:00 GMT"})
    assert error.reset_at == "2026-10-29T21:00:00+00:00"
    assert OnshapeError(429, "quota").reset_at is None


def test_session_quota_never_retries(tmp_path, monkeypatch):
    from r3_onshape.session import QuotaExhausted, SessionTransport
    api = SessionTransport("https://example.invalid", tmp_path / "session.json")
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise urllib.error.HTTPError("url", 429, "quota", {"Retry-After": "1"}, io.BytesIO(b"spent"))
    monkeypatch.setattr(api._opener, "open", fail)
    monkeypatch.setattr("r3_onshape.session.time.sleep", lambda _: pytest.fail("quota retry"))
    with pytest.raises(QuotaExhausted) as raised:
        api.request("GET", "/partstudios/d/D/w/W/e/E/features")
    assert len(calls) == 1 and raised.value.reset_at


class FakeAPI(Transport):
    name = "fake"
    def __init__(self, fail_feature: int | None = None):
        self.calls = []
        self.feature_posts = 0
        self.fail_feature = fail_feature
        self.parts = {}

    def request(self, method, path, query=None, body=None, accept="application/json", timeout=120.0):
        self.calls.append((method, path, body))
        out = {}
        if method == "POST" and path == "/documents":
            out = {"id": "D", "defaultWorkspace": {"id": "W"}}
        elif path.endswith("/elements"):
            out = []
        elif method == "POST" and path == "/partstudios/d/D/w/W":
            out = {"id": "E"}
        elif method == "POST" and path.endswith("/features"):
            self.feature_posts += 1
            if self.feature_posts == self.fail_feature:
                raise OnshapeError(429, "spent", {"Retry-After": "90"})
            feature = body["feature"]
            if feature["featureType"] == "extrude":
                self.parts[f"P{len(self.parts)}"] = feature["name"]
            out = {"feature": {"featureId": f"F{self.feature_posts}"}, "featureState": {"featureStatus": "OK"}}
        elif method == "GET" and path.startswith("/parts/"):
            out = [{"partId": key, "name": name} for key, name in self.parts.items()]
        elif method == "GET" and path.startswith("/metadata/"):
            out = {"properties": []}
        elif path.endswith("/versions"):
            out = {"id": "V"}
        return json.dumps(out).encode()


def _part():
    from r3_onshape.geometry import Loop, PartPlan, Plane, Prism
    from r3_onshape.parts import ConnectorSpec, LocalPart
    plan = PartPlan(name="Block", kind="purchased", material="Aluminum", density=2700., mass=.01,
                    ops=[Prism(Plane.through([0, 0, 0], [0, 0, 1]),
                               [Loop.rectangle([0, 0], [.01, .01])], .01, 1, "new", "block")])
    return LocalPart("block", "Block", "Block", "block", plan, library=True,
                     connectors=[ConnectorSpec("mc_mount", np.eye(4), "mount")])


class FakeProject:
    def __init__(self, root):
        self.root, self.lock = root, {}
        self.spec = SimpleNamespace(cad=SimpleNamespace(library_name="Library", folder=None), components={})
    def build_dir(self, *suffix):
        path = self.root / "build"
        for item in suffix:
            path /= item
        path.mkdir(parents=True, exist_ok=True)
        return path
    def write_lock(self, key, value):
        self.lock[key] = value


def test_library_quota_resume_does_not_repeat_finished_features(tmp_path):
    from r3_onshape.organized import ensure_library
    project, api, part = FakeProject(tmp_path), FakeAPI(fail_feature=3), _part()
    with pytest.raises(OnshapeError):
        ensure_library(api, project, {part.key: part}, StageResult(stage="cad"))
    assert api.feature_posts == 3  # sketch/extrude done; connector rejected
    assert not any(method == "DELETE" for method, _, _ in api.calls)
    assert project.lock["cad_library_build"]["document_id"] == "D"
    api.fail_feature = None
    doc = ensure_library(api, project, {part.key: part}, StageResult(stage="cad"))
    assert api.feature_posts == 4  # only the rejected connector was sent again
    assert sum(path == "/documents" for _, path, _ in api.calls) == 1
    assert sum(method == "POST" and path == "/partstudios/d/D/w/W" for method, path, _ in api.calls) == 1
    assert doc.studios["Block"].part_ids == {"block": "P0"}
    assert project.lock["cad_library_build"] == {}
    assert project.lock["cad_library"]["version_id"] == "V"
    # Once pinned, a further run only checks the document and reuses it.
    before = len(api.calls)
    ensure_library(api, project, {part.key: part}, StageResult(stage="cad"))
    assert api.calls[before:][0][:2] == ("GET", "/documents/D")
    assert api.feature_posts == 4


def test_journal_refuses_changed_inputs_and_diverging_calls(tmp_path):
    path = tmp_path / "journal.json"
    api = FakeAPI()
    RequestJournal(api, path, "one").json("POST", "/documents", body={"name": "one"})
    with pytest.raises(ValueError, match="inputs changed"):
        RequestJournal(api, path, "two")
    with pytest.raises(ValueError, match="diverged"):
        RequestJournal(api, path, "one").json("POST", "/documents", body={"name": "two"})
    assert len(api.calls) == 1


def test_existing_feature_updates_are_batched():
    api = FakeAPI()
    features = [{"btType": "BTMFeature-134", "featureId": "F1", "parameters": [], "suppressed": False},
                {"btType": "BTMFeature-134", "featureId": "F2", "suppressed": True}]
    update_features(api, "/partstudios/d/D/w/W/e/E", features)
    assert len(api.calls) == 1
    assert api.calls[0][1].endswith("/features/updates")
    assert api.calls[0][2]["features"] == features
    with pytest.raises(ValueError, match="suppression batches"):
        update_features(api, "base", [{"featureId": "F1"}, {"featureId": "F2", "suppressed": True}])
    with pytest.raises(ValueError, match="existing feature"):
        update_features(api, "base", [{"featureType": "extrude"}])


def test_planner_uses_same_features_as_real_builder():
    from r3_onshape.dry_run import studio_plan
    from r3_onshape.studio import build_studio
    api, part = FakeAPI(), _part()
    built = build_studio(api, "D", "W", "E", "Block", [part])
    plan = studio_plan("Block", [part])
    writes = [body["feature"] for method, path, body in api.calls if method == "POST" and path.endswith("/features")]
    assert [(f["name"], f["featureType"]) for f in writes] == [(f["feature"], f["type"]) for f in plan["features"]]
    assert plan["estimated_features_calls"] == built.features == len(writes)


def test_organization_stops_immediately_and_resumes_checkpoint(tmp_path, monkeypatch):
    from r3_onshape.organized import AssemblyPlan, DocumentInfo, StudioRef, TOP_LEVEL, organize
    calls = []
    class Bridge:
        blocked = True
        def feature_folder(self, *args):
            calls.append(args[-2])
            if args[-2] == "Mate connectors" and self.blocked:
                raise OnshapeError(429, "spent", {"Retry-After": "60"})
            return {"ok": True}
        def hide_partstudio_features(self, *args):
            calls.append("hide"); return {}
        def tab_folders(self, *args): return []
        def close_tabs(self): calls.append("close")
    robot = DocumentInfo("D", "W", "Robot", studios={"Block": StudioRef("Block", "E", {}, {},
                         {"Base shape": ["F1"], "Mate connectors": ["F2"]})}, assemblies={TOP_LEVEL: "A"})
    bridge = Bridge()
    monkeypatch.setattr("r3_onshape.organized._hide_assembly_connectors", lambda *args: {})
    monkeypatch.setattr("r3_onshape.organized._assembly_folders", lambda *args: [])
    progress = tmp_path / "org.json"
    args = (bridge, robot, DocumentInfo("L", "LW", "Library"), AssemblyPlan({}, [], [TOP_LEVEL]),
            StageResult(stage="cad"), False, None, progress)
    with pytest.raises(OnshapeError): organize(*args)
    assert calls == ["Base shape", "Mate connectors"]
    bridge.blocked = False
    organize(*args)
    assert calls.count("Base shape") == 1
    assert calls.count("Mate connectors") == 2
    assert "hide" in calls


def test_dry_run_entrypoints_never_discover_a_transport(tmp_path, monkeypatch, capsys):
    from r3_onshape import dry_run, organized, negative_organized, stage
    part, project = _part(), FakeProject(tmp_path)
    project.fingerprint = lambda: {}
    project.read_build_json = lambda _: None
    project.lock["cad_library"] = {"document_id": "LIB", "workspace_id": "LW", "version_id": "LV", "studios": {}}
    saved = {"robot": {"did": "D", "wid": "W", "name": "Robot", "studios": {}, "variable_studio": "V",
                       "assemblies": {organized.TOP_LEVEL: "A"}},
             "instance_ids": {}, "sub_instances": {}, "mate_ids": {}}
    (project.build_dir("cad") / "build_state.json").write_text(json.dumps(saved))
    part.name = "Servo post"
    monkeypatch.setattr(dry_run, "build_part_catalog", lambda _: ({part.key: part}, {}))
    monkeypatch.setattr(organized, "plan_assembly", lambda *args: organized.AssemblyPlan({}, [], [organized.TOP_LEVEL]))
    monkeypatch.setattr(organized, "robot_variables", lambda _: ({}, {}))
    monkeypatch.setattr("r3_model.project.Project.load", lambda _: project)
    def forbidden(*args, **kwargs): pytest.fail("dry run contacted/discovered Onshape")
    monkeypatch.setattr("r3_onshape.client.default_transport", forbidden)
    monkeypatch.setattr(stage, "default_transport", forbidden)
    monkeypatch.setattr("r3_onshape.bridge.connect", forbidden)
    assert organized.main(["rebuild", "ignored", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert organized.main(["organize", "ignored", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert negative_organized.main(["ignored", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"]
    monkeypatch.setenv("R3_CAD_DRY_RUN", "1")
    result = stage.run(project)
    assert result.ok and result.diagnostics[0].code == "onshape_dry_run"
    assert not [key for key in project.lock if key.endswith("pending")]


def test_organization_check_stops_before_reading_any_other_endpoint():
    from r3_onshape.organized import DocumentInfo, StudioRef, organization_check
    class API:
        calls = []
        def json(self, method, path):
            self.calls.append(path)
            raise OnshapeError(429, "spent", {"Retry-After": "120"})
    api = API()
    robot = DocumentInfo("D", "W", "Robot", studios={"Block": StudioRef("Block", "E", {}, {}, {})})
    with pytest.raises(OnshapeError):
        organization_check(api, None, robot, DocumentInfo("L", "LW", "Library"), {}, {}, StageResult(stage="cad"))
    assert api.calls == ["/partstudios/d/D/w/W/e/E/features"]


def test_editor_quota_never_retries(monkeypatch):
    from r3_onshape.bridge import NativeBridge
    # Construct without bridge/auth discovery: only the local adapter retry policy is under test.
    bridge = NativeBridge.__new__(NativeBridge)
    calls = []
    def fail(*args):
        calls.append(args)
        raise OnshapeError(429, "spent", {"Retry-After": "60"})
    monkeypatch.setattr(bridge, "_post", fail)
    monkeypatch.setattr("r3_onshape.bridge.time.sleep", lambda _: pytest.fail("quota retry"))
    with pytest.raises(OnshapeError): bridge.local("element_tree", {})
    assert len(calls) == 1


def test_library_refresh_reuses_version_pinned_alias_part(tmp_path, monkeypatch):
    from r3_onshape.aliases import part_signature
    from r3_onshape.organized import ensure_library
    part, project, api = _part(), FakeProject(tmp_path), FakeAPI()
    project.lock["cad_library"] = {"document_id": "OLD", "workspace_id": "OW", "version_id": "OV", "digest": "older",
        "part_signatures": {part.key: part_signature(part)}, "studios": {part.studio: {
            "element_id": "OE", "part_ids": {part.key: "OP"}, "connectors": {part.key: {"mc_mount": "OF"}}, "folders": {}}}}
    monkeypatch.setattr("r3_onshape.aliases.geometry_aliases", lambda _: {"sku-variant": part.key})
    doc = ensure_library(api, project, {part.key: part}, StageResult(stage="cad"))
    assert api.feature_posts == 0
    assert not [call for call in api.calls if call[0] == "DELETE"]
    assert doc.references[0].did == "OLD" and doc.references[0].vid == "OV"
    assert doc.references[0].part_ref(part.key)[:2] == ("OE", "OP")
    assert project.lock["cad_library"]["references"][0]["did"] == "OLD"
    assert project.lock["cad_library"]["part_signatures"][part.key] == part_signature(part)


def test_dry_run_subtracts_acknowledged_feature_writes(tmp_path):
    from r3_onshape.dry_run import _rebuild_progress
    project, api = FakeProject(tmp_path), FakeAPI()
    project.lock["cad_build_pending"] = {"document_id": "D"}
    journal = RequestJournal(api, project.build_dir("cad") / "robot-D.journal.json", "digest")
    journal.json("POST", "/partstudios/d/D/w/W/e/E/features", body={"feature": {"name": "sketch", "featureType": "newSketch"}})
    out = {"summary": {"features": 3, "rest_feature_reads": 2}}
    _rebuild_progress(project, out)
    assert out["summary"]["remaining_feature_writes"] == 2
    assert out["summary"]["estimated_features_calls"] == 4
    assert out["completed_feature_requests"][0]["feature"] == "sketch"


def test_robot_resume_keeps_finished_construction_after_quota(tmp_path, monkeypatch):
    from r3_onshape import organized
    project, api, part = FakeProject(tmp_path), FakeAPI(fail_feature=2), _part()
    part.library = False
    project.spec.cad.document_name = "Robot"
    project.spec.robot = SimpleNamespace(name="Robot")
    monkeypatch.setattr(organized, "build_part_catalog", lambda _: ({part.key: part}, {}))
    monkeypatch.setattr(organized, "plan_assembly", lambda *args: organized.AssemblyPlan({}, [], []))
    monkeypatch.setattr(organized, "robot_variables", lambda _: ({}, {}))
    monkeypatch.setattr(organized, "input_digest", lambda _: "same-inputs")
    monkeypatch.setattr(organized, "ensure_library", lambda *args: organized.DocumentInfo("L", "LW", "Library"))
    class Builder:
        def __init__(self, *args):
            self.instance_ids, self.sub_instances, self.mate_ids = {}, {}, {}
        def build(self): pass
        def verify(self): raise OnshapeError(429, "verification quota", {"Retry-After": "30"})
    monkeypatch.setattr(organized, "AssemblyBuilder", Builder)
    def studios(transport, *args):
        for name, kind in (("sketch", "newSketch"), ("solid", "extrude")):
            transport.json("POST", "/partstudios/d/D/w/W/e/E/features", body={"feature": {"name": name, "featureType": kind}})
    monkeypatch.setattr(organized, "_build_studios", studios)
    # The variable-studio response is the only extra endpoint this minimal workflow needs.
    original = api.request
    def request(method, path, *args):
        if method == "POST" and path.endswith("/variablestudio"):
            return b'{"id":"V"}'
        return original(method, path, *args)
    monkeypatch.setattr(api, "request", request)
    result = StageResult(stage="cad")
    organized.sync_organized(project, api, result)
    assert result.diagnostics[-1].code == "onshape_quota_blocked"
    assert not project.lock["cad_build_pending"]["construction_complete"]
    assert api.feature_posts == 2
    api.fail_feature = None
    # Replay the first sketch locally, finish the solid, checkpoint construction;
    # verification is also quota-blocked and must preserve the complete document.
    result = StageResult(stage="cad")
    organized.sync_organized(project, api, result)
    assert project.lock["cad_build_pending"]["construction_complete"]
    assert api.feature_posts == 3
    assert result.diagnostics[-1].data["reset_at"]
    before = len(api.calls)
    organized.sync_organized(project, api, StageResult(stage="cad"))
    assert len(api.calls) == before  # completed construction is not even replayed remotely
    assert not any(method == "DELETE" for method, _, _ in api.calls)
