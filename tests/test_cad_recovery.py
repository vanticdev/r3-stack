"""Recover unrelated synthetic build attempts without making provider requests."""
from pathlib import Path

import pytest

from r3_model.project import Project
from r3_orchestrator import mcp_server as m
from r3_onshape.recovery import build_lock


@pytest.fixture
def project(tmp_path):
    from r3_github.scaffold import create_project
    root = tmp_path / "synthetic-instrument"
    create_project(root, template="blank", platform_ref="test")
    p = Project.load(root)
    p.write_lock("cad_library_build", {"digest": "old", "document_id": "synthetic-library", "workspace_id": "w"})
    p.write_lock("cad_build_pending", {"digest": "old-robot", "document_id": "synthetic-robot", "workspace_id": "w"})
    p.write_lock("cad", {"document_id": "completed-pin"})
    p.write_lock("sourcing", {"fixture": {"catalog_id": "synthetic"}})
    return root


def test_recovery_preserves_documents_pins_journals_and_uses_revision(project):
    journal = project / "build/cad/prior.journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"synthetic":true}')
    before = (project / "r3.lock.yaml").read_bytes()
    inspected = m.r3_cad_recover(project=str(project))
    assert inspected["ok"] and not inspected["changed"]
    assert (project / "r3.lock.yaml").read_bytes() == before
    assert m.r3_cad_recover(project=str(project), expected_sha256="wrong")["code"] == "cad_recovery_conflict"
    assert not m.r3_cad_recover(project=str(project), expected_sha256=inspected["sha256"], dry_run=True)["changed"]
    assert m.r3_cad_recover(project=str(project), expected_sha256=inspected["sha256"])["changed"]
    current = Project.load(project).lock
    assert not current["cad_library_build"] and not current["cad_build_pending"]
    assert current["cad"]["document_id"] == "completed-pin"
    assert current["sourcing"]["fixture"]["catalog_id"] == "synthetic"
    assert {x["build"]["document_id"] for x in current["cad_abandoned_builds"]} == {"synthetic-library", "synthetic-robot"}
    assert journal.read_text() == '{"synthetic":true}'


def test_recovery_and_stage_refuse_a_concurrent_cad_build(project):
    from r3_onshape.stage import run
    p = Project.load(project)
    with build_lock(p):
        assert m.r3_cad_recover(project=str(project))["code"] == "cad_busy"
        result = run(Project.load(project))
        assert not result.ok and result.errors[0].code == "cad_busy"


def test_model_tool_category_is_exposed_as_existing_enum():
    tool = m.mcp._tool_manager.get_tool("r3_models_add")
    assert "other" in tool.parameters["properties"]["category"]["enum"]
    assert "structure" not in tool.parameters["properties"]["category"]["enum"]
