"""2.4 GHz ExpressLRS radio link: propagation, LoRa packet errors, receiver.

Propagation (per packet), handset -> car antenna:

* **Sionna RT** ray tracing (``radio_sionna``) of the scene built from the spec
  (``radio_scene``): ITU ground, obstacle boxes, dipole antennas, the car's own
  body. The coherent multipath sum at the ELRS FHSS hop frequency gives the
  channel gain *including* small-scale fading. This is the default.
* **Analytic fallback** (``radio_analytic``): closed-form two-ray + knife edges,
  used only when Sionna RT cannot be imported (or ``R3_RADIO_BACKEND=analytic``),
  always reported by a ``radio_propagation_fallback`` diagnostic.
* Large-scale log-normal shadowing for clutter the scene does not contain
  (terrain undulation, grass, people), Gudmundson-correlated:
  S(x + dx) = rho S(x) + sqrt(1 - rho^2) sigma N(0, 1), rho = exp(-|dx| / d_corr).

Link budget: P_rx = P_tx + G_channel - S - J (G_channel includes the antenna
gains; J is the fallback knife-edge term, 0 with ray tracing). Noise:
N = -174 dBm/Hz + 10 log10(BW) + NF, combined in power with interference.
The datasheet ``sensitivity_dbm`` fixes the required SNR at the 10 % PER
point (SNR_req = sensitivity - N_thermal); PER follows a LoRa waterfall
(logistic in SNR, 10 %-90 % over ~2.6 dB).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.units import to_si

from . import crsf
from .radio_analytic import (  # noqa: F401 - re-exported for existing callers
    AnalyticPropagation, ChannelSample, Propagation, knife_edge_loss_db, reflection_coefficient,
)
from .radio_scene import RadioScene, scene_from_environment

SPEED_OF_LIGHT = 299_792_458.0
BOLTZMANN_DBM_HZ = -174.0  # kT at 290 K
#: ELRS 2.4 GHz LoRa air rates (SX1280): packet rate -> (spreading factor, bandwidth Hz).
ELRS_LORA_MODES = {500: (5, 812_500.0), 250: (6, 812_500.0), 150: (7, 812_500.0), 50: (8, 812_500.0)}
#: Semtech LoRa demodulator SNR limits (dB) by spreading factor.
LORA_SNR_LIMIT = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
#: Logistic waterfall width (dB). PER 10 % -> 90 % spans 2 ln(9) w = 4.4 w.
PER_WATERFALL_WIDTH_DB = 0.6


def thermal_noise_dbm(bandwidth_hz: float, noise_figure_db: float) -> float:
    return BOLTZMANN_DBM_HZ + 10.0 * math.log10(bandwidth_hz) + noise_figure_db


def power_sum_dbm(*levels: float) -> float:
    return 10.0 * math.log10(sum(10.0 ** (level / 10.0) for level in levels))


class Shadowing:
    """Gudmundson spatially correlated log-normal shadowing (dB)."""

    def __init__(self, sigma_db: float, decorrelation_m: float, rng: np.random.Generator) -> None:
        self.sigma = sigma_db
        self.decorrelation = decorrelation_m
        self.rng = rng
        self.value = float(rng.normal(0.0, sigma_db)) if sigma_db > 0 else 0.0
        self.last: np.ndarray | None = None

    def update(self, position: np.ndarray) -> float:
        if self.sigma <= 0:
            return 0.0
        if self.last is not None:
            moved = float(np.linalg.norm(position[:2] - self.last[:2]))
            if moved > 0.0:
                rho = math.exp(-moved / self.decorrelation)
                self.value = rho * self.value + math.sqrt(1.0 - rho * rho) * self.sigma * float(self.rng.normal())
        self.last = position.copy()
        return self.value


def packet_error_rate(snr_db: float, snr_required_db: float, width_db: float = PER_WATERFALL_WIDTH_DB) -> float:
    """Logistic LoRa waterfall with PER = 10 % at ``snr_required_db``."""
    snr50 = snr_required_db - width_db * math.log(9.0)
    x = (snr_db - snr50) / width_db
    if x > 40:
        return 0.0
    if x < -40:
        return 1.0
    return 1.0 / (1.0 + math.exp(x))


#: ExpressLRS ISM2G4 FHSS domain: 80 channels, 1 MHz apart from 2400.4 MHz (ExpressLRS FHSS.cpp).
ELRS_FHSS_START_HZ, ELRS_FHSS_SPACING_HZ, ELRS_FHSS_CHANNELS = 2400.4e6, 1.0e6, 80
#: Packets per hop (ExpressLRS 2.4 GHz LoRa rate table, FHSShopInterval; estimate for rates not listed).
ELRS_HOP_INTERVAL = {500: 4, 250: 4, 150: 4, 50: 2}


@dataclass
class LinkSample:
    rx_power_dbm: float
    path_loss_db: float  # propagation loss excluding the catalog antenna peak gains (includes pattern, obstacles, body)
    shadowing_db: float
    obstacle_db: float  # separate knife-edge term (fallback only)
    snr_db: float
    per: float
    distance: float
    fading_db: float = 0.0  # coherent multipath sum relative to its local (incoherent) mean
    frequency: float = 0.0


def select_backend(renv: dict[str, Any]) -> str:
    """``R3_RADIO_BACKEND`` env var, then ``radio_environment.propagation_backend``; default Sionna RT."""
    choice = os.environ.get("R3_RADIO_BACKEND") or str(renv.get("propagation_backend", "sionna_rt"))
    return "analytic" if choice.strip().lower() in ("analytic", "analytic_two_ray", "two_ray", "fallback") else "sionna_rt"


class RadioLink:
    """TX (pilot handset) -> RX (car receiver) uplink and telemetry downlink."""

    def __init__(self, environment: Any, receiver_params: dict[str, Any], rng: np.random.Generator,
                 scene: RadioScene | None = None, scene_dir: Path | None = None, backend: str | None = None) -> None:
        renv = dict(environment.radio_environment or {})
        p = receiver_params
        self.scene = scene if scene is not None else scene_from_environment(environment, receiver_params)
        self.tx_position = np.asarray(self.scene.tx_position, dtype=float)
        self.tx_power_dbm = float(self.scene.tx_power_dbm)
        self.tx_gain_dbi = float(self.scene.tx_gain_dbi)
        self.frequency = to_si(p.get("frequency", 2.44e9))
        self.packet_rate = to_si(p.get("packet_rate", 150.0))
        self.sensitivity_dbm = float(p.get("sensitivity_dbm", -108.0))
        self.noise_figure_db = float(p.get("noise_figure_db", 6.0))
        self.rx_gain_dbi = float(p.get("antenna_gain_dbi", 2.0))
        self.telemetry_power_dbm = float(p.get("telemetry_power_dbm", 17.0))
        self.failsafe_timeout = to_si(p.get("failsafe_timeout", 1.0))
        self.antenna_height = to_si(p.get("antenna_height_above_mount", 0.1))
        rate = int(round(self.packet_rate))
        self.spreading_factor, self.bandwidth = ELRS_LORA_MODES.get(rate, (7, 812_500.0))
        self.interference_dbm = float(renv.get("interference_dbm", -150.0))
        self.n_thermal_dbm = thermal_noise_dbm(self.bandwidth, self.noise_figure_db)
        self.noise_dbm = power_sum_dbm(self.n_thermal_dbm, self.interference_dbm)
        self.snr_required_db = self.sensitivity_dbm - self.n_thermal_dbm
        self.lora_snr_limit_db = LORA_SNR_LIMIT.get(self.spreading_factor, -7.5)
        # Previous analytic model, kept as the labeled fallback and as a plotted reference.
        self.propagation = Propagation(
            frequency=self.frequency, permittivity=float(renv.get("ground_permittivity", 15.0)),
            conductivity=float(renv.get("ground_conductivity", 0.01)), roughness=float(renv.get("ground_roughness_m", 0.01)),
            polarization=str(renv.get("polarization", "vertical")), obstacles=list(environment.obstacles or []))
        self.analytic = AnalyticPropagation(self.tx_position, self.propagation, self.tx_gain_dbi, self.rx_gain_dbi)
        self.diagnostics: list[Diagnostic] = []
        requested = backend or select_backend(renv)
        self.model: Any = self.analytic
        if requested == "sionna_rt":
            try:
                from .radio_sionna import SionnaPropagation

                directory = scene_dir or Path(os.environ.get("TMPDIR", "/tmp")) / f"r3_radio_scene_{self.scene.digest()}"
                self.model = SionnaPropagation(
                    self.scene, directory, grid_m=float(renv.get("rt_grid_m", 0.25)),
                    max_depth=int(renv.get("rt_max_depth", 3)), samples_per_src=int(renv.get("rt_samples_per_source", 200_000)))
            except Exception as exc:  # noqa: BLE001 - missing package, LLVM or variant -> labeled fallback
                self.diagnostics.append(Diagnostic.warning(
                    "radio_propagation_fallback",
                    f"Sionna RT unavailable ({type(exc).__name__}: {exc}); using the analytic two-ray FALLBACK "
                    "(no ray-traced multipath, obstacles as knife edges, no car body)",
                    subjects=["radio"], data={"sionna_available": 0.0}))
        else:
            self.diagnostics.append(Diagnostic.warning(
                "radio_propagation_fallback", "analytic two-ray propagation FALLBACK forced (R3_RADIO_BACKEND / "
                "radio_environment.propagation_backend); Sionna RT ray tracing is not used", subjects=["radio"],
                data={"sionna_available": -1.0}))
        sigma = float(renv.get("shadowing_sigma_db", 0.0))
        corr = float(renv.get("shadowing_decorrelation_m", 10.0))
        self.uplink_shadow = Shadowing(sigma, corr, rng)
        self.rng = rng
        self.hop_interval = int(p.get("fhss_hop_interval", ELRS_HOP_INTERVAL.get(rate, 4)))
        self.fhss_frequencies = ELRS_FHSS_START_HZ + ELRS_FHSS_SPACING_HZ * np.arange(ELRS_FHSS_CHANNELS)
        self.hop_sequence = rng.permutation(ELRS_FHSS_CHANNELS)

    @property
    def backend(self) -> str:
        return self.model.name

    def hop_frequency(self, slot_index: int) -> float:
        """Carrier of packet slot ``slot_index`` (pseudo-random ELRS hop sequence over the ISM band)."""
        if self.model is self.analytic:
            return self.frequency
        return float(self.fhss_frequencies[self.hop_sequence[(slot_index // self.hop_interval) % ELRS_FHSS_CHANNELS]])

    def assumptions(self) -> list[tuple[str, str]]:
        out = [
            ("radio", f"ELRS {int(self.packet_rate)} Hz = LoRa SF{self.spreading_factor} / {self.bandwidth / 1e3:.1f} kHz; "
                      f"thermal noise {self.n_thermal_dbm:.1f} dBm (NF {self.noise_figure_db} dB), with interference "
                      f"{self.noise_dbm:.1f} dBm."),
            ("radio", f"Datasheet sensitivity {self.sensitivity_dbm} dBm defines SNR_req = {self.snr_required_db:.1f} dB at 10 % PER "
                      f"(LoRa SF{self.spreading_factor} demod limit {self.lora_snr_limit_db} dB; implied implementation "
                      f"loss {self.snr_required_db - self.lora_snr_limit_db:.1f} dB)."),
            ("radio", f"Gudmundson log-normal shadowing sigma {self.uplink_shadow.sigma} dB / {self.uplink_shadow.decorrelation} m "
                      "for clutter outside the scene; downlink shares the uplink channel and shadowing (reciprocal)."),
        ]
        if self.model is not self.analytic:
            out.append(("radio", f"FHSS: {ELRS_FHSS_CHANNELS} channels from {ELRS_FHSS_START_HZ / 1e6:.1f} MHz, hop every "
                                 f"{self.hop_interval} packets, pseudo-random sequence (seeded; not the ELRS hash)."))
        return out + [("radio", text) for text in self.model.assumptions()]

    def sample(self, rx_position: np.ndarray, tx_power_dbm: float | None = None, shadow: float | None = None,
               rotation: np.ndarray | None = None, frequency: float | None = None) -> LinkSample:
        f = self.frequency if frequency is None else frequency
        channel = self.model.channel(rx_position, rotation, f)
        shadowing = self.uplink_shadow.update(rx_position) if shadow is None else shadow
        power = self.tx_power_dbm if tx_power_dbm is None else tx_power_dbm
        rx_power = power + channel.gain_db - shadowing - channel.obstacle_db
        snr = rx_power - self.noise_dbm
        return LinkSample(rx_power, self.tx_gain_dbi + self.rx_gain_dbi - channel.gain_db, shadowing, channel.obstacle_db, snr,
                          packet_error_rate(snr, self.snr_required_db), float(np.linalg.norm(rx_position - self.tx_position)),
                          channel.gain_db - channel.mean_gain_db, f)

    def mean_rx_power_dbm(self, points: np.ndarray, model: Any | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Static-scene (no body, no shadowing) RX power at car-antenna ``points``: (coherent at carrier, local mean)."""
        coherent, mean = (model or self.model).static_gain_db(points)
        return self.tx_power_dbm + coherent, self.tx_power_dbm + mean


