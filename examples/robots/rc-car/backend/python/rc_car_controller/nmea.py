"""NMEA-0183 parsing (GGA, RMC, VTG) and a local tangent-plane projection.

Sentence: ``$<talker><type>,<fields>*<checksum>\\r\\n``; the checksum is the XOR of
every character between ``$`` and ``*`` as two hex digits. Any talker id
(GP, GN, GL, GA, BD) is accepted. Latitude is ``ddmm.mmmm``, longitude
``dddmm.mmmm``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

KNOT_MPS = 1852.0 / 3600.0
MAX_SENTENCE_LENGTH = 120  # NMEA allows 82 chars; u-blox can exceed slightly.


def checksum(body: str) -> int:
    value = 0
    for char in body.encode("ascii"):
        value ^= char
    return value


def encode_sentence(body: str) -> bytes:
    """``body`` excludes ``$`` and ``*``; returns the full CRLF-terminated sentence."""
    return f"${body}*{checksum(body):02X}\r\n".encode("ascii")


def validate(sentence: str) -> str | None:
    """Return the body of a sentence with a valid checksum, else None."""
    sentence = sentence.strip()
    if not sentence.startswith("$") or len(sentence) < 9 or sentence[-3] != "*":
        return None
    body, given = sentence[1:-3], sentence[-2:]
    try:
        return body if int(given, 16) == checksum(body) else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Gga:
    utc_s: float
    lat_deg: float | None
    lon_deg: float | None
    fix_quality: int
    satellites: int
    hdop: float | None
    altitude_m: float | None


@dataclass(frozen=True)
class Rmc:
    utc_s: float
    valid: bool
    lat_deg: float | None
    lon_deg: float | None
    speed_mps: float | None
    course_deg: float | None


@dataclass(frozen=True)
class Vtg:
    course_deg: float | None
    speed_mps: float | None


Sentence = Gga | Rmc | Vtg


def _float(text: str) -> float | None:
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _time(text: str) -> float:
    if len(text) < 6:
        return 0.0
    return int(text[0:2]) * 3600 + int(text[2:4]) * 60 + float(text[4:])


def _coordinate(value: str, hemisphere: str, degree_digits: int) -> float | None:
    if not value or not hemisphere:
        return None
    degrees = float(value[:degree_digits]) + float(value[degree_digits:]) / 60.0
    return -degrees if hemisphere in ("S", "W") else degrees


def parse(sentence: str) -> Sentence | None:
    """Parse one sentence; returns None for bad checksums or unsupported types."""
    body = validate(sentence)
    if body is None:
        return None
    fields = body.split(",")
    kind = fields[0][-3:]
    try:
        if kind == "GGA" and len(fields) >= 10:
            return Gga(_time(fields[1]), _coordinate(fields[2], fields[3], 2), _coordinate(fields[4], fields[5], 3),
                       int(fields[6] or 0), int(fields[7] or 0), _float(fields[8]), _float(fields[9]))
        if kind == "RMC" and len(fields) >= 9:
            knots = _float(fields[7])
            return Rmc(_time(fields[1]), fields[2] == "A", _coordinate(fields[3], fields[4], 2),
                       _coordinate(fields[5], fields[6], 3), None if knots is None else knots * KNOT_MPS, _float(fields[8]))
        if kind == "VTG" and len(fields) >= 8:
            kmh = _float(fields[7])
            return Vtg(_float(fields[1]), None if kmh is None else kmh / 3.6)
    except (ValueError, IndexError):
        return None
    return None


class NmeaReader:
    """Line-assembling stream reader; counts checksum failures."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.sentences_ok = 0
        self.checksum_errors = 0

    def feed(self, data: bytes) -> list[Sentence]:
        self._buffer.extend(data)
        results: list[Sentence] = []
        while True:
            end = self._buffer.find(b"\n")
            if end < 0:
                if len(self._buffer) > MAX_SENTENCE_LENGTH:
                    del self._buffer[:-MAX_SENTENCE_LENGTH]
                return results
            line = bytes(self._buffer[:end]).decode("ascii", "replace")
            del self._buffer[:end + 1]
            start = line.rfind("$")
            if start < 0:
                continue
            line = line[start:]
            if validate(line) is None:
                self.checksum_errors += 1
                continue
            parsed = parse(line)
            if parsed is not None:
                self.sentences_ok += 1
                results.append(parsed)


