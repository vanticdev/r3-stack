"""Connectors stage on the bench: models, the Pi header against the official STEP, mated pairs."""

import json
import shutil
from pathlib import Path

import pytest

from r3_connectors.models import ModelUnavailable, fetch_vendor
from r3_connectors.stage import run
from r3_model.project import Project

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bench"


@pytest.fixture(scope="module")
def stage(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    shutil.copytree(BENCH, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns("build", "__pycache__", "_vendor"))
    project = Project.load(root)
    return root, run(project, render=False)


def test_connectors_json(stage):
    root, result = stage
    data = json.loads((root / "build" / "connectors" / "connectors.json").read_text())
    j8 = data["connectors"]["pi.J8"]
    assert len(j8["pins"]) == 40 and j8["pins"]["12"]["signal"] == "GPIO18"
    assert any("esc_pwm" in b for b in j8["pins"]["12"]["bindings"])
    assert "controller.PI_ESC_PWM" in j8["pins"]["12"]["net"]
    direct = [p for p in data["mated_pairs"] if p["kind"] == "direct"]
    assert direct and direct[0]["lateral_offset"] < 1e-6 and 0 <= direct[0]["axial_gap"] < 5e-4
    assert direct[0]["contact_overlap"] > 0.004  # header posts reach well into the socket contacts
    for pair in data["mated_pairs"]:
        assert pair["max_pin_lateral_error"] < 1e-6, pair
    assert not [d for d in result.diagnostics if d.code in ("connector_mate_misaligned", "connector_pose_mismatch")]
    for type_id, entry in data["types"].items():
        assert entry["glb"] and (root / "build" / "connectors" / entry["glb"]).stat().st_size > 1000, type_id


def test_pi_header_matches_official_step(stage):
    root, result = stage
    from r3_catalog import load_catalog

    try:
        fetch_vendor(load_catalog().component("raspberry-pi-5-8gb").model)
    except ModelUnavailable as error:
        pytest.skip(f"official Raspberry Pi 5 STEP unavailable: {error}")
    data = json.loads((root / "build" / "connectors" / "connectors.json").read_text())
    check = data["checks"]["pi.J8_model"]
    assert check["posts_found"] == 40 and check["max_pin_error"] < 5e-5
    assert abs(check["post_tip_z"] - check["pin_map_tip_z"]) < 5e-5
