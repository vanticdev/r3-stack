"""Sionna RT propagation: validation against the closed-form two-ray model, the labeled fallback,
car-body blockage, path extrapolation, FHSS and the scene builder."""

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_sim import radio_sionna
from r3_sim.radio import ELRS_FHSS_CHANNELS, RadioLink
from r3_sim.radio_analytic import itu_ground_permittivity, two_ray_dipole_gain_db
from r3_sim.radio_scene import BodyMesh, RadioScene, material_for, scene_for_project

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"
SIONNA_OK, SIONNA_WHY = radio_sionna.sionna_status()
needs_sionna = pytest.mark.skipif(not SIONNA_OK, reason=f"Sionna RT unavailable: {SIONNA_WHY}")
F = 2.44e9
TX = np.array([-2.0, 0.0, 1.2])
H_RX = 0.178


def flat_scene(**overrides) -> RadioScene:
    values = dict(frequency=F, tx_position=TX.copy(), tx_power_dbm=20.0, tx_gain_dbi=2.15, rx_gain_dbi=2.15,
                  tx_pattern="hw_dipole", rx_pattern="hw_dipole", polarization="V", ground_material="medium_dry_ground")
    values.update(overrides)
    return RadioScene(**values)


@pytest.fixture(scope="module")
def flat(tmp_path_factory) -> "radio_sionna.SionnaPropagation":
    return radio_sionna.SionnaPropagation(flat_scene(), tmp_path_factory.mktemp("flat_scene"))


@needs_sionna
def test_sionna_matches_analytic_two_ray_on_flat_ground(flat):
    """Flat ITU medium-dry ground, no obstacles, vertical half-wave dipoles: the ray tracer must reproduce the closed-form
    two-ray field sum (same material, same patterns) from the near field through the 40 dB/decade region."""
    eps = itu_ground_permittivity("medium_dry_ground", F)
    distances = np.array([3.0, 7.0, 15.0, 40.0, 100.0, 300.0, 700.0, 1500.0])
    points = np.column_stack([TX[0] + distances, np.zeros_like(distances), np.full_like(distances, H_RX)])
    coherent, _ = flat.static_gain_db(points)
    reference = np.array([two_ray_dipole_gain_db(TX, p, F, eps) for p in points])
    assert np.max(np.abs(coherent - reference)) < 1.0, np.round(coherent - reference, 3)
    # Off-axis receivers too (dipole pattern is azimuth-symmetric).
    off = np.array([[TX[0] + 60.0, 25.0, H_RX], [TX[0] + 250.0, -80.0, H_RX]])
    assert np.allclose(flat.static_gain_db(off)[0], [two_ray_dipole_gain_db(TX, p, F, eps) for p in off], atol=1.0)
    # Two-ray regime beyond the breakpoint (4 h_t h_r / lambda ~ 7 m): ~40 dB/decade.
    far = flat.static_gain_db(np.array([[TX[0] + 150.0, 0, H_RX], [TX[0] + 1500.0, 0, H_RX]]))[0]
    assert far[0] - far[1] == pytest.approx(40.0, abs=2.5)


@needs_sionna
def test_link_channel_extrapolates_between_traces(flat):
    """Per-packet channel (cached trace + first-segment extrapolation, coherent at the carrier) equals a direct trace."""
    for d in (20.0, 180.0):
        exact = flat.static_gain_db(np.array([[TX[0] + d + 0.1, 0.0, H_RX]]))[0][0]
        extrapolated = flat.channel(np.array([TX[0] + d + 0.1, 0.0, H_RX])).gain_db  # cell center is 0.1 m away
        assert extrapolated == pytest.approx(exact, abs=0.3)


@needs_sionna
def test_car_body_blocks_paths(tmp_path):
    """A 0.3 m metal box on the car between the antenna (low, at its front face) and the handset blocks the LoS and
    the ground reflection when the car faces away from the handset, not when it faces it."""
    import trimesh

    box = trimesh.creation.box(extents=[0.3, 0.3, 0.3])
    box.apply_translation([-0.2, 0.0, 0.15])
    scene = flat_scene(body=[BodyMesh("metal", np.asarray(box.vertices), np.asarray(box.faces), ["block"])],
                       antenna_offset=np.array([0.0, 0.0, 0.12]))
    model = radio_sionna.SionnaPropagation(scene, tmp_path / "scene")
    car = np.array([TX[0] + 30.0, 0.0, 0.0])

    def gain(yaw: float) -> float:
        c, s = math.cos(yaw), math.sin(yaw)
        rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        return model.channel(car + rotation @ scene.antenna_offset, rotation).gain_db

    facing_tx, facing_away = gain(math.pi), gain(0.0)  # box behind the antenna vs between antenna and handset
    assert facing_tx - facing_away > 10.0
    assert facing_tx == pytest.approx(model.static_gain_db((car + scene.antenna_offset)[None, :])[0][0], abs=1.0)


@needs_sionna
def test_fhss_hops_change_the_coherent_channel():
    project = Project.load(RC_CAR)
    params = dict(project.catalog.component("elrs-receiver-2g4").behavior.params)
    link = RadioLink(project.spec.simulation.environment, params, np.random.default_rng(1))
    hops = [link.hop_frequency(i) for i in range(0, 4 * ELRS_FHSS_CHANNELS, link.hop_interval)]
    assert min(hops) >= 2400.4e6 and max(hops) <= 2479.4e6 + 1.0
    assert len(set(hops)) == ELRS_FHSS_CHANNELS
    assert link.hop_frequency(0) == link.hop_frequency(link.hop_interval - 1)
    assert link.backend == "sionna_rt" and not link.diagnostics


