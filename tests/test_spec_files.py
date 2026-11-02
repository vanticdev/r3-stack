"""Split descriptions, file ownership, optimistic concurrency and pulled authority."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from r3_github.scaffold import create_project
from r3_model.project import Project
from r3_model.pulled import PullError
from r3_model.spec_files import SpecError, load_spec_files, refresh_pulled_spec, validate_pulled_spec
from r3_orchestrator import mcp_server, spec_ops
from r3_orchestrator.api import agents, freshness
from r3_orchestrator.api.server import create_app

FIXTURE = Path(__file__).parent / "fixtures/split-description"


@pytest.fixture
def root(tmp_path):
    target = tmp_path / "mechanism"
    shutil.copytree(FIXTURE, target)
    return target


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.yaml") if p.is_file()}


def test_load_and_sources(root):
    spec = Project.load(root).spec
    assert set(spec.components) == {"base", "link"}
    assert spec.components["link"].mount == "base"
    assert spec.requirements.max_mass == 4
    assert spec.cad.source.endswith("/e/assembly")
    read = spec_ops.get_spec(root)
    assert read["spec"]["components"]["base"]["type"] == "synthetic-base"
    assert set(read["files"]) == {"robot.yaml", "parts/base.yaml", "parts/link.yaml", "electrical/system.yaml", "software/system.yaml"}
    assert next(s for s in read["sources"] if s["path"] == ["components", "base", "type"]) == {
        "path": ["components", "base", "type"], "file": "parts/base.yaml", "source_path": ["type"]}
    assert spec_ops.get_spec(root, "components.base")["spec"]["type"] == "synthetic-base"


@pytest.mark.parametrize("edit,code,file,path", [
    (lambda r: (r / "parts/base.yaml").unlink(), "spec_include_missing", "robot.yaml", ["components", "base", "$include"]),
    (lambda r: (r / "parts/base.yaml").write_text("type: a\ntype: b\n"), "spec_duplicate_definition", "parts/base.yaml", ["type"]),
    (lambda r: (r / "parts/base.yaml").write_text("type: a\nunknown: 1\n"), "spec_unknown_key", "parts/base.yaml", ["components", "base", "unknown"]),
    (lambda r: (r / "parts/link.yaml").write_text("type: b\nmount: undefined\n"), "spec_reference_undefined", "parts/link.yaml", ["components", "link", "mount"]),
    (lambda r: (r / "software/system.yaml").write_text("bindings: {probe: {interface: gpio, computer: missing, pins: []}}\n"), "spec_reference_undefined", "software/system.yaml", ["software", "bindings", "probe", "computer"]),
    (lambda r: (r / "electrical/system.yaml").write_text("direct_mates: [{a: missing.J, b: base.J}]\n"), "spec_reference_undefined", "electrical/system.yaml", ["electrical", "direct_mates", 0, "a"]),
])
def test_stable_diagnostics(root, edit, code, file, path):
    edit(root)
    with pytest.raises(SpecError) as captured:
        Project.load(root)
    assert captured.value.code == code
    error = captured.value.errors[0]
    assert error["file"] == file and error["path"] == path
    reply = mcp_server.r3_get_spec(project=str(root))
    assert not reply["ok"] and reply["errors"][0]["code"] == code


@pytest.mark.parametrize("value,code", [
    ({"$include": "robot.yaml"}, "spec_include_cycle"),
    ({"$include": "parts/base.yaml"}, "spec_duplicate_definition"),
    ({"$include": "../outside.yaml"}, "spec_include_invalid"),
    ({"$include": "/tmp/outside.yaml"}, "spec_include_outside_project"),
    ({"$include": ["parts/base.yaml"]}, "spec_include_invalid"),
    ({"$include": "parts/new.yaml", "type": "x"}, "spec_include_invalid"),
])
def test_unsafe_and_ambiguous_includes(root, value, code):
    path = root / "robot.yaml"
    data = yaml.safe_load(path.read_text())
    data["components"]["link"] = value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(SpecError) as error:
        Project.load(root)
    assert error.value.code == code


def test_nested_list_include_and_patch(root):
    (root / "software/system.yaml").write_text("modules:\n  - {$include: software/controller.yaml}\n")
    (root / "software/controller.yaml").write_text("name: controller\nruns_on: base\n")
    assert Project.load(root).spec.software.modules[0].runs_on == "base"
    before = spec_ops.get_spec(root)
    result = spec_ops.patch_spec(root, {"software": {"modules": [{"name": "controller", "runs_on": "link"}]}}, before["sha256"])
    assert result["ok"]
    assert Project.load(root).spec.software.modules[0].runs_on == "link"


def test_get_indexed_section_preserves_sources_and_fingerprint(root):
    (root / "software/system.yaml").write_text("modules:\n  - {$include: software/controller.yaml}\n")
    (root / "software/controller.yaml").write_text("name: controller\nruns_on: base\n")
    before = contents(root)
    whole = spec_ops.get_spec(root)
    selected = mcp_server.r3_get_spec(project=str(root), section="software.modules.0.runs_on")
    assert selected["ok"] and selected["spec"] == "base"
    for key in ("sha256", "files", "sources", "read_only"):
        assert selected[key] == whole[key]
    assert contents(root) == before


@pytest.mark.parametrize("index", ["-1", "1", "01", "1.0", "first", "\u0660", "9" * 5000])
def test_get_invalid_list_section_is_missing(root, index):
    (root / "software/system.yaml").write_text("modules: [{name: controller, runs_on: base}]\n")
    whole = spec_ops.get_spec(root)
    section = f"software.modules.{index}"
    selected = spec_ops.get_spec(root, section)
    assert selected == {"ok": False, "code": "spec_section_missing", "section": section, "sha256": whole["sha256"]}


def test_get_numeric_mapping_key_is_not_a_list_index(root):
    path = root / "parts/base.yaml"
    data = yaml.safe_load(path.read_text())
    data["params"] = {"0": {"samples": ["inspection"]}}
    path.write_text(yaml.safe_dump(data))
    selected = spec_ops.get_spec(root, "components.base.params.0.samples.0")
    assert selected["ok"] and selected["spec"] == "inspection"


def test_fingerprint_and_cache_invalidate_on_include_edit(root):
    cache = freshness.ProjectCache()
    first = cache.load(root)
    before = first.fingerprint()
    path = root / "parts/base.yaml"
    path.write_text(path.read_text().replace("Base  #", "Frame  #"))
    after = cache.load(root)
    assert after is not first and after.spec.components["base"].label == "Frame"
    assert after.fingerprint()["robot_yaml_sha256"] != before["robot_yaml_sha256"]


def test_patch_correct_files_comments_conflicts_and_dry_run(root):
    before = contents(root)
    read = spec_ops.get_spec(root)
    patch = {"components": {"base": {"label": "Frame"}}, "software": {"config": {"mode": "test"}}}
    dry = spec_ops.patch_spec(root, patch, read["sha256"], dry_run=True)
    assert dry["ok"] and dry["sha256"] == read["sha256"] and dry["patched_sha256"] != read["sha256"]
    assert contents(root) == before
    saved = spec_ops.patch_spec(root, patch, read["sha256"])
    assert saved["ok"] and saved["files_changed"] == ["parts/base.yaml", "software/system.yaml"]
    assert (root / "robot.yaml").read_bytes() == before["robot.yaml"]
    assert "label: Frame" in (root / "parts/base.yaml").read_text()
    assert "# keep this comment" in (root / "parts/base.yaml").read_text()
    assert "# Software description" in (root / "software/system.yaml").read_text()
    assert spec_ops.patch_spec(root, patch, read["sha256"])["code"] == "spec_conflict"
    current = spec_ops.get_spec(root)
    (root / "parts/link.yaml").write_text("type: synthetic-link\nlabel: Manual edit\n")
    assert spec_ops.patch_spec(root, {"robot": {"version": "2"}}, current["sha256"])["code"] == "spec_conflict"


def test_patch_new_part_file_and_invalid_transaction(root):
    read = spec_ops.get_spec(root)
    result = spec_ops.patch_spec(root, {"components": {"tool": {"type": "synthetic-tool", "mount": "link"}}}, read["sha256"])
    assert result["ok"] and (root / "parts/tool.yaml").is_file()
    assert yaml.safe_load((root / "robot.yaml").read_text())["components"]["tool"] == {"$include": "parts/tool.yaml"}
    read = spec_ops.get_spec(root)
    before = contents(root)
    result = spec_ops.patch_spec(root, {"components": {"base": {"label": "Changed"}}, "software": {"modules": [{"name": "bad", "runs_on": "undefined"}]}}, read["sha256"])
    assert result["code"] == "spec_invalid"
    assert result["errors"][0]["code"] == "spec_reference_undefined"
    assert result["errors"][0]["file"] == "software/system.yaml"
    assert contents(root) == before


def test_failed_multi_file_replace_rolls_back(root, monkeypatch):
    import r3_model.spec_files as files

    read = spec_ops.get_spec(root)
    before = contents(root)
    replace = files.os.replace
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        return replace(source, target)

    monkeypatch.setattr(files.os, "replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        spec_ops.patch_spec(root, {"components": {"base": {"label": "A"}, "link": {"label": "B"}}}, read["sha256"])
    assert contents(root) == before
    assert not list(root.rglob(".base.yaml.*")) and not list(root.rglob(".link.yaml.*"))


def publish_pull(root):
    folder = root / "cad/pulled"
    folder.mkdir(parents=True)
    frame = np.eye(4).tolist()
    parts = {}
    for name in ("base", "link"):
        paths = {kind: f"{name}.{kind}" for kind in ("stl", "step")}
        hashes = {}
        for kind, path in paths.items():
            content = f"synthetic {name} {kind}".encode()
            (folder / path).write_bytes(content)
            (folder / path).chmod(0o444)
            hashes[kind] = hashlib.sha256(content).hexdigest()
        selector = load_spec_files(root).data["components"][name].get("onshape") or {}
        parts[name] = {**paths, "onshape": {"occurrence_path": selector.get("occurrence_path", []),
                      "configuration": selector.get("configuration", "")}, "sha256": hashes, "mate_connectors": {"origin": frame}, "mass": 1.0,
                       "com": [0, 0, 0], "inertia": [1, 0, 0, 0, 1, 0, 0, 0, 1], "material": "test", "purchased": False}
    data = {"schema": "r3/pull@1", "document_id": "document", "version_id": "version", "parts": parts,
            "poses": {name: frame for name in parts},
            "mates": [{"id": "joint", "kind": "fastened", "a": ["base", "origin"], "b": ["link", "origin"]}]}
    text = json.dumps(data)
    (folder / "pull.json").write_text(text)
    (folder / "pull.json").chmod(0o444)
    (root / "r3.lock.yaml").write_text(yaml.safe_dump({"cad_pull": {"manifest_sha256": hashlib.sha256(text.encode()).hexdigest()}}))


def test_pull_refresh_and_integrity(root):
    publish_pull(root)
    read = spec_ops.get_spec(root)
    result = refresh_pulled_spec(root, read["sha256"])
    assert result["ok"] and result["sha256"] != read["sha256"]
    spec = Project.load(root).spec
    assert spec.components["base"].pulled.owner == "onshape-pull"
    assert spec.components["base"].pulled.stl == "base.stl"
    assert spec.attachments[0].a == ("base", "origin")
    assert validate_pulled_spec(root).version_id == "version"
    assert "# keep this comment" in (root / "parts/base.yaml").read_text()
    assert (root / "cad/pulled/pull.json").stat().st_mode & 0o222 == 0
    with pytest.raises(SpecError) as conflict:
        refresh_pulled_spec(root, read["sha256"])
    assert conflict.value.code == "spec_conflict"


@pytest.mark.parametrize("patch", [
    {"components": {"base": {"pulled": {"mass": 3}}}},
    {"components": {"base": {"pulled": None}}},
    {"components": {"base": None}},
    {"components": None},
    {"attachments": []},
])
def test_patch_refuses_pull_owned_fields_and_parent_deletion(root, patch):
    publish_pull(root)
    refresh_pulled_spec(root)
    read = spec_ops.get_spec(root)
    before = contents(root)
    result = spec_ops.patch_spec(root, patch, read["sha256"])
    assert result["code"] == "spec_read_only"
    assert contents(root) == before


@pytest.mark.parametrize("tamper", ["mass", "pose", "mate_connectors", "attachments", "sha256"])
def test_integrity_compares_local_derived_values(root, tamper):
    publish_pull(root)
    refresh_pulled_spec(root)
    if tamper == "attachments":
        path = root / "robot.yaml"
        data = yaml.safe_load(path.read_text())
        data["attachments"][0]["kind"] = "revolute"
    else:
        path = root / "parts/base.yaml"
        data = yaml.safe_load(path.read_text())
        pulled = data["pulled"]
        if tamper == "mass":
            pulled["mass"] = 2
        elif tamper == "pose":
            pulled["pose"][0][3] = 0.1
        elif tamper == "mate_connectors":
            pulled["mate_connectors"]["origin"][0][3] = 0.1
        else:
            pulled["sha256"]["stl"] = "f" * 64
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(PullError) as error:
        validate_pulled_spec(root)
    assert error.value.code == "cad_pull_modified"


def test_scaffold_split_blank_and_split_template(root, tmp_path, monkeypatch):
    blank = tmp_path / "blank"
    written = create_project(blank, platform_ref="test")
    assert {"electrical/system.yaml", "software/system.yaml"} <= set(written)
    assert (blank / "parts").is_dir() and Project.load(blank).spec.components == {}
    monkeypatch.setenv("R3_EXAMPLES_DIR", str(root.parent))
    target = tmp_path / "copy"
    create_project(target, name="copy", template=root.name, platform_ref="test")
    assert Project.load(target).spec.robot.name == "copy"
    assert Project.load(target).spec.components["base"].type == "synthetic-base"


def test_http_spec_views_and_patch(root):
    with TestClient(create_app(projects_roots=[root.parent])) as client:
        url = f"/api/projects/{root.name}/spec"
        read = client.get(url).json()
        assert read["files"]["parts/base.yaml"]["yaml"].startswith("# Base")
        result = client.patch(url, json={"sha256": read["sha256"], "patch": {"components": {"base": {"label": "HTTP"}}}})
        assert result.status_code == 200, result.text
        assert client.get(url).json()["spec"]["components"]["base"]["label"] == "HTTP"


def test_pull_hooks_and_missing_instances_preserve_good_description(root):
    from r3_model.description import check_pulled, refresh_pulled
    from r3_model.pulled import load_pulled

    publish_pull(root)
    manifest = json.loads((root / "cad/pulled/pull.json").read_text())
    assert refresh_pulled(root, manifest)["ok"]
    check_pulled(root, load_pulled(root))
    read = spec_ops.get_spec(root)
    assert spec_ops.patch_spec(root, {"components": {"extra": {"type": "synthetic-extra"}}}, read["sha256"])["ok"]
    before = contents(root)
    for action in (lambda: refresh_pulled(root, manifest), lambda: check_pulled(root, load_pulled(root))):
        with pytest.raises(PullError) as error:
            action()
        assert error.value.code == "cad_pull_missing" and "extra" in str(error.value)
        assert contents(root) == before


def test_full_native_metadata_is_mirrored_and_checked(root):
    from r3_model.description import refresh_pulled

    publish_pull(root)
    path = root / "cad/pulled/pull.json"
    data = json.loads(path.read_text())
    data["parts"]["base"].update(onshape={"document_id": "document", "version_id": "version", "element_id": "element",
        "part_id": "part", "occurrence_path": ["instance"], "configuration": "size=large"}, density=2500, volume=0.0004)
    data["mates"][0]["feature"] = "native-feature"
    text = json.dumps(data)
    path.chmod(0o644)
    path.write_text(text)
    path.chmod(0o444)
    (root / "r3.lock.yaml").write_text(yaml.safe_dump({"cad_pull": {"manifest_sha256": hashlib.sha256(text.encode()).hexdigest()}}))
    refresh_pulled(root, data)
    spec = Project.load(root).spec
    assert spec.parts is spec.components
    assert spec.parts["base"].pulled.onshape.part_id == "part"
    assert spec.parts["base"].pulled.density == 2500
    assert spec.attachments[0].feature == "native-feature"
    assert validate_pulled_spec(root).document_id == "document"


def test_agent_tracks_changes_to_included_files(root):
    session = agents.AgentSession("test", "mechanism", root, "codex", "", "test")
    session._spec_start = session._spec_last = agents._description(root)
    read = spec_ops.get_spec(root)
    result = spec_ops.patch_spec(root, {"components": {"base": {"label": "Agent"}}}, read["sha256"])
    assert result["ok"]
    agents.AgentManager()._check_spec(session)
    events = session.events.since()
    changed = next(event for event in events if event["type"] == "spec_changed")
    assert "a/parts/base.yaml" in changed["diff"] and "a/robot.yaml" not in changed["diff"]
    assert changed["sha256"] == result["sha256"]


@pytest.mark.parametrize("section,text,path", [
    ("software/system.yaml", "modules: [{name: duplicated, runs_on: base}, {name: duplicated, runs_on: link}]\n", ["software", "modules", 1, "name"]),
    ("electrical/system.yaml", "harnesses: [{id: duplicated}, {id: duplicated}]\n", ["electrical", "harnesses", 1, "id"]),
])
def test_duplicate_definitions_have_precise_source(root, section, text, path):
    (root / section).write_text(text)
    with pytest.raises(SpecError) as error:
        Project.load(root)
    assert error.value.code == "spec_duplicate_definition"
    assert error.value.errors[0]["path"] == path and error.value.errors[0]["file"] == section


def test_hash_covers_exact_file_bytes_including_line_endings(root):
    before = spec_ops.get_spec(root)
    path = root / "parts/base.yaml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    after = spec_ops.get_spec(root)
    assert after["sha256"] != before["sha256"]
    assert after["files"]["parts/base.yaml"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_invalid_component_value_is_structured_and_unchanged(root):
    read = spec_ops.get_spec(root)
    before = contents(root)
    reply = spec_ops.patch_spec(root, {"components": {"base": [1]}}, read["sha256"])
    assert reply["code"] == "spec_invalid"
    assert contents(root) == before


def test_scaffold_handles_nested_robot_and_component_index_includes(root, tmp_path, monkeypatch):
    index = root / "robot.yaml"
    data = yaml.safe_load(index.read_text())
    (root / "identity.yaml").write_text(yaml.safe_dump(data["robot"]))
    (root / "parts/index.yaml").write_text(yaml.safe_dump(data["components"]))
    data["robot"] = {"$include": "identity.yaml"}
    data["components"] = {"$include": "parts/index.yaml"}
    index.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("R3_EXAMPLES_DIR", str(root.parent))
    copied = tmp_path / "renamed"
    create_project(copied, template=root.name, platform_ref="test")
    spec = Project.load(copied).spec
    assert spec.robot.name == "renamed" and set(spec.components) == {"base", "link"}


def test_refresh_rejects_mismatched_manifest_before_writing(root):
    from r3_model.description import refresh_pulled

    publish_pull(root)
    before = contents(root)
    manifest = json.loads((root / "cad/pulled/pull.json").read_text())
    manifest["version_id"] = "different-version"
    with pytest.raises(PullError) as error:
        refresh_pulled(root, manifest)
    assert error.value.code == "cad_pull_modified"
    assert contents(root) == before


def test_check_pulled_rejects_stale_supplied_model(root):
    from dataclasses import replace
    from r3_model.description import check_pulled
    from r3_model.pulled import load_pulled

    publish_pull(root)
    refresh_pulled_spec(root)
    model = load_pulled(root)
    extra = replace(model.parts["base"], name="undefined")
    stale = replace(model, parts={**model.parts, "undefined": extra})
    with pytest.raises(PullError) as error:
        check_pulled(root, stale)
    assert error.value.code == "cad_pull_modified"


def test_undefined_module_reference_points_to_runs_on_not_module_name(root):
    path = root / "software/system.yaml"
    path.write_text("modules: [{name: controller, runs_on: missing}]\n")
    with pytest.raises(SpecError) as error:
        Project.load(root)
    assert error.value.errors[0]["file"] == "software/system.yaml"
    assert error.value.errors[0]["path"] == ["software", "modules", 0, "runs_on"]
    assert error.value.errors[0]["source_path"] == ["modules", 0, "runs_on"]


def test_layout_preservation_never_discards_significant_scalar_whitespace(root):
    read = spec_ops.get_spec(root)
    first = spec_ops.patch_spec(root, {"components": {"base": {"label": "two  spaces"}}}, read["sha256"])
    assert first["ok"]
    second = spec_ops.patch_spec(root, {"components": {"base": {"label": "two spaces"}}}, first["sha256"])
    assert second["ok"]
    assert Project.load(root).spec.components["base"].label == "two spaces"
    assert "# keep this comment" in (root / "parts/base.yaml").read_text()


@pytest.mark.parametrize("literal", [
    "-1.3877787807814457e-17", "1.2345678901234567e-101",
    "1.200E+003", "0.0001200", "1_234.50", "-0.0",
])
def test_unrelated_patch_preserves_float_literal(literal):
    from r3_model.spec_files import dump_yaml, roundtrip

    text = f"label: Before\nvalues: [{literal}]  # native evidence\n"
    loader, document = roundtrip(text)
    document["label"] = "After"
    assert dump_yaml(loader, document) == text.replace("Before", "After")


def test_changed_float_does_not_reuse_original_literal():
    from r3_model.spec_files import dump_yaml, roundtrip

    loader, document = roundtrip("value: 1.200E+003\n")
    document["value"] *= 2
    assert float(yaml.safe_load(dump_yaml(loader, document))["value"]) == 2400
