"""RTK GNSS in the twin: RTCM3 framing, the base station stream, the SiK correction link budget, the rover's
single/float/fixed solution, kernel wiring, firmware use of the fix quality, and the cross-track window."""

from __future__ import annotations

import math
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_sim import rtcm
from r3_sim.correction_link import GnssBaseStation, SikCorrectionLink, correction_scene, fsk_ber, gps_tow_ms
from r3_sim.devices import RtkGnssReceiver
from r3_sim.evaluation import course_progress, evaluate
from r3_sim.kernel import GNSS_EPOCH_UTC, CoSimKernel
from r3_sim.radio_scene import scene_for_project
from r3_sim.vehicle.mjcf import _behavior_params

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "examples" / "robots" / "rc-car" / "backend" / "python" / "rc_car_controller"
ORIGIN = (37.4275, -122.1697, 30.0)


# ---------------------------------------------------------------- RTCM3
def test_crc24q_check_value_and_frame_round_trip():
    assert rtcm.crc24q(b"123456789") == 0xCDE703  # CRC-24Q catalogue check value
    ecef = rtcm.ecef_from_geodetic(*ORIGIN)
    frame = rtcm.frame(rtcm.msg_1005(7, ecef))
    assert frame[0] == 0xD3 and len(frame) == 19 + 6  # 152-bit 1005 payload
    parser = rtcm.RtcmParser()
    # Garbage with a false preamble: its bogus length (211 bytes) swallows the real frame until the CRC fails,
    # then the parser resynchronises one byte on and recovers both real frames.
    noise = [(0.0, 0x55), (0.0, 0xD3), (0.0, 0x00)]
    stream = noise + [(0.1, b) for b in frame] + [(0.2, 0x00)] * 250 + [(0.3, b) for b in frame]
    messages = parser.feed(stream)
    assert [m.number for m in messages] == [1005, 1005] and parser.crc_errors == 1
    assert messages[0].station_ecef() == pytest.approx(ecef, abs=1e-4) and messages[0].t == 0.1


def test_msm7_length_and_crc_rejection():
    rng = np.random.default_rng(0)
    payload = rtcm.msm7(1077, 0, 123456, 10, 2, last=False, rng=rng)
    frame = rtcm.frame(payload)
    assert len(frame) == rtcm.msm7_bytes(10, 2) == 275
    parser = rtcm.RtcmParser()
    (message,) = parser.feed([(0.0, b) for b in frame])
    assert message.system == "GPS" and message.epoch_ms() == 123456 and message.multiple()
    broken = bytearray(frame)
    broken[40] ^= 0x10
    assert parser.feed([(0.0, b) for b in broken]) == [] and parser.crc_errors == 1


# ---------------------------------------------------------------- rover solution
def _rover(**overrides) -> RtkGnssReceiver:
    params = {"update_rate": "10 Hz", "baud": 115200, "uart2_baud": 57600, "horizontal_cep": 1.5, "rtk_cep": 0.01,
              "rtk_ppm": 1e-6, "rtk_convergence_time": "10 s", "float_cep": 0.3, "dgnss_timeout": "60 s",
              "rtk_fix_max_age": "10 s", "rtk_age_drift": 0.002, "cold_start": "25 s", **overrides}
    return RtkGnssReceiver(params, ORIGIN, np.random.default_rng(3), epoch_utc=GNSS_EPOCH_UTC,
                           gps_tow0_ms=gps_tow_ms(GNSS_EPOCH_UTC))


class _Base:
    position = [-4.0, -3.0, 1.6]
    survey_error = [0.0, 0.0, 0.0]
    rtcm = {"messages": {1005: "1 Hz", 1077: "1 Hz", 1097: "1 Hz", 1127: "1 Hz"}}


def _drive(rover: RtkGnssReceiver, base: GnssBaseStation, t0: float, t1: float, corrections: bool,
           pos: np.ndarray) -> list[tuple[str, np.ndarray]]:
    out = []
    for t in np.arange(t0 + 0.01, t1 + 1e-9, 0.01):
        stream = base.advance(t)
        if corrections:
            rover.feed_rtcm(stream)
        if rover.advance(t, pos, np.zeros(3), True):
            out.append((rover.mode, np.array(rover.last_fix) - pos[:2]))
    return out


