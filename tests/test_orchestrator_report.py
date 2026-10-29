"""Report stage and the web dashboard's JSON loading."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from orchestrator_helpers import ROOT, make_project
from r3_orchestrator import api

WEB_TEST = ROOT / "apps" / "web" / "tests" / "loader.test.mjs"


@pytest.fixture
def built(tmp_path):
    root = make_project(tmp_path)
    # A scenario result and an extra plot, as the sim stage would write them.
    scenario = root / "build" / "sim" / "full_throttle"
    scenario.mkdir(parents=True)
    (scenario / "summary.json").write_text(json.dumps({"scenario": "full_throttle", "passed": False, "top_speed_mps": 7.4,
                                                       "checks": {"min_top_speed_mps": {"value": 7.4, "limit": 8.0, "passed": False}}}))
    shutil.copy(ROOT / "apps" / "web" / "tests" / "fixture-build" / "report" / "summary.json", scenario / "not-an-image.json")
    api.validate(root, ["model"])
    return root


def test_summary_json(built):
    summary = json.loads((built / "build" / "report" / "summary.json").read_text())
    assert summary["schema"] == "r3/report-summary@1" and summary["ok"] is False
    assert summary["counts"]["error"] == sum(d["severity"] == "error" for d in summary["diagnostics"])
    assert summary["artifacts"]["graph"] == "model/graph.json" and summary["artifacts"]["routes"] is None
    assert "model/layout.png" in summary["artifacts"]["images"]
    (scenario,) = summary["scenarios"]
    assert scenario["id"] == "full_throttle" and scenario["passed"] is False and scenario["metrics"]["top_speed_mps"] == 7.4
    stages = {s["stage"]: s["status"] for s in summary["stages"]}
    assert stages["model"] == "failed" and stages["cad"] == "missing"
    assert any(a["stage"] == "model" for a in summary["assumptions"])


def test_index_html_is_self_contained(built):
    html = (built / "build" / "report" / "index.html").read_text()
    assert html.startswith("<!doctype html>") and "component_interference" in html
    assert "data:image/png;base64," in html  # layout.png embedded
    assert "full_throttle" in html and "Assumptions" in html
    assert "<script" not in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("source", ["fixture", "generated"])
def test_web_loader(built, source):
    env = {**os.environ}
    if source == "generated":
        env.update(R3_BUILD_DIR=str(built / "build"), R3_EXPECT_ROUTES="0")
    out = subprocess.run(["node", "--test", str(WEB_TEST)], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    if source == "generated":
        scene = json.loads(next(line for line in out.stdout.splitlines() if line.startswith('{"components"')))
        assert scene["components"] == 23 and scene["wires"] == 30 and scene["routed"] is False
        assert {"esc", "standoff_rl", "motor", "standoff_rr", "pi", "hat"} <= set(scene["flagged"])
