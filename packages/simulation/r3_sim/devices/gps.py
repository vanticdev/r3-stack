"""u-blox M10 GNSS receiver emitting NMEA 0183 (GGA, RMC, VTG) over UART.

Local tangent plane: world +x = east, +y = north, +z = up around a fixed
geodetic origin. Small-offset geodetic conversion with WGS-84 radii of
curvature: lat = lat0 + y / M, lon = lon0 + x / (N cos lat0),
M = a (1 - e^2) / (1 - e^2 sin^2 lat0)^1.5, N = a / sqrt(1 - e^2 sin^2 lat0).

Position error: per-axis first-order Gauss-Markov (correlation time 60 s)
plus white noise; sigma per axis = CEP / 1.1774 (circular CEP50 of a 2-D
Gaussian). Velocity noise is white. Sentences are scheduled at the fix epoch
with 8N1 byte timing at the module baud rate; a baud mismatch with the
software's UART configuration corrupts bytes (framing errors).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3
KNOTS_PER_MPS = 1.0 / 0.514444
GM_CORRELATION_TIME = 60.0
WHITE_FRACTION = 0.3  # fraction of the per-axis sigma that is white


def nmea_checksum(body: str) -> str:
    value = 0
    for char in body:
        value ^= ord(char)
    return f"{value:02X}"


def sentence(body: str) -> bytes:
    return f"${body}*{nmea_checksum(body)}\r\n".encode("ascii")


def _lat(lat: float) -> tuple[str, str]:
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    deg = int(lat)
    return f"{deg:02d}{(lat - deg) * 60:08.5f}", hemi


def _lon(lon: float) -> tuple[str, str]:
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    deg = int(lon)
    return f"{deg:03d}{(lon - deg) * 60:08.5f}", hemi


class LocalTangentPlane:
    def __init__(self, lat0: float, lon0: float, alt0: float) -> None:
        self.lat0, self.lon0, self.alt0 = lat0, lon0, alt0
        s = math.sin(math.radians(lat0))
        denom = 1.0 - WGS84_E2 * s * s
        self.meridian = WGS84_A * (1.0 - WGS84_E2) / denom ** 1.5
        self.prime = WGS84_A / math.sqrt(denom)

    def to_geodetic(self, east: float, north: float, up: float) -> tuple[float, float, float]:
        lat = self.lat0 + math.degrees(north / self.meridian)
        lon = self.lon0 + math.degrees(east / (self.prime * math.cos(math.radians(self.lat0))))
        return lat, lon, self.alt0 + up

    def to_local(self, lat: float, lon: float) -> tuple[float, float]:
        north = math.radians(lat - self.lat0) * self.meridian
        east = math.radians(lon - self.lon0) * self.prime * math.cos(math.radians(self.lat0))
        return east, north


class GpsReceiver:
    def __init__(self, params: dict, origin: tuple[float, float, float], rng: np.random.Generator,
                 epoch_utc: datetime | None = None) -> None:
        from r3_model.units import to_si

        self.rate = to_si(params.get("update_rate", 10.0))
        self.baud = int(params.get("baud", 115200))
        self.cep = float(params.get("horizontal_cep", 2.5))
        self.velocity_noise = float(params.get("velocity_noise", 0.05))
        self.cold_start = to_si(params.get("cold_start", 30.0))
        self.byte_time = 10.0 / self.baud
        self.ltp = LocalTangentPlane(*origin)
        self.rng = rng
        self.sigma = self.cep / 1.1774
        self.sigma_white = WHITE_FRACTION * self.sigma
        self.sigma_gm = math.sqrt(self.sigma ** 2 - self.sigma_white ** 2)
        self.error = rng.normal(0.0, self.sigma_gm, 2)  # stationary start
        self.vertical_error = float(rng.normal(0.0, 2.0 * self.sigma_gm))
        self.next_epoch = 0.0
        self.powered_at: float | None = -1e9  # warm at scenario start
        self.epoch_utc = epoch_utc or datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.last_fix: tuple[float, float] | None = None
        self.tx_free_t = 0.0

    def _sentences(self, t: float, position: np.ndarray, velocity: np.ndarray, fix: bool) -> bytes:
        stamp = self.epoch_utc + timedelta(seconds=t)
        hhmmss = stamp.strftime("%H%M%S") + f".{int(round(stamp.microsecond / 1e4)) % 100:02d}"
        ddmmyy = stamp.strftime("%d%m%y")
        if not fix:
            return (sentence(f"GNGGA,{hhmmss},,,,,0,00,99.99,,,,,,") + sentence(f"GNRMC,{hhmmss},V,,,,,,,{ddmmyy},,,N,V")
                    + sentence("GNVTG,,,,,,,,,N"))
        lat, lon, alt = self.ltp.to_geodetic(position[0], position[1], position[2])
        lat_s, ns = _lat(lat)
        lon_s, ew = _lon(lon)
        vx = velocity[0] + self.rng.normal(0.0, self.velocity_noise)
        vy = velocity[1] + self.rng.normal(0.0, self.velocity_noise)
        speed = math.hypot(vx, vy)
        course = (math.degrees(math.atan2(vx, vy)) + 360.0) % 360.0  # from north, clockwise
        knots = speed * KNOTS_PER_MPS
        kmh = speed * 3.6
        hdop = 0.8
        return (sentence(f"GNGGA,{hhmmss},{lat_s},{ns},{lon_s},{ew},1,12,{hdop:.2f},{alt:.1f},M,-32.0,M,,")
                + sentence(f"GNRMC,{hhmmss},A,{lat_s},{ns},{lon_s},{ew},{knots:.3f},{course:.2f},{ddmmyy},,,A,V")
                + sentence(f"GNVTG,{course:.2f},T,,M,{knots:.3f},N,{kmh:.3f},K,A"))

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
            # Gauss-Markov: e_k = phi e_{k-1} + sqrt(1 - phi^2) sigma n.
            phi = math.exp(-period / GM_CORRELATION_TIME)
            self.error = phi * self.error + math.sqrt(1 - phi * phi) * self.sigma_gm * self.rng.normal(0.0, 1.0, 2)
            fix = t - self.powered_at >= self.cold_start
            noisy = position.copy()
            noisy[:2] += self.error + self.rng.normal(0.0, self.sigma_white, 2)
            noisy[2] += self.vertical_error
            data = self._sentences(t, noisy, velocity, fix)
            if fix:
                self.last_fix = (float(noisy[0]), float(noisy[1]))
            start = max(t, self.tx_free_t)
            for k, byte in enumerate(data):
                out.append((start + (k + 1) * self.byte_time, byte))
            self.tx_free_t = start + len(data) * self.byte_time
        return out
