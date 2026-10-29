"""RTK base station and its RTCM3 correction radio link (SiK 915 MHz) to the rover.

Base station (``simulation.environment.gnss_base``): a ZED-F9P-class receiver
in fixed-base mode at its configured (surveyed) coordinates streams RTCM3 on
its UART into a SiK telemetry radio: 1005 (ARP) and MSM7 per constellation at
the configured rates, MSMs of one epoch back to back with the multiple-message
bit set on all but the last. Output starts ``BASE_OUTPUT_LATENCY`` after the
epoch, at the radio's serial baud.

SiK link (transparent serial over FHSS/TDM GFSK, SiK firmware):

* TDM: the two radios alternate transmit windows; one window carries up to
  ``max_packet_bytes`` of queued serial data in one packet. Window length =
  overhead + max packet airtime; the TDM round is two windows (estimate of the
  SiK schedule; its real window width is derived from the same quantities).
* Channel: handset-independent propagation at the hop frequency (902-928 MHz,
  ``hop_channels``) from the base radio antenna to the rover radio antenna:
  Sionna RT ray tracing of the same scene as the ELRS link (ground, obstacles,
  car body posed per trace) at 915 MHz, or the analytic two-ray fallback (always
  reported by ``correction_link_propagation_fallback``), plus Gudmundson
  log-normal shadowing (its own realisation).
* Packet errors: non-coherent binary FSK, BER = 0.5 exp(-Eb/N0 / 2). The
  datasheet sensitivity (at ``air_rate``) is the power where BER =
  ``sensitivity_ber``; Eb/N0 scales 1:1 with received power above it and drops
  by the noise rise from interference, 10 log10(1 + I / N_thermal), with
  N_thermal = -174 dBm/Hz + 10 log10(air_rate) + NF. No ECC (SiK default), so a
  packet is lost on any bit error: PER = 1 - (1 - BER)^(8 (header + payload)).
  Lost packets are not retransmitted; the rover's RTCM CRC rejects the broken
  frames.
"""

from __future__ import annotations

import collections
import dataclasses
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.units import to_si

from . import rtcm
from .radio import Shadowing, power_sum_dbm
from .radio_analytic import AnalyticPropagation, Propagation
from .radio_scene import RadioScene

BASE_OUTPUT_LATENCY = 0.05  # s from the observation epoch to the first RTCM byte (estimate)
SIK_HEADER_BYTES = 13  # preamble 5 + sync 2 + header/length 2 + CRC 2 + TDM trailer 2 (SiK packet framing, estimate)
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
GPS_UTC_LEAP_S = 18


def gps_tow_ms(utc: datetime) -> int:
    """GPS time of week (ms) of a UTC instant (GPST = UTC + 18 s since 2017)."""
    seconds = (utc - GPS_EPOCH).total_seconds() + GPS_UTC_LEAP_S
    return int(round((seconds % 604800.0) * 1000.0))


def _rate(value: Any) -> float:
    return float(to_si(value))