@dataclass
class _UartByte:
    t: float
    value: int


class ElrsReceiver:
    """ELRS receiver: decodes uplink packets and writes CRSF frames to its UART TX.

    * Each received uplink packet -> one RC_CHANNELS_PACKED frame (0x16).
    * Every ``stats_interval`` packet slots -> LINK_STATISTICS (0x14) while connected.
    * LQ = % of the last 100 packet slots received (ELRS definition).
    * No packet for ``failsafe_timeout`` -> disconnected: no frames until resync
      (``sync_packets`` consecutive good packets).
    """

    def __init__(self, link: RadioLink, baud: float, rng: np.random.Generator, stats_interval: int = 10,
                 sync_packets: int = 2, boot_time: float = 0.3) -> None:
        self.link = link
        self.byte_time = 10.0 / baud  # 8N1
        self.rng = rng
        self.stats_interval = stats_interval
        self.sync_packets = sync_packets
        self.boot_time = boot_time
        self.period = 1.0 / link.packet_rate
        self.next_slot = 0.0
        self.slot_index = 0
        self.history: list[int] = []
        self.connected = True  # scenario starts with an established link
        self.consecutive = 0
        self.last_packet_t = 0.0
        self.powered_since: float | None = 0.0 - boot_time
        self.tx_free_t = 0.0
        self.last_sample: LinkSample | None = None
        self.last_rssi = -130
        self.last_snr = 0
        self.lq = 100
        self.frames_rc = 0
        self.rc_frame_times: list[float] = []
        self.link_lost_events: list[float] = []
        self.packets_ok = 0
        self.packets_lost = 0

    def _emit(self, t: float, data: bytes, out: list[tuple[float, int]]) -> None:
        start = max(t, self.tx_free_t)
        for k, byte in enumerate(data):
            out.append((start + (k + 1) * self.byte_time, byte))
        self.tx_free_t = start + len(data) * self.byte_time

    def advance(self, t_end: float, rx_position: np.ndarray, channels_us: list[float], powered: bool,
                rotation: np.ndarray | None = None) -> list[tuple[float, int]]:
        """Process packet slots up to ``t_end``; returns (arrival time, byte) pairs on the RX->Pi UART.
        ``rotation`` (car body to world) poses the car body in the ray-traced scene."""
        out: list[tuple[float, int]] = []
        if not powered:
            self.powered_since = None
            self.connected = False
            self.history.clear()
            self.next_slot = t_end
            return out
        if self.powered_since is None:
            self.powered_since = t_end
            self.next_slot = t_end + self.boot_time
        while self.next_slot <= t_end:
            t = self.next_slot
            self.next_slot += self.period
            self.slot_index += 1
            frequency = self.link.hop_frequency(self.slot_index)
            sample = self.link.sample(rx_position, rotation=rotation, frequency=frequency)
            self.last_sample = sample
            received = self.rng.random() >= sample.per
            self.history.append(1 if received else 0)
            if len(self.history) > 100:
                del self.history[0]
            self.lq = int(round(100.0 * sum(self.history) / len(self.history)))
            if received:
                self.packets_ok += 1
                self.last_packet_t = t
                self.last_rssi = int(round(sample.rx_power_dbm + self.rng.normal(0.0, 1.0)))
                self.last_snr = int(round(sample.snr_db))
                self.consecutive += 1
                if not self.connected and self.consecutive >= self.sync_packets:
                    self.connected = True
                if self.connected:
                    self._emit(t, crsf.rc_channels_frame(channels_us), out)
                    self.frames_rc += 1
                    self.rc_frame_times.append(t)
            else:
                self.packets_lost += 1
                self.consecutive = 0
                if self.connected and t - self.last_packet_t >= self.link.failsafe_timeout:
                    self.connected = False
                    self.link_lost_events.append(self.last_packet_t)
            if self.connected and self.slot_index % self.stats_interval == 0:
                down = self.link.sample(rx_position, tx_power_dbm=self.link.telemetry_power_dbm, shadow=sample.shadowing_db,
                                       rotation=rotation, frequency=frequency)
                stats = crsf.LinkStatistics(
                    uplink_rssi_ant1_dbm=self.last_rssi, uplink_rssi_ant2_dbm=self.last_rssi, uplink_link_quality=self.lq,
                    uplink_snr_db=self.last_snr, active_antenna=0, rf_mode=crsf.ELRS_RF_MODE.get(int(round(self.link.packet_rate)), 2),
                    uplink_tx_power=crsf.tx_power_enum(self.link.tx_power_dbm),
                    downlink_rssi_dbm=int(round(down.rx_power_dbm)), downlink_link_quality=int(round(100 * (1 - down.per))),
                    downlink_snr_db=int(round(down.snr_db)))
                self._emit(t, stats.frame(), out)
        return out