def _format_coordinate(value: float, degree_digits: int, positive: str, negative: str) -> tuple[str, str]:
    hemisphere = positive if value >= 0 else negative
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60.0
    return f"{degrees:0{degree_digits}d}{minutes:08.5f}", hemisphere


def _format_time(utc_s: float) -> str:
    utc_s %= 86400.0
    hours, rest = divmod(utc_s, 3600.0)
    minutes, seconds = divmod(rest, 60.0)
    return f"{int(hours):02d}{int(minutes):02d}{seconds:05.2f}"


def format_gga(utc_s: float, lat: float, lon: float, altitude_m: float, fix_quality: int = 1,
               satellites: int = 12, hdop: float = 0.8, talker: str = "GN") -> bytes:
    la, ns = _format_coordinate(lat, 2, "N", "S")
    lo, ew = _format_coordinate(lon, 3, "E", "W")
    body = f"{talker}GGA,{_format_time(utc_s)},{la},{ns},{lo},{ew},{fix_quality},{satellites:02d},{hdop:.2f},{altitude_m:.1f},M,0.0,M,,"
    return encode_sentence(body)


def format_rmc(utc_s: float, lat: float, lon: float, speed_mps: float, course_deg: float,
               valid: bool = True, talker: str = "GN") -> bytes:
    la, ns = _format_coordinate(lat, 2, "N", "S")
    lo, ew = _format_coordinate(lon, 3, "E", "W")
    body = (f"{talker}RMC,{_format_time(utc_s)},{'A' if valid else 'V'},{la},{ns},{lo},{ew},"
            f"{speed_mps / KNOT_MPS:.3f},{course_deg % 360.0:.2f},010126,,,A")
    return encode_sentence(body)


def format_vtg(speed_mps: float, course_deg: float, talker: str = "GN") -> bytes:
    body = f"{talker}VTG,{course_deg % 360.0:.2f},T,,M,{speed_mps / KNOT_MPS:.3f},N,{speed_mps * 3.6:.3f},K,A"
    return encode_sentence(body)


# WGS-84 ellipsoid.
WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3


@dataclass(frozen=True)
class LocalTangentPlane:
    """East-north projection about a geodetic origin.

    Uses the local radii of curvature, R_N = a(1-e^2)/(1-e^2 sin^2 phi)^1.5 (meridian)
    and R_E = a/sqrt(1-e^2 sin^2 phi) (prime vertical): x_east = dlon R_E cos phi0,
    y_north = dlat R_N. Error is below 1 cm within ~2 km of the origin.
    """

    lat0_deg: float
    lon0_deg: float

    def _radii(self) -> tuple[float, float]:
        s = math.sin(math.radians(self.lat0_deg))
        w = 1.0 - WGS84_E2 * s * s
        return WGS84_A * (1.0 - WGS84_E2) / w ** 1.5, WGS84_A / math.sqrt(w)

    def to_local(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        r_n, r_e = self._radii()
        x = math.radians(lon_deg - self.lon0_deg) * r_e * math.cos(math.radians(self.lat0_deg))
        y = math.radians(lat_deg - self.lat0_deg) * r_n
        return x, y

    def to_geodetic(self, x: float, y: float) -> tuple[float, float]:
        r_n, r_e = self._radii()
        lat = self.lat0_deg + math.degrees(y / r_n)
        lon = self.lon0_deg + math.degrees(x / (r_e * math.cos(math.radians(self.lat0_deg))))
        return lat, lon
