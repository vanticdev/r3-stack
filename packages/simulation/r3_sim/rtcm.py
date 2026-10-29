"""RTCM 3.3 framing and the messages an RTK base streams (RTCM 10403.3).

Transport frame: preamble 0xD3, 6 reserved bits (0), 10-bit payload length
(0..1023 bytes), payload, CRC-24Q over preamble + length + payload
(generator 0x1864CFB, Qualcomm CRC-24Q, as in RTCM 10403.3 section 4).

Messages:

* **1005** stationary reference station ARP: ECEF X/Y/Z in 0.1 mm units
  (DF025-DF027, 38-bit signed), 152 bits.
* **MSM7** (1077 GPS, 1087 GLONASS, 1097 Galileo, 1127 BeiDou). Header
  (DF002 type, DF003 station, 30-bit epoch, multiple-message flag, IODS, clock
  steering, ext. clock, smoothing, 64-bit satellite mask, 32-bit signal mask,
  Nsat x Nsig cell mask) = 169 bits + cells. Then per satellite 36 bits (rough
  range ms, extended info, rough range mod 1 ms, rough phase-range rate) and
  per signal 80 bits (fine pseudorange, fine phase range, lock time, half
  cycle, CNR, fine phase-range rate). Lengths are exact; the observable
  *values* are synthesised (deterministic pseudo-random): the rover's RTK
  solution is a statistical model conditioned on which corrections arrive
  intact, when, and over which baseline (``devices.gnss_rtk``), not a carrier-phase solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

PREAMBLE = 0xD3
CRC24Q_POLY = 0x1864CFB
MSM7_TYPES = {"GPS": 1077, "GLONASS": 1087, "Galileo": 1097, "BeiDou": 1127}
MSM_SYSTEM = {1070 + k: "GPS" for k in range(10)} | {1080 + k: "GLONASS" for k in range(10)} | \
    {1090 + k: "Galileo" for k in range(10)} | {1120 + k: "BeiDou" for k in range(10)}
#: Visible satellites per constellation and tracked signals per satellite (estimate for an open mid-latitude site).
DEFAULT_SATELLITES = {"GPS": 10, "GLONASS": 7, "Galileo": 8, "BeiDou": 10}
DEFAULT_SIGNALS = 2  # L1/L2 (GPS L1C/A + L2C, GAL E1 + E5b, BDS B1I + B2I)


def _crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= CRC24Q_POLY
        table.append(crc & 0xFFFFFF)
    return table


_CRC_TABLE = _crc_table()


def crc24q(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFF) ^ _CRC_TABLE[((crc >> 16) ^ byte) & 0xFF]
    return crc


class BitWriter:
    def __init__(self) -> None:
        self.value = 0
        self.bits = 0

    def put(self, value: int, width: int) -> None:
        if value < 0:
            value += 1 << width  # two's complement
        self.value = (self.value << width) | (value & ((1 << width) - 1))
        self.bits += width

    def to_bytes(self) -> bytes:
        pad = (-self.bits) % 8
        return (self.value << pad).to_bytes((self.bits + pad) // 8, "big")


def bits(data: bytes, start: int, width: int, signed: bool = False) -> int:
    value = int.from_bytes(data, "big") >> (len(data) * 8 - start - width) & ((1 << width) - 1)
    if signed and value >> (width - 1):
        value -= 1 << width
    return value


def frame(payload: bytes) -> bytes:
    if len(payload) > 1023:
        raise ValueError("RTCM3 payload exceeds 1023 bytes")
    head = bytes([PREAMBLE, (len(payload) >> 8) & 0x03, len(payload) & 0xFF])
    crc = crc24q(head + payload)
    return head + payload + crc.to_bytes(3, "big")


def msg_1005(station_id: int, ecef: tuple[float, float, float]) -> bytes:
    w = BitWriter()
    w.put(1005, 12)
    w.put(station_id, 12)
    w.put(0, 6)  # ITRF realisation year (reserved)
    w.put(1, 1)  # GPS
    w.put(1, 1)  # GLONASS
    w.put(1, 1)  # Galileo
    w.put(0, 1)  # reference-station indicator: physical
    w.put(round(ecef[0] * 1e4), 38)
    w.put(1, 1)  # single receiver oscillator
    w.put(0, 1)  # reserved
    w.put(round(ecef[1] * 1e4), 38)
    w.put(0, 2)  # quarter-cycle indicator
    w.put(round(ecef[2] * 1e4), 38)
    return w.to_bytes()


def msm7(message: int, station_id: int, epoch_ms: int, n_sat: int, n_sig: int, last: bool, rng: np.random.Generator) -> bytes:
    """MSM7 with ``n_sat`` satellites x ``n_sig`` signals, all cells present; synthetic observables."""
    w = BitWriter()
    w.put(message, 12)
    w.put(station_id, 12)
    w.put(epoch_ms % (604800 * 1000) if message != 1087 else epoch_ms % (86400 * 1000), 30)
    w.put(0 if last else 1, 1)  # multiple message bit: more MSMs follow for this epoch
    w.put(0, 3)  # IODS
    w.put(0, 7)  # reserved
    w.put(0, 2)  # clock steering
    w.put(0, 2)  # external clock
    w.put(0, 1)  # divergence-free smoothing
    w.put(0, 3)  # smoothing interval
    sat_ids = sorted(int(v) for v in rng.choice(np.arange(1, 33), size=min(n_sat, 32), replace=False))
    w.put(sum(1 << (64 - s) for s in sat_ids), 64)
    sig_ids = [2, 16][:n_sig] if n_sig <= 2 else list(range(2, 2 + n_sig))
    w.put(sum(1 << (32 - s) for s in sig_ids), 32)
    n_cells = len(sat_ids) * len(sig_ids)
    w.put((1 << n_cells) - 1, n_cells)
    for _ in sat_ids:
        w.put(int(rng.integers(64, 90)), 8)  # rough range, whole ms (~20 000 km)
    for _ in sat_ids:
        w.put(0, 4)  # extended satellite info
    for _ in sat_ids:
        w.put(int(rng.integers(0, 1024)), 10)
    for _ in sat_ids:
        w.put(int(rng.integers(-800, 800)), 14)  # rough phase-range rate, m/s
    for _ in range(n_cells):
        w.put(int(rng.integers(-(1 << 19), 1 << 19)), 20)
    for _ in range(n_cells):
        w.put(int(rng.integers(-(1 << 23), 1 << 23)), 24)
    for _ in range(n_cells):
        w.put(700, 10)  # lock time indicator (long continuous lock)
    for _ in range(n_cells):
        w.put(0, 1)
    for _ in range(n_cells):
        w.put(int(rng.integers(40 * 16, 50 * 16)), 10)  # CNR, 1/16 dB-Hz
    for _ in range(n_cells):
        w.put(int(rng.integers(-(1 << 14), 1 << 14)), 15)
    return w.to_bytes()


def msm7_bytes(n_sat: int, n_sig: int) -> int:
    """Framed length of an MSM7 message (bytes)."""
    payload_bits = 169 + n_sat * n_sig + 36 * n_sat + 80 * n_sat * n_sig
    return math.ceil(payload_bits / 8) + 6


@dataclass
class RtcmMessage:
    number: int
    payload: bytes
    t: float  # arrival time of the last byte

    @property
    def system(self) -> str | None:
        return MSM_SYSTEM.get(self.number)

    def epoch_ms(self) -> int | None:
        return bits(self.payload, 24, 30) if self.system else None

    def multiple(self) -> bool:
        return bool(bits(self.payload, 54, 1)) if self.system else False

    def station_ecef(self) -> tuple[float, float, float] | None:
        if self.number not in (1005, 1006):
            return None
        return (bits(self.payload, 34, 38, True) * 1e-4, bits(self.payload, 74, 38, True) * 1e-4,
                bits(self.payload, 114, 38, True) * 1e-4)


class RtcmParser:
    """Byte-stream RTCM3 deframer (receiver side): resynchronises on the preamble, drops CRC failures."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.times: list[float] = []
        self.crc_errors = 0
        self.messages_ok = 0

    def feed(self, items: list[tuple[float, int]]) -> list[RtcmMessage]:
        out: list[RtcmMessage] = []
        for t, byte in items:
            self.buffer.append(byte)
            self.times.append(t)
        while True:
            start = self.buffer.find(bytes([PREAMBLE]))
            if start < 0:
                self.buffer.clear()
                self.times.clear()
                return out
            if start:
                del self.buffer[:start]
                del self.times[:start]
            if len(self.buffer) < 3:
                return out
            if self.buffer[1] & 0xFC:
                self._skip()
                continue
            length = ((self.buffer[1] & 0x03) << 8) | self.buffer[2]
            total = length + 6
            if len(self.buffer) < total:
                return out
            data = bytes(self.buffer[:total])
            if crc24q(data[:-3]) != int.from_bytes(data[-3:], "big") or length < 2:
                self.crc_errors += 1
                self._skip()
                continue
            payload = data[3:-3]
            out.append(RtcmMessage(bits(payload, 0, 12), payload, self.times[total - 1]))
            self.messages_ok += 1
            del self.buffer[:total]
            del self.times[:total]

    def _skip(self) -> None:
        del self.buffer[:1]
        del self.times[:1]


def ecef_from_geodetic(lat_deg: float, lon_deg: float, alt: float) -> tuple[float, float, float]:
    """WGS-84: X = (N + h) cos(lat) cos(lon), Y = (N + h) cos(lat) sin(lon), Z = (N (1 - e^2) + h) sin(lat)."""
    a, e2 = 6378137.0, 6.69437999014e-3
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    return ((n + alt) * math.cos(lat) * math.cos(lon), (n + alt) * math.cos(lat) * math.sin(lon),
            (n * (1.0 - e2) + alt) * math.sin(lat))
