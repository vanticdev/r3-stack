"""HTTP API endpoints (FastAPI TestClient) against a temporary copy of tests/fixtures/bench."""

from __future__ import annotations

import os
import shutil

import re
import yaml

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api_helpers import copy_bench, write_sim_scenario, write_stage_outputs, write_bench_pull  # noqa: E402
from r3_orchestrator import api  # noqa: E402
from r3_orchestrator.api.agents import AgentManager  # noqa: E402
from r3_orchestrator.api.server import create_app  # noqa: E402

PID = "bench"
DOCTOR = {"ok": True, "environment": {"python": "3", "display": False}, "tools": {"mujoco": {"available": True}}, "stages": []}


@pytest.fixture(scope="module")
def built_template(tmp_path_factory):
    """bench with real assembly/model/report results plus stage hand-off files (built once per module)."""
    parent = tmp_path_factory.mktemp("template")
    root = copy_bench(parent)
    # A real model error to exercise failed-stage paths: the heartbeat GPIO binding on an unwired header pin.
    data = yaml.safe_load((root / "robot.yaml").read_text())
    data["software"]["bindings"]["heartbeat"]["pins"] = ["pi.J8.32"]
    data["simulation"]["scenarios"][1]["expect"]["min_top_speed_mps"] = 8.0
    data["simulation"]["scenarios"].append({"id": "low_battery", "duration": "1 s", "expect": {"min_cell_voltage_v": 3.0}})
    (root / "robot.yaml").write_text("# Robot frame: synthetic instrument fixture\n" + yaml.safe_dump(data, sort_keys=False))
    (root / "r3.lock.yaml").write_text(yaml.safe_dump({"cad": {"provider": "onshape", "document_id": "d1", "workspace_id": "w1", "element_id": "e1", "url": "https://cad.onshape.com/documents/d1/w/w1/e/e1"}}))
    write_bench_pull(root)
    api.validate(root, ["assembly", "model"])
    write_stage_outputs(root)
    write_sim_scenario(root, fingerprint=api.load_project(root).fingerprint())
    return root


@pytest.fixture
def projects(tmp_path, built_template):
    parent = tmp_path / "projects"
    parent.mkdir()
    shutil.copytree(built_template, parent / PID)
    return parent


@pytest.fixture
def client(projects, tmp_path):
    agents = AgentManager(executables={"claude": str(tmp_path / "missing" / "claude"), "codex": str(tmp_path / "missing" / "codex")})
    with TestClient(create_app([projects], web_dist=tmp_path / "dist", agents=agents, doctor=lambda: dict(DOCTOR))) as c:
        yield c


def error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"code", "message", "data"} and body["code"] == code, body
    return body


# ----------------------------------------------------------------- system
def test_openapi_lists_the_contract(client):
    spec = client.get("/api/openapi.json").json()
    paths = spec["paths"]
    for path, method in [("/api/doctor", "get"), ("/api/projects", "get"), ("/api/projects", "post"), ("/api/projects/{pid}", "get"),
                         ("/api/projects/{pid}/spec", "get"), ("/api/projects/{pid}/spec", "patch"),
                         ("/api/projects/{pid}/catalog", "get"), ("/api/projects/{pid}/model", "get"), ("/api/projects/{pid}/cad", "get"),
                         ("/api/projects/{pid}/wiring", "get"), ("/api/projects/{pid}/electrical", "get"), ("/api/projects/{pid}/pcb", "get"),
                         ("/api/projects/{pid}/software", "get"), ("/api/projects/{pid}/sim", "get"),
                         ("/api/projects/{pid}/sim/{scenario}", "get"), ("/api/projects/{pid}/diagnostics", "get"),
                         ("/api/projects/{pid}/artifacts/{path}", "get"), ("/api/projects/{pid}/runs", "post"), ("/api/runs/{rid}", "get"),
                         ("/api/runs/{rid}/events", "get"), ("/api/projects/{pid}/deploy", "post"), ("/api/projects/{pid}/agent", "post"),
                         ("/api/agent/{sid}/events", "get"), ("/api/agent/{sid}/cancel", "post")]:
        assert method in paths.get(path, {}), f"{method.upper()} {path} missing from OpenAPI"
    assert "ErrorBody" in spec["components"]["schemas"] and "ReleaseItem" in spec["components"]["schemas"]


def test_doctor_includes_agent_clis(client):
    body = client.get("/api/doctor").json()
    assert body["tools"]["mujoco"]["available"] is True and "checked_at" in body
    assert {a["provider"]: a["available"] for a in body["agents"]} == {"claude": False, "codex": False}
    assert all(a["detail"] for a in body["agents"])


