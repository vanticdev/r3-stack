"""SKU inheritance reuses an immutable CAD identity, without merging geometry variants."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from r3_model.project import Project
from r3_onshape.aliases import geometry_aliases, geometry_type, part_signature, reusable_alias_parts
from r3_onshape.organized import AssemblyBuilder, DocumentInfo, StudioRef, locked_library_document
from r3_onshape.parts import build_part_catalog
from r3_onshape.recompose import plan_sha


@pytest.fixture
def project(tmp_path):
    source = Project.load(Path(__file__).resolve().parents[1] / "examples/robots/rc-car")
    source.spec.components = {"pi": source.spec.components["pi"]}
    source.spec.components["pi"].type = "raspberry-pi-5-4gb"
    source.spec.components["pi"].source = None
    source.spec.mechanical.fasteners = []
    source.root = tmp_path
    source.lock = {}
    return source


def test_inherited_sku_has_the_canonical_geometry_key(project):
    assert geometry_type(project, "raspberry-pi-5-4gb") == "raspberry-pi-5-8gb"
    parts, instances = build_part_catalog(project)
    assert instances == {"pi": "raspberry-pi-5-8gb"}
    assert list(parts) == ["raspberry-pi-5-8gb"]
    assert geometry_aliases(project) == {"raspberry-pi-5-4gb": "raspberry-pi-5-8gb"}
    project.spec.components["pi"].type = "raspberry-pi-5-8gb"
    base, base_instances = build_part_catalog(project)
    assert base_instances == instances
    assert plan_sha(base[instances["pi"]].plan) == plan_sha(parts[instances["pi"]].plan)


def test_geometry_or_interface_changes_do_not_alias(project):
    project.catalog.components["raspberry-pi-5-4gb"].mechanical.size[2] += .001
    assert geometry_type(project, "raspberry-pi-5-4gb") == "raspberry-pi-5-4gb"


def test_canonical_library_lookup_uses_old_document_without_writes(project):
    parts, _ = build_part_catalog(project)
    key = "raspberry-pi-5-8gb"
    library = DocumentInfo("LIB", "LW", "Parts", vid="LV", studios={"Pi": StudioRef("Pi", "PE", {key: "PID"}, {key: {}}, {})})
    class NoNetwork:
        def json(self, *args, **kwargs): pytest.fail("canonical lookup must not call Onshape")
    parts[key].bank_model = None  # this test exercises the existing installed library path
    builder = AssemblyBuilder(NoNetwork(), project, DocumentInfo("D", "W", "Robot"), library, parts,
                              SimpleNamespace(), SimpleNamespace())
    doc, eid, pid, version = builder._source(key)
    assert (doc.did, eid, pid, version) == ("LIB", "PE", "PID", "LV")


def test_alias_reuse_survives_a_library_refresh_and_lock_roundtrip(project):
    parts, _ = build_part_catalog(project)
    key = "raspberry-pi-5-8gb"
    project.lock["cad_library"] = {"document_id": "LIB", "workspace_id": "LW", "version_id": "LV", "digest": "old",
        "aliases": {"raspberry-pi-5-4gb": key}, "studios": {"Pi": {
            "element_id": "PE", "part_ids": {key: "PID"}, "connectors": {key: {}}, "folders": {}}}}
    project.lock["cad_library"]["part_signatures"] = {key: part_signature(parts[key])}
    recording = project.build_dir("cad") / "fixtures/onshape_local_plans.json.gz"
    recording.parent.mkdir(parents=True)
    with gzip.open(recording, "wt") as handle:
        json.dump({"parts": {key: {"plan_sha256": plan_sha(parts[key].plan)}}}, handle)
    assert reusable_alias_parts(project, parts) == {key}
    from dataclasses import asdict
    old = locked_library_document(project)
    project.lock["cad_library"] = {"document_id": "NEW", "workspace_id": "NW", "version_id": "NV", "studios": {},
                                  "references": [asdict(old)], "aliases": {"raspberry-pi-5-4gb": key},
                                  "part_signatures": {key: part_signature(parts[key])}}
    new = locked_library_document(project)
    assert new.references[0].studios["Pi"].part_ids[key] == "PID"
    assert reusable_alias_parts(project, parts) == {key}
    parts[key].bank_model = None
    builder = AssemblyBuilder(None, project, DocumentInfo("D", "W", "Robot"), new, parts, SimpleNamespace(), SimpleNamespace())
    assert builder._source(key)[0].did == "LIB"


def test_alias_parent_connector_change_invalidates_reuse(project):
    parts, _ = build_part_catalog(project)
    key = "raspberry-pi-5-8gb"
    project.lock["cad_library"] = {"studios": {"Pi": {"part_ids": {key: "PID"}}},
                                  "part_signatures": {key: part_signature(parts[key])}}
    assert reusable_alias_parts(project, parts) == {key}
    parts[key].connectors[0].frame[0, 3] += .001
    assert reusable_alias_parts(project, parts) == set()
