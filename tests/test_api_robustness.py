"""Server robustness: readable project_invalid errors, stale-code detection, the project cache and
``r3 serve --reload``. Uses a temporary bench copy (no build/ artifacts)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api_helpers import copy_bench  # noqa: E402
from r3_model.project import Project  # noqa: E402
from r3_orchestrator import cli  # noqa: E402
from r3_orchestrator.api import freshness, server  # noqa: E402
from r3_orchestrator.api.agents import AgentManager  # noqa: E402
from r3_orchestrator.api.errors import MAX_LISTED_ERRORS  # noqa: E402
from r3_orchestrator.api.server import create_app  # noqa: E402

PID = "bench"


@pytest.fixture
def projects(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()
    copy_bench(parent, PID)
    freshness.PROJECTS.clear()
    yield parent
    freshness.PROJECTS.clear()


def make_client(projects: Path, tmp_path: Path, **kwargs) -> TestClient:
    agents = AgentManager(executables={"claude": str(tmp_path / "missing" / "claude"), "codex": str(tmp_path / "missing" / "codex")})
    return TestClient(create_app([projects], web_dist=tmp_path / "dist", agents=agents, doctor=lambda: {"ok": True}, **kwargs))


@pytest.fixture
def client(projects, tmp_path):
    with make_client(projects, tmp_path) as c:
        yield c


def edit_spec(root: Path, change) -> None:
    path = root / "robot.yaml"
    spec = yaml.safe_load(path.read_text())
    change(spec)
    path.write_text(yaml.safe_dump(spec, sort_keys=False))


def bad_types(*components: str):
    """Make each component's catalog ``type`` a number: one ``string_type`` error per component."""
    def change(spec: dict) -> None:
        for cid in components:
            spec["components"][cid]["type"] = 7
    return change


# ----------------------------------------------------------- project_invalid
def test_bad_robot_yaml_is_a_structured_project_invalid_error(client, projects):
    edit_spec(projects / PID, bad_types("gps"))
    response = client.get(f"/api/projects/{PID}")
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"code", "message", "data"} and body["code"] == "project_invalid"
    data = body["data"]
    assert data["project"] == PID and data["exception"] == "ValidationError" and data["total"] == 1
    assert data["errors"] == [{"path": "components.gps.type", "message": "Input should be a valid string", "type": "string_type"}]
    # The message is one concise line naming the first problem; the raw pydantic dump lives in details only.
    message = body["message"]
    assert "\n" not in message and "components.gps.type: Input should be a valid string" in message and "robot.yaml" in message
    assert "input_value" not in message and "errors.pydantic.dev" not in message
    assert "validation error for RobotSpec" in data["details"] and "input_value" in data["details"]

    # Every project endpoint reports the same error, including those that load through api.* (release).
    for path in ("model", "wiring", "sim", "release"):
        other = client.get(f"/api/projects/{PID}/{path}")
        assert other.status_code == 422 and other.json()["code"] == "project_invalid", (path, other.text)
        assert other.json()["data"]["errors"][0]["path"] == "components.gps.type"


def test_many_errors_list_the_first_few_and_count_all(client, projects):
    root = projects / PID
    ids = list(yaml.safe_load((root / "robot.yaml").read_text())["components"])[:MAX_LISTED_ERRORS + 3]
    edit_spec(root, bad_types(*ids))
    data = client.get(f"/api/projects/{PID}").json()["data"]
    assert data["total"] == len(ids) and len(data["errors"]) == MAX_LISTED_ERRORS
    assert [e["path"] for e in data["errors"]] == [f"components.{cid}.type" for cid in ids[:MAX_LISTED_ERRORS]]
    assert f"{len(ids)} validation errors" in client.get(f"/api/projects/{PID}").json()["message"]


def test_project_list_carries_the_structured_error_and_other_projects_load(client, projects):
    copy_bench(projects, "good-project")
    edit_spec(projects / PID, bad_types("gps"))
    rows = {row["id"]: row for row in client.get("/api/projects").json()["projects"]}
    bad, good = rows[PID], rows["good-project"]
    assert bad["error"] == bad["error_detail"]["message"] and "components.gps.type" in bad["error"]
    assert bad["error_detail"]["code"] == "project_invalid"
    assert bad["error_detail"]["data"]["errors"][0]["path"] == "components.gps.type"
    assert good["error"] is None and good["error_detail"] is None and good["name"] and good["stages"]


