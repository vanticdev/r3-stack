"""u-blox ZED-F9P-class RTK GNSS rover: NMEA (GGA, RMC, VTG, GST) on UART1, RTCM3 in on UART2.

Solution modes (GGA fix quality / RMC mode indicator), driven only by the RTCM3
frames that actually arrive intact on UART2 (``feed_rtcm``):

* **single** (1 / A): no usable corrections. Per-axis first-order Gauss-Markov
  bias (60 s) + white noise sized to the standalone CEP (as ``GpsReceiver``).
* **RTK float** (5 / F): corrections are usable (base position from 1005/1006 and
  an observation epoch younger than ``dgnss_timeout``, CFG-NAVSPG-CONSTR_DGNSSTO).
  The error decays from the single-point error towards the float accuracy:
  e_k = phi e_{k-1} + sqrt(1 - phi^2) sigma_float n, phi = exp(-T / tau), tau = T_conv / 3.
* **RTK fixed** (4 / R): after the ambiguity search has seen fresh corrections
  (age <= ``rtk_fix_max_age``) for the convergence time T_conv, drawn per
  acquisition from U(0.4, 1.0) x ``rtk_convergence_time`` (datasheet bound
  "< 10 s"). Per-axis sigma = (CEP_rtk + ppm x baseline) / 1.1774, combined
  with the correction-age term ``rtk_age_drift`` x age; 70 % of the variance is
  a 10 s Gauss-Markov (multipath), 30 % white.
* Stale corrections (age > ``rtk_fix_max_age``) drop fixed to float; age >
  ``dgnss_timeout`` (or no base position) drops to single.

RTK positions are relative to the base's *configured* coordinates, so the base
survey error shifts every RTK solution (``survey_error``). The receiver's own
accuracy estimate is reported in GST (sigma lat/lon/alt). Wrong-fix
(incorrect integer ambiguity) events are not modelled. Corrections are
statistically, not physically, used: see ``r3_sim.rtcm``.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from ..rtcm import MSM_SYSTEM, RtcmParser
from .gps import GpsReceiver, KNOTS_PER_MPS, _lat, _lon, sentence

FIX_QUALITY = {"single": 1, "float": 5, "fixed": 4}
RMC_MODE = {"single": "A", "float": "F", "fixed": "R"}
MULTIPATH_TAU = 10.0  # s, RTK-fixed multipath correlation time (estimate)
MULTIPATH_FRACTION = 0.7  # variance share of the correlated RTK-fixed error (estimate)


def _hp_lat(lat: float) -> tuple[str, str]:
    """ddmm.mmmmmmm (CFG-NMEA-HIGHPREC: 7 decimal minutes, ~0.2 mm)."""
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    deg = int(lat)
    return f"{deg:02d}{(lat - deg) * 60:010.7f}", hemi


def _hp_lon(lon: float) -> tuple[str, str]:
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    deg = int(lon)
    return f"{deg:03d}{(lon - deg) * 60:010.7f}", hemi


class RtkGnssReceiver(GpsReceiver):
    def __init__(self, params: dict, origin: tuple[float, float, float], rng: np.random.Generator,
                 epoch_utc: datetime | None = None, survey_error: tuple[float, float, float] = (0.0, 0.0, 0.0),
                 gps_tow0_ms: int = 0) -> None:
        from r3_model.units import to_si

        super().__init__(params, origin, rng, epoch_utc)
        self.rtk_cep = float(params.get("rtk_cep", 0.01))
        self.rtk_ppm = float(params.get("rtk_ppm", 1e-6))
        self.float_cep = float(params.get("float_cep", 0.3))
        self.convergence = to_si(params.get("rtk_convergence_time", 10.0))
        self.dgnss_timeout = to_si(params.get("dgnss_timeout", 60.0))
        self.fix_max_age = to_si(params.get("rtk_fix_max_age", 10.0))
        self.age_drift = float(params.get("rtk_age_drift", 0.002))
        self.uart2_baud = int(params.get("uart2_baud", 38400))
        self.high_precision = bool(params.get("nmea_high_precision", True))
        self.constellations = set(params.get("constellations") or ["GPS", "Galileo", "BeiDou"])
        self.survey_error = np.asarray(survey_error, dtype=float)
        self.tow0_ms = int(gps_tow0_ms)
        self.parser = RtcmParser()
        self.base_ecef: tuple[float, float, float] | None = None
        self.base_local: np.ndarray | None = None
        self.last_epoch_t: float | None = None  # observation time of the newest complete correction epoch
        self._epoch_systems: dict[int, set[str]] = {}
        self.mode = "single"
        self.mode_events: list[tuple[float, str]] = []
        self.age: float = math.inf
        self.converged = 0.0
        self.needed = self._draw_convergence()
        self.float_error = np.zeros(2)
        self.fix_error = rng.normal(0.0, 1.0, 2)  # unit-variance multipath state, scaled at use
        self.epochs_by_mode = {"single": 0, "float": 0, "fixed": 0}
        self.first_fix_t: float | None = None
        self.max_age_in_rtk = 0.0
        self.corrections_epochs = 0
        self.sigma_reported = self.sigma

    def _draw_convergence(self) -> float:
        return float(self.rng.uniform(0.4, 1.0)) * self.convergence

    # ------------------------------------------------------------------ corrections
    def _epoch_time(self, number: int, epoch_ms: int) -> float:
        tow = epoch_ms + (14000 if MSM_SYSTEM.get(number) == "BeiDou" else 0)  # BDT = GPST - 14 s
        week = 604800 * 1000
        delta = (tow - self.tow0_ms) % week
        return (delta - week if delta > week // 2 else delta) / 1000.0

    def feed_rtcm(self, items: list[tuple[float, int]]) -> None:
        """RTCM3 bytes (arrival time, value) on UART2; already garbled by the kernel on a baud mismatch."""
        for message in self.parser.feed(items):
            ecef = message.station_ecef()
            if ecef is not None:
                self.base_ecef = ecef
                self.base_local = self._local_from_ecef(ecef)
                continue
            system = message.system
            if system is None or system not in self.constellations:
                continue
            epoch = self._epoch_time(message.number, message.epoch_ms() or 0)
            key = round(epoch * 1000)
            self._epoch_systems.setdefault(key, set()).add(system)
            if not message.multiple():  # last MSM of this epoch
                self._epoch_systems.pop(key, None)
                if self.last_epoch_t is None or epoch > self.last_epoch_t:
                    self.last_epoch_t = epoch
                    self.corrections_epochs += 1
            for stale in [k for k in self._epoch_systems if k < key - 5000]:
                del self._epoch_systems[stale]

    def _local_from_ecef(self, ecef: tuple[float, float, float]) -> np.ndarray:
        """ECEF -> local ENU (m) about the receiver's tangent-plane origin (closed-form Bowring geodetic)."""
        x, y, z = ecef
        a, e2 = 6378137.0, 6.69437999014e-3
        b = a * math.sqrt(1.0 - e2)
        ep2 = (a * a - b * b) / (b * b)
        p = math.hypot(x, y)
        th = math.atan2(z * a, p * b)
        lat = math.atan2(z + ep2 * b * math.sin(th) ** 3, p - e2 * a * math.cos(th) ** 3)
        lon = math.atan2(y, x)
        n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
        alt = p / math.cos(lat) - n
        east, north = self.ltp.to_local(math.degrees(lat), math.degrees(lon))
        return np.array([east, north, alt - self.ltp.alt0])

    # ------------------------------------------------------------------ solution
    def _update_mode(self, t: float, period: float) -> None:
        self.age = t - self.last_epoch_t if self.last_epoch_t is not None and t >= self.last_epoch_t else math.inf
        usable = self.base_local is not None and self.age <= self.dgnss_timeout
        previous = self.mode
        if not usable:
            self.mode, self.converged = "single", 0.0
        else:
            fresh = self.age <= self.fix_max_age
            if self.mode == "single":
                self.mode, self.converged, self.needed = "float", 0.0, self._draw_convergence()
                self.float_error = self.error.copy()  # the float filter starts from the code solution
            if self.mode == "float" and fresh:
                self.converged += period
                if self.converged >= self.needed:
                    self.mode = "fixed"
                    if self.first_fix_t is None:
                        self.first_fix_t = t
            elif self.mode == "fixed" and not fresh:
                self.mode, self.converged, self.needed = "float", 0.0, self._draw_convergence()
                self.float_error = self._fixed_offset()
        if self.mode != previous:
            self.mode_events.append((t, self.mode))

    def _fixed_sigma(self, baseline: float) -> float:
        base = (self.rtk_cep + self.rtk_ppm * baseline) / 1.1774
        age = self.age if math.isfinite(self.age) else 0.0
        return math.hypot(base, self.age_drift * age)

    def _fixed_offset(self, baseline: float = 0.0) -> np.ndarray:
        sigma = self._fixed_sigma(baseline)
        return math.sqrt(MULTIPATH_FRACTION) * sigma * self.fix_error

    def _horizontal_error(self, period: float, position: np.ndarray) -> np.ndarray:
        # Single-point bias process keeps running in every mode (it is what the receiver falls back to).
        phi = math.exp(-period / 60.0)
        self.error = phi * self.error + math.sqrt(1 - phi * phi) * self.sigma_gm * self.rng.normal(0.0, 1.0, 2)
        phi_m = math.exp(-period / MULTIPATH_TAU)
        self.fix_error = phi_m * self.fix_error + math.sqrt(1 - phi_m * phi_m) * self.rng.normal(0.0, 1.0, 2)
        if self.mode == "single":
            self.sigma_reported = self.sigma
            return self.error + self.rng.normal(0.0, self.sigma_white, 2)
        baseline = float(np.linalg.norm(position[:2] - self.base_local[:2])) if self.base_local is not None else 0.0
        if self.mode == "float":
            sigma_f = self.float_cep / 1.1774
            tau = max(self.needed / 3.0, 1e-3)
            phi_f = math.exp(-period / tau)
            self.float_error = phi_f * self.float_error + math.sqrt(1 - phi_f * phi_f) * sigma_f * self.rng.normal(0.0, 1.0, 2)
            self.sigma_reported = math.hypot(sigma_f, float(np.linalg.norm(self.float_error)) / math.sqrt(2.0))
            return self.float_error + self.survey_error[:2]
        sigma = self._fixed_sigma(baseline)
        white = math.sqrt(1.0 - MULTIPATH_FRACTION) * sigma
        self.sigma_reported = sigma
        return self._fixed_offset(baseline) + self.rng.normal(0.0, white, 2) + self.survey_error[:2]

    def _rtk_sentences(self, t: float, position: np.ndarray, velocity: np.ndarray, fix: bool) -> bytes:
        if not fix:
            return self._sentences(t, position, velocity, False)
        from datetime import timedelta

        stamp = self.epoch_utc + timedelta(seconds=t)
        hhmmss = stamp.strftime("%H%M%S") + f".{int(round(stamp.microsecond / 1e4)) % 100:02d}"
        ddmmyy = stamp.strftime("%d%m%y")
        lat, lon, alt = self.ltp.to_geodetic(position[0], position[1], position[2])
        lat_s, ns = (_hp_lat if self.high_precision else _lat)(lat)
        lon_s, ew = (_hp_lon if self.high_precision else _lon)(lon)
        vx = velocity[0] + self.rng.normal(0.0, self.velocity_noise)
        vy = velocity[1] + self.rng.normal(0.0, self.velocity_noise)
        speed = math.hypot(vx, vy)
        course = (math.degrees(math.atan2(vx, vy)) + 360.0) % 360.0
        knots = speed * KNOTS_PER_MPS
        quality = FIX_QUALITY[self.mode]
        mode = RMC_MODE[self.mode]
        age = f"{min(self.age, 999.0):.1f}" if self.mode != "single" else ""
        station = "0000" if self.mode != "single" else ""
        sigma = self.sigma_reported
        gga = sentence(f"GNGGA,{hhmmss},{lat_s},{ns},{lon_s},{ew},{quality},12,0.80,{alt:.3f},M,-32.0,M,{age},{station}")
        rmc = sentence(f"GNRMC,{hhmmss},A,{lat_s},{ns},{lon_s},{ew},{knots:.3f},{course:.2f},{ddmmyy},,,{mode},V")
        vtg = sentence(f"GNVTG,{course:.2f},T,,M,{knots:.3f},N,{speed * 3.6:.3f},K,{'D' if self.mode != 'single' else 'A'}")
        gst = sentence(f"GNGST,{hhmmss},{sigma:.3f},{sigma:.3f},{sigma:.3f},0.0,{sigma:.3f},{sigma:.3f},{2.0 * sigma:.3f}")
        return gga + rmc + vtg + gst

    def advance(self, t_end: float, position: np.ndarray, velocity: np.ndarray, powered: bool) -> list[tuple[float, int]]:
        out: list[tuple[float, int]] = []
        if not powered:
            self.powered_at = None
            self.next_epoch = t_end
            return out
        if self.powered_at is None:
            self.powered_at = t_end
        period = 1.0 / self.rate
        while self.next_epoch <= t_end:
            t = self.next_epoch
            self.next_epoch = round((self.next_epoch + period) * 1e6) / 1e6
            fix = t - self.powered_at >= self.cold_start
            if fix:
                self._update_mode(t, period)
            noisy = position.copy()
            noisy[:2] += self._horizontal_error(period, position)
            noisy[2] += self.vertical_error if self.mode == "single" else self.survey_error[2]
            data = self._rtk_sentences(t, noisy, velocity, fix)
            if fix:
                self.last_fix = (float(noisy[0]), float(noisy[1]))
                self.epochs_by_mode[self.mode] += 1
                if self.mode != "single" and math.isfinite(self.age):
                    self.max_age_in_rtk = max(self.max_age_in_rtk, self.age)
            start = max(t, self.tx_free_t)
            for k, byte in enumerate(data):
                out.append((start + (k + 1) * self.byte_time, byte))
            self.tx_free_t = start + len(data) * self.byte_time
        return out

    def stats(self) -> dict[str, float | int | str | None]:
        total = max(1, sum(self.epochs_by_mode.values()))
        return {"rtk_fixed_fraction": round(self.epochs_by_mode["fixed"] / total, 4),
                "rtk_float_fraction": round(self.epochs_by_mode["float"] / total, 4),
                "rtk_single_fraction": round(self.epochs_by_mode["single"] / total, 4),
                "rtk_time_to_first_fix_s": self.first_fix_t, "rtk_max_correction_age_s": round(self.max_age_in_rtk, 3),
                "rtk_correction_epochs": self.corrections_epochs, "rtcm_crc_errors": self.parser.crc_errors,
                "rtcm_messages_ok": self.parser.messages_ok, "rtk_mode_changes": len(self.mode_events)}
