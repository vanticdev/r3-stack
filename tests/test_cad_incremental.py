"""Incremental standard-feature edits; all transports here are offline fakes."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from r3_model.diagnostics import StageResult
from r3_onshape import incremental as inc
from r3_onshape.client import OnshapeError, Transport
from r3_onshape.dry_run import FeaturePlanner
from r3_onshape.geometry import Loop, PartPlan, Plane, Prism
from r3_onshape.organized import AssemblyPlan, DocumentInfo, MatePlan, Placement, StudioRef, TOP_LEVEL
from r3_onshape.parts import ConnectorSpec, LocalPart
from r3_onshape.studio import populate_studio


def part(key="block", depth=.01, library=False):
    plan = PartPlan(name=key, kind="fabricated", material="Aluminum", density=2700.,
                    ops=[Prism(Plane.through([0, 0, 0], [0, 0, 1]),
                               [Loop.rectangle([0, 0], [.01, .01])], depth, 1, "new", "block")])
    return LocalPart(key, key, key, key, plan, library=library,
                     connectors=[ConnectorSpec("mc_mount", np.eye(4), "mount")])


def replace_ids(data, mapping):
    if isinstance(data, dict): return {k: replace_ids(v, mapping) for k, v in data.items()}
    if isinstance(data, list): return [replace_ids(v, mapping) for v in data]
    return mapping.get(data, data) if isinstance(data, str) else data


def recorded(part, eid):
    planner = FeaturePlanner(part.studio)
    populate_studio(planner, [part])
    mapping = {d["id"]: f"F{i}" for i, d in enumerate(planner.definitions)}
    mapping["planned-part-0"] = "P"
    features = [{**replace_ids(d["feature"], mapping), "featureId": mapping[d["id"]]} for d in planner.definitions]
    ref = StudioRef(part.studio, eid, {part.key: "P"},
                    {part.key: {"mc_mount": features[-1]["featureId"]}}, replace_ids(planner.out.folders, mapping))
    return ref, features


class Fake(Transport):
    name = "fake-incremental"
    def __init__(self, studios=None):
        self.features = copy.deepcopy(studios or {})
        self.calls = []
        self.instances = {TOP_LEVEL: []}
        self.rollback = {}
        self.fail_eid = None
        self.fail_path = None
        self.serial = 100

    def request(self, method, path, query=None, body=None, accept="application/json", timeout=120.0):
        self.calls.append((method, path, copy.deepcopy(body)))
        eid = path.split("/e/")[-1].split("/")[0] if "/e/" in path else ""
        if ((self.fail_eid == eid and method == "POST" and "/features" in path)
                or self.fail_path and path.endswith(self.fail_path)):
            raise OnshapeError(429, "quota", {"Retry-After": "60"})
        result = {}
        if method == "GET" and path.endswith("/features"):
            result = {"features": copy.deepcopy(self.features.get(eid, []))}
        elif method == "POST" and path.endswith("/features/updates"):
            for patch in body["features"]:
                old = next(f for f in self.features[eid] if f["featureId"] == patch["featureId"])
                old.update(patch)
            result = {"featureStates": {f["featureId"]: {"featureStatus": "OK"} for f in body["features"]}}
        elif method == "POST" and "/features/featureid/" in path:
            old = next(f for f in self.features[eid] if f["featureId"] == body["feature"]["featureId"])
            old.update(body["feature"])
            result = {"feature": old, "featureState": {"featureStatus": "OK"}}
        elif method == "DELETE" and "/features/featureid/" in path:
            from urllib.parse import unquote
            fid = unquote(path.split("/featureid/")[1])
            self.features[eid] = [f for f in self.features[eid] if f["featureId"] != fid]
        elif method == "POST" and path.endswith("/features/rollback"):
            self.rollback[eid] = int(body)
        elif method == "POST" and path.endswith("/features"):
            self.serial += 1
            feature = {**body["feature"], "featureId": f"N{self.serial}"}
            index = self.rollback.get(eid, -1)
            self.features.setdefault(eid, []).insert(len(self.features.get(eid, [])) if index == -1 else index, feature)
            result = {"feature": feature, "featureState": {"featureStatus": "OK"}}
        elif path.endswith("/versions"):
            result = {"id": "V2"}
        elif method == "POST" and path.startswith("/partstudios/") and "/e/" not in path:
            self.serial += 1
            eid = f"NEW{self.serial}"
            self.features[eid] = []
            result = {"id": eid}
        elif method == "GET" and path.startswith("/parts/"):
            result = [{"partId": "P", "name": "block"}] if any(f.get("featureType") == "extrude" for f in self.features.get(eid, [])) else []
        elif method == "GET" and path.startswith("/metadata/"):
            result = {"properties": []}
        elif method == "GET" and path.startswith("/assemblies/"):
            result = {"rootAssembly": {"instances": copy.deepcopy(self.instances[TOP_LEVEL])}}
        elif method == "POST" and path.endswith("/modify"):
            self.instances[TOP_LEVEL] = [i for i in self.instances[TOP_LEVEL] if i["id"] not in body["deleteInstances"]]
        elif method == "POST" and path.endswith("/instances"):
            self.serial += 1
            self.instances[TOP_LEVEL].append({**body, "id": f"I{self.serial}"})
        return json.dumps(result).encode()


def edits(api):
    return [(m, p, b) for m, p, b in api.calls if m != "GET" and "/features" in p]


def test_unchanged_features_are_not_rewritten_and_connectors_keep_ids():
    original = part()
    ref, features = recorded(original, "E")
    api = Fake({"E": features, "UNTOUCHED": copy.deepcopy(features)})
    doc = DocumentInfo("D", "W", "Robot")
    builder = inc.IncrementalStudioBuilder(api, doc, ref, [part(depth=.02)])
    populate_studio(builder, [part(depth=.02)])
    out = builder.finish()
    writes = edits(api)
    assert len(writes) == 1 and writes[0][1].endswith("/features/updates")
    assert [f["featureId"] for f in writes[0][2]["features"]] == ["F1"]
    assert out.connectors == ref.connectors
    assert out.part_ids == ref.part_ids and out.folders == ref.folders
    assert api.features["E"][0] == features[0]  # unchanged sketch
    assert api.features["UNTOUCHED"] == features
    assert not any("UNTOUCHED" in p for _, p, _ in api.calls)


def test_sketch_geometry_uses_single_feature_update_and_retains_feature():
    original = part()
    ref, features = recorded(original, "E")
    changed = part()
    changed.plan.ops[0].loops = [Loop.rectangle([0, 0], [.02, .01])]
    api = Fake({"E": features})
    builder = inc.IncrementalStudioBuilder(api, DocumentInfo("D", "W", "R"), ref, [changed])
    populate_studio(builder, [changed]); builder.finish()
    writes = edits(api)
    assert len(writes) == 1 and writes[0][1].endswith("/featureid/F0")
    assert writes[0][2]["feature"]["entities"]
    assert all(f["featureId"] == f"F{i}" for i, f in enumerate(api.features["E"]))


def test_feature_count_change_only_deletes_obsolete_and_inserts_before_connectors():
    original = part()
    extra = Prism(Plane.through([0, 0, 0], [0, 0, 1]), [Loop.rectangle([.001, .001], [.002, .002])],
                  None, 1, "remove", "slot", "Cutouts")
    original.plan.ops.append(extra)
    ref, features = recorded(original, "E")
    changed = part()
    changed.plan.ops.append(Prism(extra.plane, extra.loops, None, 1, "remove", "new slot", "Cutouts"))
    api = Fake({"E": features})
    builder = inc.IncrementalStudioBuilder(api, DocumentInfo("D", "W", "R"), ref, [changed])
    populate_studio(builder, [changed]); out = builder.finish()
    assert [f["name"] for f in api.features["E"]] == ["block sketch", "block extrude 10 mm", "new slot sketch", "new slot cut through all", "mc_mount"]
    assert out.connectors == ref.connectors
    assert set(out.folders["Base shape"]) == set(ref.folders["Base shape"])
    assert len(out.folders["Cutouts"]) == 2
    assert len([m for m, _, _ in edits(api) if m == "DELETE"]) == 2


class Project:
    def __init__(self, root, state, library, pending):
        self.root = root
        self.lock = {"cad": {"document_id": "D", "workspace_id": "W"}, "cad_library": library}
        self.model = {"source": {"recomposed": {"pending_rebuild": pending}}}
        self.spec = SimpleNamespace(components={})
        inc.save_json(self.build_dir("cad") / "build_state.json", state)
    def build_dir(self, stage=""):
        path = self.root / "build" / stage
        path.mkdir(parents=True, exist_ok=True)
        return path
    def read_build_json(self, name): return self.model
    def write_lock(self, key, value): self.lock[key] = copy.deepcopy(value)


def setup_project(tmp_path, monkeypatch, library=False):
    a, b, untouched = part("a", .02, library), part("b", .02), part("untouched")
    ra, fa = recorded(part("a", library=library), "EA")
    rb, fb = recorded(part("b"), "EB")
    ru, fu = recorded(untouched, "EU")
    lib = {"document_id": "L", "workspace_id": "LW", "version_id": "V1", "name": "Library", "digest": "old",
           "studios": {"a": {k: v for k, v in asdict(ra).items() if k != "name"}} if library else {}}
    state = {"robot": {"did": "D", "wid": "W", "name": "Robot", "variable_studio": "",
                       "studios": {r.name: asdict(r) for r in ([rb, ru] if library else [ra, rb, ru])},
                       "assemblies": {TOP_LEVEL: "A"}},
             "instance_ids": {TOP_LEVEL: {"a": "IA", "b": "IB", "untouched": "IU"}},
             "sub_instances": {}, "mate_ids": {TOP_LEVEL: []}}
    project = Project(tmp_path, state, lib, {"a": {}, "b": {}})
    parts = {p.key: p for p in [a, b, untouched]}
    plan = AssemblyPlan({key: Placement(key, key, TOP_LEVEL, np.eye(4), "Structure") for key in parts}, [], [TOP_LEVEL])
    monkeypatch.setattr(inc, "build_part_catalog", lambda _: (parts, {key: key for key in parts}))
    monkeypatch.setattr(inc, "plan_assembly", lambda *args: plan)
    monkeypatch.setattr(inc, "robot_variables", lambda _: ({}, {}))
    monkeypatch.setattr(inc, "input_digest", lambda _: "digest")
    def verify(project, api, result, robot, library, builder, *args, **kwargs):
        return {"mode": "incremental", "robot": robot, "library_doc": library}
    monkeypatch.setattr("r3_onshape.organized.verify_and_import", verify)
    api = Fake({"EA": fa, "EB": fb, "EU": fu, "A": []})
    api.instances[TOP_LEVEL] = [{"id": iid, "elementId": eid, "partId": "P"}
                              for iid, eid in [("IA", "EA"), ("IB", "EB"), ("IU", "EU")]]
    return project, api, parts, plan


@pytest.mark.parametrize("phase", ["studio", "assembly", "verification"])
def test_quota_stop_resume_and_lock_publication(tmp_path, monkeypatch, phase):
    project, api, parts, plan = setup_project(tmp_path, monkeypatch, library=True)
    if phase == "studio": api.fail_eid = "EB"
    if phase == "assembly": api.fail_path = "/instances"
    real_verify = __import__("r3_onshape.organized", fromlist=["verify_and_import"]).verify_and_import
    if phase == "verification":
        def blocked(*args, **kwargs): raise OnshapeError(429, "verify quota", {"Retry-After": "30"})
        monkeypatch.setattr("r3_onshape.organized.verify_and_import", blocked)
    result = StageResult(stage="cad")
    inc.sync_incremental(project, api, result)
    assert not result.ok and result.diagnostics[-1].code == "onshape_quota_blocked"
    assert result.diagnostics[-1].data["reset_at"]
    assert project.lock["cad_library"]["version_id"] == "V1"  # no prematurely published pin
    assert not any(m == "DELETE" and p.startswith("/documents/") for m, p, _ in api.calls)
    first = len(api.calls)
    api.fail_eid, api.fail_path = None, None
    monkeypatch.setattr("r3_onshape.organized.verify_and_import", real_verify)
    result = StageResult(stage="cad")
    report = inc.sync_incremental(project, api, result)
    assert result.ok
    assert not any("/e/EA/features" in p and m == "POST" for m, p, _ in api.calls[first:])
    assert not any("/e/EU/" in p for _, p, _ in api.calls)
    assert sum(m == "POST" and p.endswith("/versions") for m, p, _ in api.calls) == 1
    inc.finish_incremental(project, report)
    assert project.lock["cad_incremental_pending"] == {}
    assert project.lock["cad_library"]["version_id"] == "V2"
    assert project.lock["cad_library"]["document_id"] == "L"
    state = json.loads((project.build_dir("cad") / "build_state.json").read_text())
    assert state["instance_ids"][TOP_LEVEL]["untouched"] == "IU"
    assert state["robot"]["studios"]["b"]["element_id"] == "EB"
    assert set(project.lock["cad_incremental"]["part_signatures"]) == set(parts)


def test_assembly_only_replaces_affected_instances_and_mates(tmp_path, monkeypatch):
    project, api, parts, plan = setup_project(tmp_path, monkeypatch, library=True)
    touching = MatePlan("a mount", "FASTENED", ("a", "mc_mount"), ("b", "mc_mount"), 0, 0, TOP_LEVEL, "Mounting mates", "a-mount")
    unchanged = MatePlan("u mount", "FASTENED", ("b", "mc_mount"), ("untouched", "mc_mount"), 0, 0, TOP_LEVEL, "Mounting mates", "u-mount")
    plan.mates = [touching, unchanged]
    path = project.build_dir("cad") / "build_state.json"
    state = json.loads(path.read_text())
    state["mate_ids"][TOP_LEVEL] = [["MA", asdict(touching)], ["MU", asdict(unchanged)]]
    inc.save_json(path, state)
    api.features["A"] = [{"featureId": "MA"}, {"featureId": "MU"}]
    report = inc.sync_incremental(project, api, StageResult(stage="cad"))
    deletes = [(p, b) for m, p, b in api.calls if m == "DELETE"]
    assert len(deletes) == 1 and deletes[0][0].endswith("/featureid/MA")
    instance_deletes = [b for m, p, b in api.calls if p.endswith("/modify")]
    assert instance_deletes == [{"deleteInstances": ["IA"], "editDescription": "R3 incremental replacement"}]
    assert len([p for m, p, b in api.calls if m == "POST" and p.endswith("/features")]) == 1
    assert report["robot"].did == "D"


def test_resume_refuses_changed_inputs_before_transport(tmp_path, monkeypatch):
    project, api, _, _ = setup_project(tmp_path, monkeypatch)
    project.lock["cad_incremental_pending"] = {"digest": "other"}
    with pytest.raises(ValueError, match="inputs changed"):
        inc.sync_incremental(project, api, StageResult(stage="cad"))
    assert api.calls == []


def test_plan_readback_checks_bounds_volume_and_mass():
    p = part()
    geometry = SimpleNamespace(lo=np.array([0, 0, 0]), hi=np.array([.01, .01, .01]))
    masses = {"block": {"mass": [.0027], "volume": [1e-6]}}
    result = StageResult(stage="cad")
    inc.verify_plan_properties({"block": p}, {"block"}, {"block": geometry}, masses, result)
    assert result.ok
    masses["block"]["mass"] = [1.0]
    inc.verify_plan_properties({"block": p}, {"block"}, {"block": geometry}, masses, result)
    assert result.diagnostics[-1].code == "cad_incremental_plan_mismatch"
    assert result.diagnostics[-1].data["plan_mass"] == pytest.approx(.0027)


def test_offline_incremental_default_and_explicit_full(tmp_path, monkeypatch, capsys):
    from r3_onshape import organized, dry_run
    project, api, _, _ = setup_project(tmp_path, monkeypatch)
    monkeypatch.setattr("r3_model.project.Project.load", lambda _: project)
    monkeypatch.setattr("r3_onshape.client.default_transport", lambda: pytest.fail("offline transport discovery"))
    monkeypatch.setattr(dry_run, "full_rebuild_plan", lambda _: {"mode": "full", "dry_run": True})
    assert organized.main(["rebuild", "ignored", "--dry-run"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "incremental"
    assert data["summary"]["parts"] == 2
    assert data["summary"]["part_studios"] == 2
    assert {s["name"] for s in data["part_studios"]} == {"a", "b"}
    assert api.calls == []
    assert organized.main(["rebuild", "ignored", "--full", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "full"


def test_verification_failure_does_not_publish_lock(tmp_path, monkeypatch):
    project, api, _, _ = setup_project(tmp_path, monkeypatch, library=True)
    def mismatch(project, api, result, *args, **kwargs):
        from r3_model.diagnostics import Diagnostic
        result.add(Diagnostic.error("cad_incremental_plan_mismatch", "bad native mass"))
        return {"mode": "incremental"}
    monkeypatch.setattr("r3_onshape.organized.verify_and_import", mismatch)
    result = StageResult(stage="cad")
    inc.sync_incremental(project, api, result)
    assert not result.ok
    assert project.lock["cad_library"]["version_id"] == "V1"
    assert project.lock["cad_incremental_pending"]
    assert not any(m == "DELETE" and p.startswith("/documents/") for m, p, _ in api.calls)


def test_new_library_part_stays_in_existing_library_document(tmp_path, monkeypatch):
    project, api, parts, plan = setup_project(tmp_path, monkeypatch)
    new = part("new-sensor", library=True)
    parts[new.key] = new
    plan.placements["sensor"] = Placement("sensor", new.key, TOP_LEVEL, np.eye(4), "Electronics")
    project.model["source"]["recomposed"]["pending_rebuild"][new.key] = {"reason": "new part"}
    report = inc.sync_incremental(project, api, StageResult(stage="cad"))
    assert report["library_doc"].did == "L"
    assert new.key in report["library_doc"].studios
    creations = [(p, b) for m, p, b in api.calls if m == "POST" and "/partstudios/" in p and "/e/" not in p]
    assert creations == [("/partstudios/d/L/w/LW", {"name": "new-sensor"})]
    inserted = [b for m, p, b in api.calls if m == "POST" and p.endswith("/instances")]
    assert inserted[-1]["documentId"] == "L" and inserted[-1]["versionId"] == "V2"
    assert not any(p == "/documents" for _, p, _ in api.calls)


def test_server_omitted_defaults_do_not_rewrite_unchanged_features():
    p = part()
    ref, features = recorded(p, "E")
    def omit(data):
        if isinstance(data, dict): return {k: omit(v) for k, v in data.items() if v is not False and v != ""}
        if isinstance(data, list): return [omit(v) for v in data]
        return data
    features = omit(features)
    features[1]["parameters"].append({"parameterId": "server-default", "value": 1})
    # Native sketch solving may round a coordinate at double precision.
    features[0]["entities"][0]["geometry"]["pntX"] += 1e-14
    api = Fake({"E": features})
    builder = inc.IncrementalStudioBuilder(api, DocumentInfo("D", "W", "R"), ref, [p])
    populate_studio(builder, [p]); builder.finish()
    assert edits(api) == []


def test_native_organizer_can_read_unpublished_checkpoint(tmp_path, monkeypatch):
    project, api, _, _ = setup_project(tmp_path, monkeypatch, library=True)
    inc.sync_incremental(project, api, StageResult(stage="cad"))
    path, library, cp = inc.pending_organization_state(project)
    assert path.exists() and cp["assembly_complete"]
    assert library.vid == "V2" and project.lock["cad_library"]["version_id"] == "V1"
    assert json.loads(path.read_text())["robot"]["did"] == "D"


def test_stage_publishes_cad_and_library_pins_after_verification(tmp_path, monkeypatch):
    from r3_onshape import organized, stage
    project, api, _, _ = setup_project(tmp_path, monkeypatch, library=True)
    project.fingerprint = lambda: {}
    project.spec.cad = SimpleNamespace(provider="onshape")
    project.spec.assembly = object()
    monkeypatch.setattr("r3_onshape.purchased.diagnostics", lambda *args: None)
    monkeypatch.setattr(organized, "input_digest", lambda _: "digest")
    monkeypatch.setattr(stage, "default_transport", lambda: api)
    monkeypatch.setattr(stage, "_summarize", lambda *args: None)
    def write_model(project, report, transport, digest):
        library = report["library_doc"]
        data = {"source": {"element_id": "A", "assembly_id": "A", "url": report["robot"].url("A"),
                           "microversion": "M2", "validator_version": "fit", "mesh_digest": "mesh",
                           "library": {"document_id": library.did, "version_id": library.vid}}, "parts": {}}
        path = project.build_dir("cad") / "cad_model.json"
        inc.save_json(path, data)
        return path
    monkeypatch.setattr(organized, "write_model", write_model)
    # A matching cached model must not conceal an unfinished verification/pin phase.
    inc.sync_incremental(project, api, StageResult(stage="cad"))
    project.model["source"]["generator_digest"] = "digest"
    result = stage.run(project)
    assert result.ok
    cad = project.lock["cad"]
    assert (cad["document_id"], cad["workspace_id"], cad["assembly_id"]) == ("D", "W", "A")
    assert cad["revision"] == "M2" and cad["generator_digest"] == "digest"
    assert cad["library_version_id"] == project.lock["cad_library"]["version_id"] == "V2"
    assert project.lock["cad_incremental_pending"] == {}


def bank_part(key, library=False):
    from r3_catalog.modelbank import ModelEntry, Source, Spec, Validation
    p = part(key, library=library)
    p.plan.kind, p.plan.mass, p.plan.density = "purchased", .1, 0
    frame = {"origin": [0, 0, 0], "x": [1, 0, 0], "z": [0, 0, 1]}
    entry = ModelEntry(id=f"vendor-{key}", name=key, category="sensor",
                       sources=[Source(kind="vendor_step", url="https://vendor.test/model.step", sha256="a" * 64)],
                       key_specs={"dimensions": Spec(value=[.01, .01, .01], unit="m")},
                       validation=Validation(status="passed", key_features={"mount": True}),
                       placement=frame, interface_frames={"mc_mount": frame})
    p.bank_model = entry.model_dump(mode="json")
    return p


class ImportFake(Fake):
    def __init__(self, api):
        super().__init__(api.features)
        self.instances = api.instances
        self.uploads = []
        self.imported = set()

    def upload(self, path, file_path, fields, timeout=600.0):
        self.serial += 1
        eid = f"IMPORT{self.serial}"
        self.imported.add(eid)
        self.features[eid] = []
        self.uploads.append((path, fields, eid))
        return {"id": f"T{self.serial}", "requestState": "DONE", "resultElementIds": [eid]}

    def request(self, method, path, query=None, body=None, accept="application/json", timeout=120.0):
        from r3_onshape.policy import check_request
        check_request(method, path, body)
        eid = path.split("/e/")[-1].split("/")[0] if "/e/" in path else ""
        if method == "GET" and (path.endswith("/boundingboxes") or path.startswith("/parts/") and eid in self.imported):
            self.calls.append((method, path, copy.deepcopy(body)))
            result = ({"lowX": 0, "lowY": 0, "lowZ": 0, "highX": .01, "highY": .01, "highZ": .01}
                      if path.endswith("/boundingboxes") else [{"partId": "VENDOR", "name": "Vendor composite"}])
            return json.dumps(result).encode()
        return super().request(method, path, query, body, accept, timeout)


@pytest.fixture
def vendor_step(tmp_path, monkeypatch):
    # Use a CAD engine's STEP writer, never a hand-authored exchange format.
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    step = tmp_path / "vendor.step"
    writer = STEPControl_Writer()
    writer.Transfer(BRepPrimAPI_MakeBox(10, 10, 10).Shape(), STEPControl_AsIs)
    assert writer.Write(str(step)) == IFSelect_RetDone
    monkeypatch.setattr("r3_onshape.purchased.acquire", lambda source: (step, source))
    return step


@pytest.mark.parametrize("library", [False, True])
def test_native_to_import_retires_source_and_only_swaps_affected_instances(tmp_path, monkeypatch, vendor_step, library):
    project, native, parts, plan = setup_project(tmp_path, monkeypatch, library=library)
    parts["a"] = bank_part("a", library)
    api = ImportFake(native)
    touching = MatePlan("a mount", "FASTENED", ("a", "mc_mount"), ("untouched", "mc_mount"),
                        0, 0, TOP_LEVEL, "Mounting mates", "mount")
    plan.mates = [touching]
    state = inc._state(project)
    state["mate_ids"][TOP_LEVEL] = [["MA", asdict(touching)]]
    inc.save_json(project.build_dir("cad") / "build_state.json", state)
    api.features["A"] = [{"featureId": "MA"}]
    result = StageResult(stage="cad")
    report = inc.sync_incremental(project, api, result)
    assert result.ok
    doc = report["library_doc"] if library else report["robot"]
    eid, pid, _ = doc.part_ref("a")
    assert eid.startswith("IMPORT") and pid == "VENDOR"
    assert doc.studios["Retired a (EA)"].part_ids == {}
    assert doc.studios["Retired a (EA)"].connectors == {}
    assert all("a" not in s.part_ids for s in doc.studios.values() if s.element_id == "EA")
    assert len(api.uploads) == 1 and api.uploads[0][0] == ("/translations/d/L/w/LW" if library else "/translations/d/D/w/W")
    assert api.uploads[0][1]["createComposite"] is True
    assert api.features["EA"] == native.features["EA"]  # editable retired history survives
    assert ("POST", f"/elements/d/{doc.did}/w/{doc.wid}/e/EA", {"name": "Retired a (EA)"}) in api.calls
    retired_metadata = [b for m, p, b in api.calls if m == "POST" and p.endswith("/e/EA/p/P")]
    assert retired_metadata[0]["properties"][0]["value"] == "Retired a"
    assert any(m == "DELETE" and p.endswith("/featureid/MA") for m, p, _ in api.calls)
    assert not any(i["id"] == "IA" for i in api.instances[TOP_LEVEL])
    assert sum(i["elementId"] == eid for i in api.instances[TOP_LEVEL]) == 1
    assert next(i for i in api.instances[TOP_LEVEL] if i["id"] == "IU")["elementId"] == "EU"
    assert not any("/e/EU/" in p for _, p, _ in api.calls)
    assert not any(p == "/documents" for _, p, _ in api.calls)
    assert all(f["featureType"] == "mateConnector" for f in api.features[eid])
    inc.finish_incremental(project, report)
    assert project.lock["cad"]["document_id"] == "D"
    # Pending artifact entries stop scheduling once recomposition clears them.
    project.model["source"]["recomposed"]["pending_rebuild"] = {}
    assert inc._work(project, parts, inc._state(project))[0] == []


def test_new_import_uses_pinned_document_without_native_geometry(tmp_path, monkeypatch, vendor_step):
    project, native, parts, plan = setup_project(tmp_path, monkeypatch)
    parts["new"] = bank_part("new", library=True)
    plan.placements["new"] = Placement("new", "new", TOP_LEVEL, np.eye(4), "Electronics")
    api = ImportFake(native)
    report = inc.sync_incremental(project, api, StageResult(stage="cad"))
    assert report["library_doc"].part_ref("new")[0].startswith("IMPORT")
    assert len(api.uploads) == 1 and api.uploads[0][0] == "/translations/d/L/w/LW"
    assert not any(m == "POST" and p.startswith("/partstudios/") and "/e/" not in p for m, p, _ in api.calls)
    assert not any("Retired" in json.dumps(b) for _, _, b in api.calls)


@pytest.mark.parametrize("phase", ["connector", "retirement", "assembly", "checkpoint"])
def test_import_resume_does_not_repeat_acknowledged_writes(tmp_path, monkeypatch, vendor_step, phase):
    project, native, parts, plan = setup_project(tmp_path, monkeypatch, library=True)
    parts["a"] = bank_part("a", library=True)
    api = ImportFake(native)
    if phase == "connector": api.fail_eid = "IMPORT101"
    if phase == "retirement": api.fail_path = "/e/EA/p/P"
    if phase == "assembly": api.fail_path = "/instances"
    if phase == "checkpoint":
        save = inc.save_json
        def crash(path, value):
            if path.name == "incremental_state.json" and value.get("done"):
                raise RuntimeError("simulated crash before studio checkpoint")
            save(path, value)
        monkeypatch.setattr(inc, "save_json", crash)
    result = StageResult(stage="cad")
    inc.sync_incremental(project, api, result)
    assert not result.ok
    if phase != "checkpoint":
        assert result.diagnostics[-1].code == "onshape_quota_blocked"
        # Nothing, including cleanup, follows the failing request.
        last = api.calls[-1]
        assert (last[1].endswith(api.fail_path) if api.fail_path else "/e/IMPORT101/features" in last[1])
    first = len(api.calls)
    api.fail_eid, api.fail_path = None, None
    if phase == "checkpoint": monkeypatch.setattr(inc, "save_json", save)
    result = StageResult(stage="cad")
    report = inc.sync_incremental(project, api, result)
    assert result.ok and len(api.uploads) == 1
    assert sum(m == "POST" and p.endswith("/versions") for m, p, _ in api.calls) == 1
    insert_attempts = sum(m == "POST" and p.endswith("/instances") for m, p, _ in api.calls)
    assert insert_attempts == (2 if phase == "assembly" else 1)
    assert len(api.features["IMPORT101"]) == 1
    assert sum(i.get("elementId") == "IMPORT101" for i in api.instances[TOP_LEVEL]) == 1
    # Re-entering a completed checkpoint also performs no construction writes.
    after_resume = len(api.calls)
    inc.sync_incremental(project, api, StageResult(stage="cad"))
    assert api.calls[after_resume:] == []
    inc.finish_incremental(project, report)
    assert project.lock["cad_incremental_pending"] == {}