class GnssBaseStation:
    """RTCM3 stream of a fixed base, as (time, byte) on the base radio's serial input."""

    def __init__(self, base: Any, ltp: Any, rng: np.random.Generator, serial_baud: int, epoch_utc: datetime) -> None:
        self.position = np.asarray(base.position, dtype=float)
        self.survey_error = np.asarray(base.survey_error, dtype=float)
        configured = self.position + self.survey_error  # coordinates the base was told it sits at
        lat, lon, alt = ltp.to_geodetic(*configured)
        self.ecef = rtcm.ecef_from_geodetic(lat, lon, alt)
        cfg = dict(base.rtcm or {})
        messages = cfg.get("messages") or {1005: "0.2 Hz", 1077: "1 Hz", 1097: "1 Hz", 1127: "1 Hz"}
        self.rates = {int(k): _rate(v) for k, v in messages.items()}
        self.satellites = {**rtcm.DEFAULT_SATELLITES, **(cfg.get("satellites") or {})}
        self.signals = int(cfg.get("signals", rtcm.DEFAULT_SIGNALS))
        self.station_id = int(cfg.get("station_id", 0))
        self.byte_time = 10.0 / serial_baud
        self.rng = rng
        self.tow0_ms = gps_tow_ms(epoch_utc)
        self.next_epoch = 1.0  # first whole second after start
        self.tx_free_t = -math.inf  # serial line idle
        self.bytes_sent = 0

    def _due(self, number: int, epoch_index: int) -> bool:
        rate = self.rates[number]
        every = max(1, int(round(1.0 / rate))) if rate > 0 else 0
        return every > 0 and epoch_index % every == 0

    def epoch_messages(self, t: float) -> list[bytes]:
        index = int(round(t))
        out = []
        if 1005 in self.rates and self._due(1005, index):
            out.append(rtcm.frame(rtcm.msg_1005(self.station_id, self.ecef)))
        msms = [n for n in sorted(self.rates) if n in rtcm.MSM_SYSTEM and self._due(n, index)]
        tow = self.tow0_ms + int(round(t * 1000.0))
        for k, number in enumerate(msms):
            system = rtcm.MSM_SYSTEM[number]
            epoch = tow - 14000 if system == "BeiDou" else tow  # BDT = GPST - 14 s
            payload = rtcm.msm7(number, self.station_id, epoch, int(self.satellites.get(system, 8)), self.signals,
                                last=k == len(msms) - 1, rng=self.rng)
            out.append(rtcm.frame(payload))
        return out

    def advance(self, t_end: float) -> list[tuple[float, int]]:
        out: list[tuple[float, int]] = []
        while self.next_epoch <= t_end:
            t = self.next_epoch
            self.next_epoch += 1.0
            start = max(t + BASE_OUTPUT_LATENCY, self.tx_free_t)
            data = b"".join(self.epoch_messages(t))
            for k, byte in enumerate(data):
                out.append((start + (k + 1) * self.byte_time, byte))
            self.tx_free_t = start + len(data) * self.byte_time
            self.bytes_sent += len(data)
        return out


@dataclasses.dataclass
class PacketSample:
    t: float
    rx_power_dbm: float
    snr_db: float
    ebn0_db: float
    per: float
    received: bool
    distance: float
    frequency: float


def fsk_ber(ebn0_linear: float) -> float:
    """Non-coherent binary FSK: BER = 0.5 exp(-Eb/N0 / 2)."""
    return 0.5 * math.exp(-0.5 * max(ebn0_linear, 0.0))


