"""Pipeline runs over HTTP: worker processes, SSE event stream, queuing, cancellation (fake stages)."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api_helpers import copy_bench, parse_sse, sse  # noqa: E402
from r3_orchestrator.api.runs import RunManager  # noqa: E402
from r3_orchestrator.api.server import create_app  # noqa: E402

PID = "bench"


@pytest.fixture
def projects(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()
    copy_bench(parent)
    return parent


def make_client(projects, overrides):
    return TestClient(create_app([projects], runs=RunManager(stage_overrides=overrides), doctor=lambda: {"ok": True}))


def test_run_streams_stage_events(projects, monkeypatch):
    monkeypatch.setenv("R3_FAKE_STAGE_SLEEP", "0")
    with make_client(projects, {"model": "api_fake_stage"}) as client:
        created = client.post(f"/api/projects/{PID}/runs", json={"stages": ["model"]})
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        events = sse(client, created.json()["events_url"])
        types = [e["type"] for e in events]
        assert types[:3] == ["run_queued", "run_started", "stage_started"] and types[-1] == "run_finished"
        assert [e["stage"] for e in events if e["type"] == "stage_started"] == ["model", "report"]
        logs = [e for e in events if e["type"] == "log"]
        assert any(e["line"] == "fake stage: checking r3-bench" and e["stream"] == "stdout" and e["stage"] == "model" for e in logs)
        assert any("fake stage logging works" in e["line"] and e["stream"] == "logging" for e in logs)
        assert [e["message"] for e in events if e["type"] == "progress"] == ["halfway"]
        (diagnostic,) = [e for e in events if e["type"] == "diagnostic"]
        assert diagnostic["diagnostic"]["code"] == "fake_warning" and diagnostic["diagnostic"]["data"] == {"value": 1.5}
        finished = {e["stage"]: e for e in events if e["type"] == "stage_finished"}
        assert finished["model"]["status"] == "ok" and finished["model"]["metrics"] == {"answer": 42}
        assert finished["model"]["counts"]["warning"] == 1
        final = events[-1]
        assert final["status"] == "succeeded" and final["ok"] is True and final["release"]["fabrication_ready"] is False
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

        status = client.get(f"/api/runs/{run_id}").json()
        assert status["status"] == "succeeded" and status["result"]["ok"] is True and status["finished_at"]
        # The result is persisted like any CLI run, so every view sees it.
        assert (projects / PID / "build" / "model" / "fake.txt").exists()
        detail = client.get(f"/api/projects/{PID}").json()
        assert detail["results"]["model"]["metrics"] == {"answer": 42}
        # Replays: from the start again, and resumed after a sequence number.
        assert [e["type"] for e in sse(client, created.json()["events_url"])] == types
        resumed = sse(client, created.json()["events_url"], headers={"Last-Event-ID": str(len(events) - 2)})
        assert [e["seq"] for e in resumed] == [len(events) - 1, len(events)]
        assert client.get(f"/api/projects/{PID}/runs").json()[0]["run_id"] == run_id


def test_crashing_stage_fails_the_run_not_the_server(projects):
    with make_client(projects, {"model": "api_fake_crash_stage"}) as client:
        run = client.post(f"/api/projects/{PID}/runs", json={"stages": ["model"]}).json()
        events = sse(client, run["events_url"])
        (diagnostic,) = [e for e in events if e["type"] == "diagnostic" and e["stage"] == "model"]
        assert diagnostic["diagnostic"]["code"] == "stage_crashed"
        assert events[-1]["status"] == "failed" and events[-1]["counts"]["error"] == 1


def test_runs_are_serialized_per_project_and_cancellable(projects, monkeypatch):
    monkeypatch.setenv("R3_FAKE_STAGE_SLEEP", "60")
    with make_client(projects, {"model": "api_fake_stage"}) as client:
        first = client.post(f"/api/projects/{PID}/runs", json={"stages": ["model"]}).json()
        second = client.post(f"/api/projects/{PID}/runs", json={"stages": ["model"]}).json()
        deadline = time.time() + 60
        while client.get(f"/api/runs/{first['run_id']}").json()["current_stage"] != "model" and time.time() < deadline:
            time.sleep(0.1)
        assert client.get(f"/api/runs/{second['run_id']}").json()["status"] == "queued"
        started = time.time()
        assert client.post(f"/api/runs/{second['run_id']}/cancel").status_code == 200
        client.post(f"/api/runs/{first['run_id']}/cancel")
        events = sse(client, first["events_url"])
        assert events[-1]["type"] == "run_finished" and events[-1]["status"] == "cancelled"
        assert time.time() - started < 30
        assert sse(client, second["events_url"])[-1]["status"] == "cancelled"
        assert client.get(f"/api/runs/{second['run_id']}").json()["started_at"] is None


def test_run_request_validation_and_deploy_sim(projects):
    with make_client(projects, {"sim": "api_fake_stage"}) as client:
        bad = client.post(f"/api/projects/{PID}/runs", json={"stages": ["nope"]})
        assert bad.status_code == 422 and bad.json()["code"] == "usage_error"
        deployed = client.post(f"/api/projects/{PID}/deploy", json={"target": "sim", "scenarios": ["full_throttle"]})
        assert deployed.status_code == 200 and deployed.json()["target"] == "sim"
        events = sse(client, deployed.json()["events_url"])
        assert events[0]["kind"] == "deploy_sim" and events[0]["scenarios"] == ["full_throttle"]
        assert [e["stage"] for e in events if e["type"] == "stage_started"] == ["sim", "report"]
        assert events[-1]["status"] == "succeeded"


def test_parse_sse_handles_heartbeats():
    lines = ["retry: 2000", "", ": keep-alive", "", "id: 1", "event: run_finished", 'data: {"seq": 1, "type": "run_finished"}', ""]
    assert parse_sse(iter(lines)) == [{"seq": 1, "type": "run_finished"}]


def test_real_model_run_reports_missing_pull(projects):
    with make_client(projects, {}) as client:
        response = client.post(f"/api/projects/{PID}/runs", json={"stages": ["model"]})
        assert response.status_code == 202, response.text
        events = sse(client, response.json()["events_url"])
        diagnostics = [event["diagnostic"] for event in events if event["type"] == "diagnostic"]
        assert [diagnostic["code"] for diagnostic in diagnostics] == ["cad_pull_missing"]
        assert events[-1]["status"] == "failed" and events[-1]["counts"]["error"] == 1
        assert not (projects / PID / "build/model/graph.json").exists()