def test_unknown_routes_and_web_fallback(client, tmp_path):
    error(client.get("/api/nope"), 404, "not_found")
    assert "web app is not built" in client.get("/").text
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>R3 app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    assert client.get("/projects/bench/wiring").text == "<html>R3 app</html>"  # SPA route
    assert client.get("/assets/app.js").text == "console.log(1)"


# --------------------------------------------------------------- projects
def test_list_projects(client):
    body = client.get("/api/projects").json()
    (row,) = body["projects"]
    assert row["id"] == PID and row["name"] == "r3-bench" and row["error"] is None
    assert row["stages"]["model"] == "failed" and row["stages"]["cad"] == "missing"
    assert row["last_run"]["status"] == "failed" and "model" in row["last_run"]["failed_stages"]
    assert "bench" in body["templates"] and "blank" in body["templates"]


def test_project_detail_with_release_matrix(client):
    body = client.get(f"/api/projects/{PID}").json()
    assert body["id"] == PID and len(body["fingerprint"]["robot_yaml_sha256"]) == 16
    assert body["lock"]["cad"]["provider"] == "onshape" and body["links"]["onshape"].startswith("https://cad.onshape.com/")
    assert body["results"]["model"]["status"] == "failed" and body["results"]["model"]["stale"] is False
    assert body["results"]["model"]["artifacts"][0]["url"].startswith(f"/api/projects/{PID}/artifacts/model/")
    assert body["report"]["schema"] == "r3/report-summary@1"
    gate = body["release"]
    items = {i["id"]: i for i in gate["items"]}
    assert gate["fabrication_ready"] is False
    assert items["requirement.max_mass"]["status"] == "verified"  # explicit pulled masses
    assert items["scenario.full_throttle.min_top_speed_mps"]["status"] == "failed"
    assert items["scenario.full_throttle.brownouts"]["status"] == "verified"
    assert items["scenario.low_battery.min_cell_voltage_v"]["status"] == "not_run"
    assert items["stage.model"]["status"] == "failed"
    assert client.get(f"/api/projects/{PID}/release").json()["counts"] == gate["counts"]


def test_broken_project_is_listed_and_reported(client, projects):
    broken = projects / "broken"
    broken.mkdir()
    (broken / "robot.yaml").write_text("schema: r3/robot@1\nrobot: {}\n")
    rows = {r["id"]: r for r in client.get("/api/projects").json()["projects"]}
    assert rows["broken"]["error"]
    error(client.get("/api/projects/broken"), 422, "project_invalid")
    error(client.get("/api/projects/missing"), 404, "project_not_found")


def test_create_project(client, projects, monkeypatch):
    created = client.post("/api/projects", json={"name": "newbot", "template": "blank"})
    assert created.status_code == 201, created.text
    assert created.json()["id"] == "newbot" and (projects / "newbot" / "robot.yaml").exists()
    error(client.post("/api/projects", json={"name": "newbot"}), 409, "project_exists")
    error(client.post("/api/projects", json={"name": "bad/name"}), 422, "create_failed")
    error(client.post("/api/projects", json={"name": "remote", "github": {"private": True}}), 428, "confirmation_required")
    assert not (projects / "remote").exists()

    from r3_github import scaffold

    calls = []
    monkeypatch.setattr(scaffold, "create_remote", lambda name, target, private=True: calls.append((name, private))
                        or {"ok": True, "url": f"https://github.com/me/{name}"})
    remote = client.post("/api/projects", json={"name": "remote", "template": "blank", "github": {"private": False}, "confirm": True})
    assert remote.status_code == 201 and remote.json()["github"]["url"] == "https://github.com/me/remote"
    assert calls == [("remote", False)]


# ------------------------------------------------------------------- spec
def test_spec_read_and_patch_keeps_comments(client, projects):
    spec = client.get(f"/api/projects/{PID}/spec").json()
    assert spec["spec"]["robot"]["name"] == "r3-bench" and "# Robot frame" in spec["yaml"]
    patched = client.patch(f"/api/projects/{PID}/spec", json={"patch": {"robot": {"description": "patched over HTTP"}},
                                                             "sha256": spec["sha256"]})
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["changed"] is True and body["preserves_comments"] is True and body["sha256"] != spec["sha256"]
    assert body["spec"]["robot"]["description"] == "patched over HTTP" and "# Robot frame" in body["yaml"]
    on_disk = (projects / PID / "robot.yaml").read_text()
    assert yaml.safe_load(on_disk)["robot"]["description"] == "patched over HTTP" and "# Robot frame" in on_disk

    stale = client.patch(f"/api/projects/{PID}/spec", json={"patch": {"robot": {"version": "9"}}, "sha256": spec["sha256"]})
    assert error(stale, 409, "spec_conflict")["data"]["current_sha256"] == body["sha256"]
    invalid = client.patch(f"/api/projects/{PID}/spec", json={"patch": {"robot": {"name": None}}, "sha256": body["sha256"]})
    assert error(invalid, 422, "spec_invalid")["data"]["errors"]
    error(client.patch(f"/api/projects/{PID}/spec", json={"sha256": "x"}), 422, "request_invalid")
    assert (projects / PID / "robot.yaml").read_text() == on_disk

    # The release gate marks evidence from the old robot.yaml as stale.
    items = {i["id"]: i for i in client.get(f"/api/projects/{PID}").json()["release"]["items"]}
    assert items["stage.model"]["status"] == "stale" and items["scenario.full_throttle.brownouts"]["status"] == "stale"


