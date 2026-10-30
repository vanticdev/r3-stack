"""Connectors stage on explicit native bench coupons: meshes, posts and mated pairs."""

import json
import shutil
from pathlib import Path

import pytest

from r3_connectors.stage import run
from r3_model.project import Project

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bench"


@pytest.fixture(scope="module")
def stage(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    shutil.copytree(BENCH, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns("build", "__pycache__", "_vendor"))
    from pulled_fixture import instrumentation_pull, revise_project
    from connector_native_fixture import box, compound, add_cables_to_pull
    project = instrumentation_pull(root)
    # Independently dimensioned 2x20, 2.54 mm pitch coupon. These are actual
    # STEP solids, not a catalog-generated connector or a vendor download.
    posts = [box((.64,.64,6), (-10 - 24.13 + 2.54 * col, 24.5 + row, 6.876))
             for col in range(20) for row in (-1.27,1.27)]
    project = revise_project(project, shapes={"pi": compound([box((85,56,1.6), (0,0,-.8)), *posts])})
    add_cables_to_pull(project)
    return root, run(Project.load(root), render=False)


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
        assert entry["glb"] is None and entry["pins"], type_id
    from r3_model.pulled import load_pulled
    native = load_pulled(root)
    assert set(data["components"]) == set(native.parts)
    for name, entry in data["components"].items():
        assert entry["source"] == "pulled", name
        assert entry["glb"] and (root / "build" / "connectors" / entry["glb"]).stat().st_size > 1000, name


def test_header_matches_explicit_native_step(stage):
    root, result = stage
    data = json.loads((root / "build" / "connectors" / "connectors.json").read_text())
    check = data["checks"]["pi.J8_model"]
    assert check["posts_found"] == 40 and check["max_pin_error"] < 5e-5
    assert abs(check["post_tip_z"] - check["pin_map_tip_z"]) < 5e-5