def test_yaml_syntax_error_names_the_line(client, projects):
    path = projects / PID / "robot.yaml"
    path.write_text(path.read_text() + "\nbroken: [unclosed\n")
    body = client.get(f"/api/projects/{PID}").json()
    assert body["code"] == "project_invalid" and body["data"]["exception"] in ("ParserError", "ScannerError")
    assert body["data"]["errors"][0]["type"] == "yaml_syntax" and "line " in body["data"]["errors"][0]["message"]
    assert "robot.yaml is not valid YAML" in body["message"]


# ------------------------------------------------------------- stale code
def test_changed_schema_code_returns_server_code_stale(client, projects, monkeypatch):
    assert client.get(f"/api/projects/{PID}").status_code == 200
    loaded = freshness.code_files()
    edited = next(k for k in loaded if k.endswith("r3_model/spec.py"))
    monkeypatch.setattr(freshness, "code_files", lambda: {**loaded, edited: (loaded[edited][0] + 1, loaded[edited][1])})
    for url in (f"/api/projects/{PID}", f"/api/projects/{PID}/model", "/api/projects"):
        response = client.get(url)
        assert response.status_code == 503, response.text
        body = response.json()
        assert body["code"] == "server_code_stale" and "r3_model/spec.py" in body["message"] and "Restart" in body["message"]
        assert body["data"]["changed"] == [edited] and body["data"]["loaded_version"] != body["data"]["current_version"]
    assert client.get("/api/doctor").status_code == 200  # system endpoints keep working
    monkeypatch.setattr(freshness, "code_files", lambda: loaded)
    assert client.get(f"/api/projects/{PID}").status_code == 200


def test_code_guard_sees_a_touched_source_file(tmp_path, monkeypatch):
    """The real file scan: touching a schema module changes the code version."""
    package = tmp_path / "fake_schema"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "spec.py").write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(freshness, "CODE_PACKAGES", ("fake_schema",))
    guard = freshness.CodeGuard()
    guard.check()
    before = freshness.code_version()
    (package / "spec.py").write_text("X = 22\n")
    assert freshness.code_version() != before and guard.changed() == ["fake_schema/spec.py"]
    with pytest.raises(Exception) as error:
        guard.check()
    assert getattr(error.value, "code", None) == "server_code_stale"


def test_project_cache_is_keyed_on_code_version_and_inputs(projects, monkeypatch):
    loads: list[Path] = []
    original = Project.load.__func__

    def counting(cls, root):
        loads.append(Path(root))
        return original(cls, root)

    monkeypatch.setattr(Project, "load", classmethod(counting))
    root = projects / PID
    first = freshness.PROJECTS.load(root)
    assert freshness.PROJECTS.load(root) is first and len(loads) == 1  # unchanged code and inputs: cached

    monkeypatch.setattr(freshness, "code_version", lambda: "newer-code")
    assert freshness.PROJECTS.load(root) is not first and len(loads) == 2  # schema code changed: reloaded
    again = freshness.PROJECTS.load(root)
    assert len(loads) == 2

    edit_spec(root, lambda spec: spec["robot"].update(description="edited while serving"))
    reloaded = freshness.PROJECTS.load(root)
    assert reloaded is not again and len(loads) == 3 and reloaded.spec.robot.description == "edited while serving"

    (root / "catalog").mkdir(exist_ok=True)
    (root / "catalog" / "connectors.yaml").write_text("connectors: []\n")
    freshness.PROJECTS.load(root)
    assert len(loads) == 4  # a project catalog file is an input too


def test_api_serves_edits_and_fixes_without_restart(client, projects):
    root = projects / PID
    assert client.get(f"/api/projects/{PID}").status_code == 200
    edit_spec(root, bad_types("gps"))
    assert client.get(f"/api/projects/{PID}").json()["code"] == "project_invalid"
    edit_spec(root, lambda spec: spec["components"]["gps"].update(type="gps-ublox-m10"))
    assert client.get(f"/api/projects/{PID}").status_code == 200


# ------------------------------------------------------------ r3 serve
@pytest.fixture
def uvicorn_calls(monkeypatch):
    import uvicorn

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    for name in ("R3_DEV", "R3_PROJECTS_ROOTS", "R3_WEB_DIST"):
        monkeypatch.setenv(name, "")  # restored after the test; serve() writes the reload options here
    return calls