# ---------------------------------------------------------------- domains
def test_catalog(client):
    body = client.get(f"/api/projects/{PID}/catalog").json()
    assert any(c["kind"] == "motor" for c in body["components"]) and body["connectors"] and body["wires"]
    motors = client.get(f"/api/projects/{PID}/catalog", params={"kind": "motor"}).json()["components"]
    assert motors and all(c["kind"] == "motor" for c in motors)


def test_model(client):
    body = client.get(f"/api/projects/{PID}/model").json()
    assert body["graph"]["nets"] and body["components"]["battery"]["pose"]["xyz"]
    assert body["components"]["battery"]["kind"] == "battery"
    assert any(i["path"] == "model/layout.png" for i in body["images"])
    assert body["results"]["model"]["metrics"]["total_mass"] > 0


def test_cad(client):
    body = client.get(f"/api/projects/{PID}/cad").json()
    import io
    import trimesh

    assert body["authority"]["ready"] is True
    assert body["cad_model"]["source"] == {"provider": "pulled", "document_id": "synthetic-document", "version_id": "synthetic-version"}
    assert body["meshes"]["base"].startswith(f"/api/projects/{PID}/artifacts/cad/pulled-meshes/")
    response = client.get(body["meshes"]["base"])
    assert response.status_code == 200
    mesh = trimesh.load_mesh(io.BytesIO(response.content), file_type="stl")
    assert mesh.volume == pytest.approx(0.004 ** 3, rel=1e-5)
    # The stale generated cad/meshes/base.stl fixture cannot override the pull.
    assert response.content != b"solid base\nendsolid base\n"


def test_wiring(client):
    body = client.get(f"/api/projects/{PID}/wiring").json()
    assert body["routes"]["wires"]["W_BAT_P"]["awg"] == 12
    assert body["bom"] == [{"section": "wire", "harness": "supply", "item": "W_BAT_P", "awg": 12, "quantity": 1, "cut_length_mm": 79}]
    assert body["clearances"]["static"][0]["min_distance"] == 0.012 and body["clearances"]["requirement"] == pytest.approx(0.008)
    assert body["harnesses"]["supply"]["index"] == 0 and "via" in body["harnesses"]["supply"]["route"]
    assert "slack_timeline" in body["missing"]


def test_electrical(client):
    body = client.get(f"/api/projects/{PID}/electrical").json()
    (wire,) = body["wires"]
    assert wire["id"] == "W_BAT_P" and wire["continuous_ratio"] == pytest.approx(4.5 / 35.0)
    (connector,) = body["connectors"]
    assert connector["burst_ratio"] == pytest.approx(59.0 / 60.0)
    (row,) = body["table"]
    assert row["battery_current"] == 1.8 and row["rails"]["5V"] == 5.1
    assert body["power_tree"]["rails"] and body["runtime"]["runtime"] == 1800.0


def test_pcb(client):
    (board,) = client.get(f"/api/projects/{PID}/pcb").json()["boards"]
    assert board["board"] == "test-board" and board["erc"] == {"errors": 0, "warnings": 1}
    assert board["gerbers_url"].endswith("/artifacts/pcb/test-board/gerbers.zip") and board["renders"][0]["path"].endswith("top.png")
    assert board["catalog_connectors"]  # the HAT's catalog pin map, to compare with board.json


def test_software(client):
    body = client.get(f"/api/projects/{PID}/software").json()
    bindings = {b["handle"]: b for b in body["bindings"]}
    esc = bindings["esc_pwm"]
    assert esc["pin_map"][0]["node"].startswith("C:pi.") and "esc.SIG" in esc["pin_map"][0]["reaches"]
    assert esc["expects_reached"] == [{"pin": "esc.J_SIG.1", "reached": True}]
    assert body["modules"][0]["name"] == "bench_controller" and body["modules"][0]["exists"]
    (logs,) = body["firmware_logs"]
    assert logs["firmware_logs"] == [{"t": 0.5, "msg": "armed"}] and "controller started" in logs["software_log_tail"]


