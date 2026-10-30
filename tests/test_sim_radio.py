import math
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_sim.radio import Propagation, RadioLink, knife_edge_loss_db, packet_error_rate, reflection_coefficient

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bench"
RX_HEIGHT = .4  # synthetic antenna height


@pytest.fixture(scope="module")
def link() -> RadioLink:
    project = Project.load(BENCH)
    params = dict(project.catalog.component("elrs-receiver-2g4").behavior.params)
    radio = RadioLink(project.spec.simulation.environment, params, np.random.default_rng(0))
    radio.uplink_shadow.sigma = 0.0  # deterministic mean link
    return radio


def rx_at(link: RadioLink, distance: float) -> np.ndarray:
    return np.array([link.tx_position[0] + distance, link.tx_position[1], RX_HEIGHT])


def test_rssi_ranges(link):
    rssi = {d: link.sample(rx_at(link, d)).rx_power_dbm for d in (10, 100, 1000)}
    assert -50 < rssi[10] < -25  # near free space (60 dB FSPL at 10 m) +-two-ray ripple
    assert -80 < rssi[100] < -60
    assert -120 < rssi[1000] < -100


def test_breakpoint_distance_and_slope(link):
    prop = link.propagation
    assert prop.wavelength == pytest.approx(299_792_458.0 / 2.44e9)
    d_c = prop.breakpoint_distance(link.tx_position[2], RX_HEIGHT)
    assert d_c == pytest.approx(4 * link.tx_position[2] * RX_HEIGHT / prop.wavelength)
    loss = lambda d: prop.two_ray_loss_db(link.tx_position, rx_at(link, d))  # noqa: E731
    # Beyond the breakpoint the loss approaches 40 dB/decade (vs 20 dB/decade free space).
    assert loss(2000) - loss(200) == pytest.approx(40.0, abs=2.5)
    # Asymptote 40 log d - 20 log(ht hr) at long range.
    assert loss(3000) == pytest.approx(40 * math.log10(3000) - 20 * math.log10(link.tx_position[2] * RX_HEIGHT), abs=1.5)


def test_reflection_coefficient_limits():
    eps = complex(15.0, -0.07)
    assert abs(reflection_coefficient(1e-4, eps) + 1) < 0.01  # grazing -> -1
    brewster = math.atan(1 / math.sqrt(15.0))
    assert abs(reflection_coefficient(brewster, eps, "vertical")) < 0.1
    assert abs(reflection_coefficient(brewster, eps, "horizontal")) > 0.5


def test_packet_error_waterfall(link):
    req = link.snr_required_db
    assert packet_error_rate(req, req) == pytest.approx(0.1)
    assert packet_error_rate(req + 5, req) < 1e-3
    assert packet_error_rate(req - 5, req) > 0.99
    # Sensitivity is consistent with thermal noise: N = -174 + 10 log10(812.5 kHz) + NF.
    assert link.n_thermal_dbm == pytest.approx(-174 + 10 * math.log10(812_500) + 6.0)


def test_knife_edge_obstacle():
    assert knife_edge_loss_db(-1.0) == 0.0
    assert knife_edge_loss_db(0.0) == pytest.approx(6.0, abs=0.1)  # grazing incidence ~6 dB
    prop = Propagation(2.44e9, obstacles=[{"center": [10, 0, 1.0], "size": [1, 1, 2.0]}])
    blocked = prop.obstacle_loss_db(np.array([0.0, 0, 0.5]), np.array([20.0, 0, 0.5]))
    clear = prop.obstacle_loss_db(np.array([0.0, 5, 0.5]), np.array([20.0, 5, 0.5]))
    assert blocked > 15 and clear == 0.0