def test_rover_single_float_fixed_transitions_and_accuracy():
    rover = _rover()
    base = GnssBaseStation(_Base(), rover.ltp, np.random.default_rng(1), 57600, GNSS_EPOCH_UTC)
    pos = np.array([30.0, 4.0, 0.2])
    fixes = _drive(rover, base, 0.0, 30.0, True, pos)
    assert [m for _, m in rover.mode_events] == ["float", "fixed"]
    t_float, t_fixed = rover.mode_events[0][0], rover.mode_events[1][0]
    assert t_fixed - t_float <= 10.0 + 1e-6  # datasheet: RTK convergence < 10 s
    fixed = np.array([e for m, e in fixes if m == "fixed"])
    baseline = math.hypot(pos[0] + 4.0, pos[1] + 3.0)
    sigma = (0.01 + 1e-6 * baseline) / 1.1774
    assert len(fixed) > 50 and np.sqrt(np.mean(fixed ** 2)) < 2.5 * sigma  # cm level, not metres
    # Corrections stop: fixed holds while they are fresh enough, then float, then single after the DGNSS timeout.
    _drive(rover, base, 30.0, 105.0, False, pos)
    modes = [(round(t, 1), m) for t, m in rover.mode_events[2:]]
    assert [m for _, m in modes] == ["float", "single"]
    last_epoch = 30.0  # the helper feeds the t = 30 s epoch whole
    assert modes[0][0] == pytest.approx(last_epoch + 10.0, abs=0.15) and modes[1][0] == pytest.approx(last_epoch + 60.0, abs=0.15)


def test_rover_ignores_uncorrected_and_unconfigured_constellations():
    rover = _rover(constellations=["Galileo"])
    base = GnssBaseStation(_Base(), rover.ltp, np.random.default_rng(1), 57600, GNSS_EPOCH_UTC)
    base.rates = {1005: 1.0, 1077: 1.0}  # GPS only, but the rover tracks Galileo
    _drive(rover, base, 0.0, 20.0, True, np.array([5.0, 0.0, 0.2]))
    assert rover.mode == "single" and rover.base_local is not None


# ---------------------------------------------------------------- correction link
def test_fsk_ber_and_packet_error_at_sensitivity():
    ebn0_ref = -2.0 * math.log(2e-3)
    assert fsk_ber(ebn0_ref) == pytest.approx(1e-3)
    per = 1.0 - (1.0 - 1e-3) ** (8 * (252 + 13))
    assert 0.85 < per < 0.9  # at sensitivity a full SiK packet mostly fails (no ECC)
    assert fsk_ber(ebn0_ref * 10.0) < 1e-20  # 10 dB above: error free


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Project:
    root = tmp_path_factory.mktemp("rtk") / "rc-car"
    shutil.copytree(ROOT / "examples" / "robots" / "rc-car", root, ignore=shutil.ignore_patterns("build", "__pycache__"))
    return Project.load(root)


def test_link_budget_loses_corrections_far_from_the_base(project):
    env = project.spec.simulation.environment
    params = dict(project.catalog.component(env.gnss_base.radio).behavior.params)
    scene = correction_scene(scene_for_project(project, "receiver", _behavior_params(project, "receiver"), include_body=False),
                             915e6, np.array([-4.0, -3.0, 1.3]), 20.0, 2.0, 2.0, np.array([0.17, -0.05, 0.17]), [])
    link = SikCorrectionLink(scene, params, params, env, {}, np.random.default_rng(0), backend="analytic")
    near = link.sample(0.0, np.array([30.0, 0.0, 0.17]), None, 915e6, 200)
    far = link.sample(0.0, np.array([3000.0, 0.0, 0.17]), None, 915e6, 200)
    assert near.per < 1e-6 and near.rx_power_dbm - link.sensitivity_dbm > 30.0
    assert far.per > 0.99