def test_sim_list_and_detail(client):
    scenarios = {s["id"]: s for s in client.get(f"/api/projects/{PID}/sim").json()["scenarios"]}
    assert scenarios["full_throttle"]["last"]["passed"] is False and scenarios["autonomous_course"]["last"] is None
    assert scenarios["full_throttle"]["last"]["clip_url"].endswith("renders/clip.mp4")
    body = client.get(f"/api/projects/{PID}/sim/full_throttle", params={"hz": 10}).json()
    recording = body["recording"]
    series = recording["series"]
    assert recording["hz"] == 10 and len(series["t"]) == 21 and series["speed"][5] == pytest.approx(2.0)
    assert series["rssi"][-1] is None  # NaN is not JSON
    frames = recording["frames"]
    assert len(frames["t"]) == 21 and frames["component_bodies"] == {"base": "base", "battery": "base", "arm": "arm"}
    base = frames["bodies"]["base"]
    assert base[10][0] == pytest.approx(2.0 * frames["t"][10], abs=1e-4) and len(base[0]) == 7
    assert frames["bodies"]["arm"][0][1] == pytest.approx(0.1, abs=1e-4)  # world pose via forward kinematics
    cable = frames["cables"]["cable00s0"]
    assert len(cable["points"]) == 21 and len(cable["points"][0]) == 4 and cable["rgba"] == [0.85, 0.1, 0.1, 1.0]
    assert cable["od"] == pytest.approx(0.004)
    assert cable["points"][0][-1] == pytest.approx([0.16, 0, 0.07], abs=1e-6)
    no_cables = client.get(f"/api/projects/{PID}/sim/full_throttle", params={"hz": 10, "cables": False}).json()
    assert no_cables["recording"]["frames"]["cables"] is None
    assert client.get(f"/api/projects/{PID}/sim/autonomous_course").json()["recording"] is None
    error(client.get(f"/api/projects/{PID}/sim/no_such"), 404, "scenario_not_found")


def test_diagnostics(client):
    body = client.get(f"/api/projects/{PID}/diagnostics").json()
    assert body["counts"]["error"] >= 1 and body["by_stage"]["model"]["error"] >= 1
    errors = client.get(f"/api/projects/{PID}/diagnostics", params={"severity": "error"}).json()["diagnostics"]
    assert errors and all(d["severity"] == "error" and d["stage"] and "hint" in d for d in errors)
    error(client.get(f"/api/projects/{PID}/diagnostics", params={"severity": "fatal"}), 422, "request_invalid")


def test_artifacts_are_confined_to_build(client, projects, tmp_path):
    graph = client.get(f"/api/projects/{PID}/artifacts/model/graph.json")
    assert graph.status_code == 200 and graph.headers["content-type"].startswith("application/json") and graph.json()["nets"]
    listing = client.get(f"/api/projects/{PID}/artifacts/model").json()
    assert listing["kind"] == "directory" and "model/graph.json" in listing["entries"]
    error(client.get(f"/api/projects/{PID}/artifacts/model/nope.json"), 404, "artifact_missing")
    error(client.get(f"/api/projects/{PID}/artifacts/..%2Frobot.yaml"), 403, "artifact_outside_build")
    error(client.get(f"/api/projects/{PID}/artifacts/model%2F..%2F..%2Fr3.lock.yaml"), 403, "artifact_outside_build")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    os.symlink(secret, projects / PID / "build" / "link.txt")
    error(client.get(f"/api/projects/{PID}/artifacts/link.txt"), 403, "artifact_outside_build")


# ----------------------------------------------------------------- deploy
def test_deploy_fleet_requires_confirmation_and_validation(client):
    error(client.post(f"/api/projects/{PID}/deploy", json={"target": "fleet"}), 428, "confirmation_required")
    refused = error(client.post(f"/api/projects/{PID}/deploy", json={"target": "fleet", "confirm": True}), 409, "validation_required")
    assert "model" in refused["data"]["failed_stages"]
    error(client.post(f"/api/projects/{PID}/deploy", json={"target": "moon"}), 422, "request_invalid")


def test_agent_cli_missing_is_reported(client):
    missing = error(client.post(f"/api/projects/{PID}/agent", json={"prompt": "hello", "provider": "codex"}), 503, "agent_cli_missing")
    assert missing["data"]["provider"] == "codex"
    error(client.post(f"/api/projects/{PID}/agent", json={"prompt": ""}), 422, "request_invalid")
    error(client.get("/api/agent/nope/events"), 404, "agent_session_not_found")
    error(client.get("/api/runs/nope"), 404, "run_not_found")