class SikCorrectionLink:
    """Base radio -> rover radio, one direction (corrections); (time, byte) out of the rover radio's serial TX."""

    def __init__(self, scene: RadioScene, base_params: dict[str, Any], rover_params: dict[str, Any], environment: Any,
                 overrides: dict[str, Any], rng: np.random.Generator, scene_dir: Path | None = None,
                 backend: str | None = None) -> None:
        renv = dict(environment.radio_environment or {})
        self.params = {**base_params, **overrides}
        p = self.params
        self.frequency = to_si(p.get("frequency", 915e6))
        band = [to_si(v) for v in p.get("band", [902e6, 928e6])]
        self.hop_frequencies = np.linspace(band[0], band[1], int(p.get("hop_channels", 50)) + 2)[1:-1]
        self.tx_power_dbm = float(p.get("tx_power_dbm", 20.0))
        self.air_rate = float(to_si(p.get("air_rate", 64000)))
        self.sensitivity_dbm = float(rover_params.get("sensitivity_dbm", p.get("sensitivity_dbm", -105.0)))
        self.sensitivity_ber = float(rover_params.get("sensitivity_ber", p.get("sensitivity_ber", 1e-3)))
        self.noise_figure_db = float(rover_params.get("noise_figure_db", 6.0))
        self.serial_baud = int(rover_params.get("serial_baud", 57600))
        self.max_packet = int(p.get("max_packet_bytes", 252))
        overhead = to_si(p.get("tdm_overhead_s", 0.004))
        self.window = overhead + (self.max_packet + SIK_HEADER_BYTES) * 8.0 / self.air_rate
        self.round = 2.0 * self.window
        self.n_thermal_dbm = -174.0 + 10.0 * math.log10(self.air_rate) + self.noise_figure_db
        interference = overrides.get("interference_dbm")
        if interference is None:
            # The environment's interference level is given in the ELRS 812.5 kHz channel; assume a flat
            # spectral density and scale it to this receiver's bandwidth (estimate).
            interference = float(renv.get("interference_dbm", -150.0)) + 10.0 * math.log10(self.air_rate / 812_500.0)
        self.interference_dbm = float(interference)
        self.noise_rise_db = power_sum_dbm(self.n_thermal_dbm, self.interference_dbm) - self.n_thermal_dbm
        self.ebn0_ref_db = 10.0 * math.log10(-2.0 * math.log(2.0 * self.sensitivity_ber))
        self.scene = scene
        self.diagnostics: list[Diagnostic] = []
        self.analytic = AnalyticPropagation(
            scene.tx_position, Propagation(frequency=self.frequency, permittivity=float(renv.get("ground_permittivity", 15.0)),
                                           conductivity=float(renv.get("ground_conductivity", 0.01)),
                                           roughness=float(renv.get("ground_roughness_m", 0.01)),
                                           polarization=str(renv.get("polarization", "vertical")),
                                           obstacles=list(environment.obstacles or [])),
            scene.tx_gain_dbi, scene.rx_gain_dbi)
        self.model: Any = self.analytic
        from .radio import select_backend

        if (backend or select_backend(renv)) == "sionna_rt":
            try:
                from .radio_sionna import SionnaPropagation

                directory = scene_dir or Path(os.environ.get("TMPDIR", "/tmp")) / f"r3_rtk_scene_{scene.digest()}"
                # Grid scaled with the wavelength (0.25 m at 2.44 GHz is ~2 lambda; keep the same cells per lambda).
                grid = float(renv.get("rt_grid_m", 0.25)) * 2.44e9 / self.frequency
                self.model = SionnaPropagation(scene, directory, grid_m=grid, max_depth=int(renv.get("rt_max_depth", 3)),
                                               samples_per_src=int(renv.get("rt_samples_per_source", 200_000)))
            except Exception as exc:  # noqa: BLE001 - missing package or variant -> labeled fallback
                self.diagnostics.append(Diagnostic.warning(
                    "correction_link_propagation_fallback",
                    f"Sionna RT unavailable for the RTK correction link ({type(exc).__name__}: {exc}); analytic two-ray FALLBACK",
                    subjects=["gnss_base"], data={"sionna_available": 0.0}))
        else:
            self.diagnostics.append(Diagnostic.warning(
                "correction_link_propagation_fallback", "analytic two-ray propagation FALLBACK forced for the RTK correction link",
                subjects=["gnss_base"], data={"sionna_available": -1.0}))
        self.shadow = Shadowing(float(renv.get("shadowing_sigma_db", 0.0)), float(renv.get("shadowing_decorrelation_m", 10.0)), rng)
        self.rng = rng
        self.hop_sequence = rng.permutation(len(self.hop_frequencies))
        self.queue: collections.deque[tuple[float, int]] = collections.deque()
        self.next_window = 0.0
        self.windows = 0
        self.rx_free_t = -math.inf
        self.samples: list[PacketSample] = []
        self.packets_sent = 0
        self.packets_lost = 0
        self.bytes_delivered = 0

    @property
    def backend(self) -> str:
        return self.model.name

    def offer(self, items: list[tuple[float, int]]) -> None:
        """Serial bytes into the base radio (time of arrival, value)."""
        self.queue.extend(items)

    def sample(self, t: float, rx_position: np.ndarray, rotation: np.ndarray | None, frequency: float, payload: int) -> PacketSample:
        channel = self.model.channel(rx_position, rotation, frequency)
        shadowing = self.shadow.update(rx_position)
        rx_power = self.tx_power_dbm + channel.gain_db - shadowing - channel.obstacle_db
        ebn0_db = self.ebn0_ref_db + (rx_power - self.sensitivity_dbm) - self.noise_rise_db
        ber = fsk_ber(10.0 ** (ebn0_db / 10.0))
        per = 1.0 - (1.0 - ber) ** (8 * (payload + SIK_HEADER_BYTES))
        received = bool(self.rng.random() >= per)
        snr = rx_power - power_sum_dbm(self.n_thermal_dbm, self.interference_dbm)
        return PacketSample(t, rx_power, snr, ebn0_db, per, received, float(np.linalg.norm(rx_position - self.scene.tx_position)),
                            frequency)

    def advance(self, t_end: float, rx_position: np.ndarray, rotation: np.ndarray | None, powered: bool) -> list[tuple[float, int]]:
        out: list[tuple[float, int]] = []
        while self.next_window <= t_end:
            t = self.next_window
            self.next_window += self.round
            self.windows += 1
            chunk: list[int] = []
            while self.queue and self.queue[0][0] <= t and len(chunk) < self.max_packet:
                chunk.append(self.queue.popleft()[1])
            if not chunk:
                continue
            self.packets_sent += 1
            frequency = float(self.hop_frequencies[self.hop_sequence[self.windows % len(self.hop_sequence)]])
            sample = self.sample(t, rx_position, rotation, frequency, len(chunk))
            self.samples.append(sample)
            if not (sample.received and powered):
                self.packets_lost += 1
                continue
            arrival = t + self.window  # end of the packet on air
            start = max(arrival, self.rx_free_t)
            for k, byte in enumerate(chunk):
                out.append((start + (k + 1) * 10.0 / self.serial_baud, byte))
            self.rx_free_t = start + len(chunk) * 10.0 / self.serial_baud
            self.bytes_delivered += len(chunk)
        return out

    def assumptions(self) -> list[tuple[str, str]]:
        return [
            ("radio", f"RTK correction link: SiK {self.frequency / 1e6:.0f} MHz GFSK {self.air_rate / 1e3:.0f} kbit/s, "
                      f"{self.tx_power_dbm:.0f} dBm, {len(self.hop_frequencies)} hop channels, TDM window {self.window * 1e3:.1f} ms "
                      f"(round {self.round * 1e3:.1f} ms), no ECC/retransmission; datasheet sensitivity {self.sensitivity_dbm} dBm at "
                      f"BER {self.sensitivity_ber:g}; non-coherent FSK BER curve; thermal noise {self.n_thermal_dbm:.1f} dBm, "
                      f"interference {self.interference_dbm:.1f} dBm (noise rise {self.noise_rise_db:.1f} dB); propagation "
                      f"{self.model.label}."),
        ] + [("radio", f"RTK correction link propagation: {text}") for text in self.model.assumptions()]

    def stats(self) -> dict[str, Any]:
        powers = [s.rx_power_dbm for s in self.samples]
        return {"rtk_link_backend": self.backend, "rtk_link_packets": self.packets_sent, "rtk_link_packets_lost": self.packets_lost,
                "rtk_link_min_rx_dbm": round(min(powers), 1) if powers else None,
                "rtk_link_margin_db": round(min(powers) - self.sensitivity_dbm, 1) if powers else None,
                "rtk_link_bytes": self.bytes_delivered}


def correction_scene(base_scene: RadioScene, frequency: float, tx_position: np.ndarray, tx_power_dbm: float, tx_gain_dbi: float,
                     rx_gain_dbi: float, antenna_offset: np.ndarray, body: list[Any]) -> RadioScene:
    """The ELRS scene's ground and obstacles with the correction radios' antennas and frequency."""
    return dataclasses.replace(base_scene, frequency=frequency, tx_position=np.asarray(tx_position, dtype=float),
                               tx_power_dbm=tx_power_dbm, tx_gain_dbi=tx_gain_dbi, rx_gain_dbi=rx_gain_dbi,
                               antenna_offset=np.asarray(antenna_offset, dtype=float), antenna_offset_source="catalog",
                               body=body, notes=list(base_scene.notes))