def test_serve_reload_runs_the_factory_and_watches_the_platform(uvicorn_calls, tmp_path, monkeypatch):
    assert cli.main(["serve", "--reload", "--projects-root", str(tmp_path), "--web-dist", str(tmp_path / "dist"),
                     "--port", "9001"]) == 0
    (args, kwargs), = uvicorn_calls
    assert args == (server.APP_FACTORY,) and args[0] == "r3_orchestrator.api.server:app_from_environment"
    assert kwargs["factory"] is True and kwargs["reload"] is True and kwargs["port"] == 9001
    assert kwargs["reload_includes"] == ["*.py"]
    root = server.source_checkout()
    assert root is not None
    assert kwargs["reload_dirs"] == [str(root / "packages"), str(root / "services"), str(root / "integrations")]
    # The options reach the reloading worker through the environment.
    import os

    assert os.environ["R3_PROJECTS_ROOTS"] == str(tmp_path.resolve())
    assert os.environ["R3_WEB_DIST"] == str((tmp_path / "dist").resolve())
    app = server.app_from_environment()
    assert isinstance(app, FastAPI) and app.state.registry.roots == [tmp_path.resolve()]


def test_serve_no_reload_runs_the_app_object(uvicorn_calls, tmp_path):
    assert cli.main(["serve", "--no-reload", "--projects-root", str(tmp_path)]) == 0
    (args, kwargs), = uvicorn_calls
    assert isinstance(args[0], FastAPI) and args[0].state.registry.roots == [tmp_path.resolve()]
    assert "reload" not in kwargs and "factory" not in kwargs and kwargs["port"] == 8321


def test_reload_defaults_on_in_dev_and_off_when_installed(uvicorn_calls, tmp_path, monkeypatch):
    cli.main(["serve", "--projects-root", str(tmp_path)])  # this test runs from a source checkout
    assert uvicorn_calls[-1][1].get("reload") is True
    monkeypatch.setenv("R3_DEV", "0")
    cli.main(["serve", "--projects-root", str(tmp_path)])
    assert "reload" not in uvicorn_calls[-1][1]

    monkeypatch.setattr(server, "source_checkout", lambda: None)  # an installed package
    monkeypatch.setenv("R3_DEV", "")
    cli.main(["serve", "--projects-root", str(tmp_path)])
    assert "reload" not in uvicorn_calls[-1][1]
    monkeypatch.setenv("R3_DEV", "1")
    cli.main(["serve", "--projects-root", str(tmp_path)])
    assert uvicorn_calls[-1][1].get("reload") is True


def test_reload_flags_are_exclusive_and_serve_keeps_its_signature(uvicorn_calls, tmp_path):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["serve", "--reload", "--no-reload"])
    assert cli.build_parser().parse_args(["serve"]).reload is None
    server.serve("127.0.0.1", 8400, [str(tmp_path)], str(tmp_path / "dist"))  # positional, as existing callers use it
    (args, kwargs), = uvicorn_calls
    assert isinstance(args[0], FastAPI) and kwargs == {"host": "127.0.0.1", "port": 8400, "log_level": "info"}


def test_cached_views_recheck_native_authority_and_never_use_stale_generated_cad(client, projects):
    from api_helpers import write_bench_pull, write_stage_outputs

    root = projects / PID
    write_stage_outputs(root)  # Legacy generated CAD exists, but cannot establish authority.
    missing = client.get(f"/api/projects/{PID}/model").json()
    assert missing["graph"] is None and missing["compiled"] is False
    assert client.get(f"/api/projects/{PID}/cad").json()["authority"]["code"] == "cad_pull_missing"

    write_bench_pull(root)
    ready = client.get(f"/api/projects/{PID}/model").json()
    assert ready["compiled"] is True
    assert all(component["shape"] == "mesh" for component in ready["graph"]["components"].values())
    # Rechecking a cached Project must notice the missing immutable manifest.
    (root / "cad/pulled/pull.json").unlink()
    missing_again = client.get(f"/api/projects/{PID}/model").json()
    assert missing_again["graph"] is None and missing_again["assembly"] is None
    cad = client.get(f"/api/projects/{PID}/cad").json()
    assert cad["authority"]["code"] == "cad_pull_missing" and cad["meshes"] == {}