def test_analytic_fallback_is_labeled(monkeypatch):
    project = Project.load(RC_CAR)
    params = dict(project.catalog.component("elrs-receiver-2g4").behavior.params)

    def unavailable() -> None:
        raise ImportError("sionna.rt not installed")

    monkeypatch.setattr(radio_sionna, "load_sionna", unavailable)
    link = RadioLink(project.spec.simulation.environment, params, np.random.default_rng(0))
    assert link.backend == "analytic_two_ray"
    assert [d.code for d in link.diagnostics] == ["radio_propagation_fallback"]
    assert "FALLBACK" in link.diagnostics[0].message and "sionna.rt not installed" in link.diagnostics[0].message
    assert any("FALLBACK" in text for _, text in link.assumptions())
    monkeypatch.setenv("R3_RADIO_BACKEND", "analytic")
    forced = RadioLink(project.spec.simulation.environment, params, np.random.default_rng(0))
    assert forced.backend == "analytic_two_ray" and forced.diagnostics[0].code == "radio_propagation_fallback"


def test_scene_from_rc_car_spec(tmp_path):
    root = tmp_path / "rc-car"
    shutil.copytree(RC_CAR, root, ignore=shutil.ignore_patterns("build", "__pycache__"))
    project = Project.load(root)
    params = dict(project.catalog.component("elrs-receiver-2g4").behavior.params)
    scene = scene_for_project(project, "receiver", params)
    assert scene.ground_material == "medium_dry_ground" and scene.ground_custom is None
    assert [b.name for b in scene.obstacles] == ["cone_a"]
    # Authored material in robot.yaml is used verbatim; only missing materials are estimates.
    assert scene.obstacles[0].material == "plastic" and not scene.obstacles[0].material_estimated
    materials = {m.material: m for m in scene.body}
    assert "chassis" in materials["metal"].parts and "pi" in materials["metal"].parts and "deck" in materials["plastic"].parts
    body_parts = sum((m.parts for m in scene.body), [])
    assert "receiver" not in body_parts and "rx_antenna" not in body_parts  # the radiator does not shadow itself
    # Without CAD the feed point is the middle of the antenna's catalog dipole, placed by its pose.
    dipole = next(s for s in project.catalog.component(project.spec.components["rx_antenna"].type).mechanical.solids
                  if s.name == "dipole")
    position, rotation = project.graph.component_pose("rx_antenna")
    expected = position + rotation @ (np.asarray(dipole.position, dtype=float)
                                      + np.asarray(dipole.axis, dtype=float) * dipole.length / 2.0)
    assert np.allclose(scene.antenna_offset, expected) and scene.antenna_offset_source == "catalog"
    built = Project.load(RC_CAR).read_build_json("cad/cad_model.json") or {}
    if (built.get("rf") or {}).get("antenna_position"):  # the CAD-derived feed point agrees with the catalog geometry
        assert np.allclose(scene.antenna_offset, built["rf"]["antenna_position"], atol=1e-4)
    # The antenna must sit outside (above) every body part.
    assert all(m.vertices[:, 2].max() < scene.antenna_offset[2] for m in scene.body)
    (root / "build" / "cad").mkdir(parents=True)
    (root / "build" / "cad" / "cad_model.json").write_text(json.dumps({"parts": {}, "rf": {"antenna_position": [0.2, 0.05, 0.21]}}))
    cad_scene = scene_for_project(Project.load(root), "receiver", params)
    assert np.allclose(cad_scene.antenna_offset, [0.2, 0.05, 0.21]) and cad_scene.antenna_offset_source == "cad"
    assert material_for("Aluminum 6061-T6") == "metal" and material_for("PETG") == "plastic"
    assert material_for("", "battery") == "metal" and material_for("itu_concrete") == "concrete"


@needs_sionna
def test_precomputed_cells_equal_inline_traces_and_persist(tmp_path):
    """Cells traced ahead of the run in worker processes are identical to cells traced in the loop (deterministic
    solver, same body pose per cell), and a new instance of the same scene loads them from disk."""
    import trimesh

    box = trimesh.creation.box(extents=[0.3, 0.3, 0.3])
    box.apply_translation([-0.2, 0.0, 0.15])
    scene = flat_scene(body=[BodyMesh("metal", np.asarray(box.vertices), np.asarray(box.faces), ["block"])],
                       antenna_offset=np.array([0.0, 0.0, 0.12]))
    inline = radio_sionna.SionnaPropagation(scene, tmp_path / "inline")
    keys = [(int(x / 0.25), int(y / 0.25), 12, b) for x, y, b in [(20.0, 0.0, 0), (20.0, 1.0, 12), (45.0, -3.0, 6),
                                                                  (80.0, 2.0, 0), (80.25, 2.0, 0), (120.0, 0.0, -6)]]
    expected = {key: inline._trace_key(key) for key in keys}
    ahead = radio_sionna.SionnaPropagation(scene, tmp_path / "ahead")
    assert ahead.precompute(keys, workers=2) == len(keys) and ahead.precompute_workers == 2
    assert (tmp_path / "ahead" / "worker_0" / "scene.xml").exists() and (tmp_path / "ahead" / "worker_1").exists()
    for key, paths in expected.items():
        got = ahead.cache[key]
        assert np.array_equal(got.a, paths.a) and np.array_equal(got.length, paths.length) and got.los == paths.los
    ahead.save_cache()
    reloaded = radio_sionna.SionnaPropagation(scene, tmp_path / "ahead")
    assert reloaded.cache_loaded == len(keys) and set(reloaded.cache) == set(keys)
    assert reloaded.precompute(keys) == 0