# ---------------------------------------------------------------- kernel wiring
def test_kernel_rover_is_fixed_at_start_and_reports_the_link(project, monkeypatch):
    monkeypatch.setenv("R3_RADIO_BACKEND", "analytic")
    scenario = next(s for s in project.spec.simulation.scenarios if s.id == "autonomous_course")
    kernel = CoSimKernel(project, scenario)
    assert isinstance(kernel.gps, RtkGnssReceiver) and kernel.rtk_link is not None
    assert kernel.gps.mode == "fixed" and kernel.gps.base_local == pytest.approx([-4.0, -3.0, 1.6], abs=1e-3)
    assert not any(d.code.startswith("rtk_") for d in kernel.diagnostics)
    for _ in range(1500):
        kernel.step()
    assert kernel.gps.mode == "fixed" and kernel.gps.age < 2.5
    rec = kernel.recorder.arrays()
    assert set(rec["gnss_mode"]) == {4.0}


def test_kernel_without_the_correction_harness_reports_it(project, tmp_path, monkeypatch):
    monkeypatch.setenv("R3_RADIO_BACKEND", "analytic")
    root = tmp_path / "rc-car"
    shutil.copytree(project.root, root, ignore=shutil.ignore_patterns("build"))
    text = (root / "robot.yaml").read_text()
    text = text.replace("        - {id: W_RTK_RTCM, from: B.3, to: A.2, awg: 28, color: white, signal: RTCM}\n", "")
    (root / "robot.yaml").write_text(text)
    broken = Project.load(root)
    scenario = next(s for s in broken.spec.simulation.scenarios if s.id == "autonomous_course")
    kernel = CoSimKernel(broken, scenario)
    assert kernel.rtk_link is None and kernel.gps.mode == "single"
    assert any(d.code == "rtk_corrections_not_connected" for d in kernel.diagnostics)


# ---------------------------------------------------------------- firmware
@pytest.fixture()
def firmware(monkeypatch):
    monkeypatch.syspath_prepend(str(FIRMWARE))
    for name in ("nmea", "estimator"):
        sys.modules.pop(name, None)
    import estimator
    import nmea

    return nmea, estimator


def test_firmware_parses_rtk_quality_age_and_gst(firmware):
    nmea, _ = firmware
    gga = nmea.parse(nmea.encode_sentence("GNGGA,120000.00,3725.6500000,N,12210.1820000,W,4,12,0.80,30.123,M,-32.0,M,1.2,0000").decode())
    assert gga.fix_quality == nmea.FIX_RTK_FIXED and gga.age_s == pytest.approx(1.2)
    assert gga.lat_deg == pytest.approx(37.0 + 25.65 / 60.0, abs=1e-9)
    gst = nmea.parse(nmea.encode_sentence("GNGST,120000.00,0.009,0.009,0.009,0.0,0.008,0.009,0.018").decode())
    assert isinstance(gst, nmea.Gst) and gst.sigma_lat_m == pytest.approx(0.008) and gst.sigma_lon_m == pytest.approx(0.009)


def test_estimator_uses_the_fix_sigma(firmware):
    _, estimator = firmware
    est = estimator.VehicleEstimator()
    est.update_gps_position(0.0, 0.0)  # standalone: initialised with ~1.3 m
    est.update_gps_position(1.0, 0.5, sigma=0.01)  # RTK fixed snaps the estimate
    assert est.x == pytest.approx(1.0, abs=0.01) and est.y == pytest.approx(0.5, abs=0.01) and est.sigma(0) < 0.02


# ---------------------------------------------------------------- evaluator
def test_cross_track_window_starts_on_the_first_segment():
    """The car approaches waypoint 1 from 1.9 m to the side (inside the capture circle), then tracks within 0.3 m:
    the first path segment, not the capture circle, opens the window."""
    waypoints = [[5, 0], [35, 0], [35, 15], [5, 15]]
    t = np.arange(0.0, 40.0, 0.01)
    x = np.where(t < 2.0, 3.5 + 0.75 * t, 5.0 + 4.0 * (t - 2.0))
    y = np.where(t < 2.0, -1.9 + 0.8 * t, 0.3 * np.sin(t))
    mask = x <= 35.0
    rec = {"t": t[mask], "x": x[mask], "y": y[mask], "speed": np.full(mask.sum(), 4.0)}
    progress = course_progress(np.column_stack([rec["x"], rec["y"]]), rec["t"], waypoints, True)
    assert progress.start < 0.5 and 2.0 <= progress.entered <= 2.1
    (check,) = evaluate({"max_cross_track_error_m": 1.5}, rec, {"mission": {"waypoints": waypoints, "laps": 2}})
    assert check.passed and check.measured <= 0.31 and check.data["window_start_t"] == progress.entered
