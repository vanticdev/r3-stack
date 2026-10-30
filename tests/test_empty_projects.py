"""The platform can discover an empty project root and create a robot from nothing."""
from pathlib import Path

from fastapi.testclient import TestClient

from r3_github import scaffold
from r3_model.project import Project
from r3_orchestrator import api, cli
from r3_orchestrator.api.server import create_app


def test_empty_project_root_is_an_empty_list(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    (root / "README.md").write_text("Create your first project here.\n")
    with TestClient(create_app([root], web_dist=tmp_path / "dist", doctor=lambda: {})) as client:
        response = client.get("/api/projects")
        assert response.status_code == 200
        assert response.json()["projects"] == []


def test_default_scaffold_does_not_require_an_example(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("R3_EXAMPLES_DIR", str(tmp_path / "empty"))
    target = tmp_path / "new-robot"
    scaffold.create_project(target)
    assert Project.load(target).spec.components == {}
    assert scaffold.available_templates() == ["blank"]


def test_default_creation_through_api_and_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("R3_EXAMPLES_DIR", str(tmp_path / "empty"))
    result = api.create_project("api-robot", parent=tmp_path)
    assert result["ok"] and Project.load(tmp_path / "api-robot").spec.components == {}
    assert cli.main(["create", "cli-robot", "--dir", str(tmp_path), "--json"]) == cli.EXIT_OK
    capsys.readouterr()
    assert Project.load(tmp_path / "cli-robot").spec.components == {}
