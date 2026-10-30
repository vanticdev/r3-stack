"""CRSF (Crossfire / ExpressLRS serial) frame codec.

Frame layout (all CRSF traffic, receiver -> flight controller):

    [address][length][type][payload ...][crc8]

``length`` counts ``type + payload + crc`` (so ``len(payload) + 2``) and is at
most 62, giving a 64-byte maximum frame. The CRC is CRC-8/DVB-S2 (polynomial
0xD5, init 0x00, no reflection, no final XOR) over ``type + payload``.

Only the two frame types a ground vehicle needs are decoded:

* ``RC_CHANNELS_PACKED`` (0x16): 16 channels x 11 bits, little-endian bit
  packing, 22 bytes. Tick 172 <-> 988 us, 992 <-> 1500 us, 1811 <-> 2012 us.
* ``LINK_STATISTICS`` (0x14): uplink/downlink RSSI, link quality and SNR.
"""

from __future__ import annotations

from dataclasses import dataclass

ADDRESS_FLIGHT_CONTROLLER = 0xC8
FRAME_TYPE_LINK_STATISTICS = 0x14
FRAME_TYPE_RC_CHANNELS_PACKED = 0x16
MAX_FRAME_LENGTH = 64
MAX_LENGTH_FIELD = MAX_FRAME_LENGTH - 2
RC_CHANNEL_COUNT = 16
RC_CHANNELS_PAYLOAD_SIZE = 22
LINK_STATISTICS_PAYLOAD_SIZE = 10

TICKS_MIN, TICKS_MAX = 172, 1811
PULSE_MIN_US, PULSE_MAX_US = 988.0, 2012.0


def _crc8_table(poly: int) -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return tuple(table)


_CRC8_DVB_S2 = _crc8_table(0xD5)


def crc8_dvb_s2(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = _CRC8_DVB_S2[crc ^ byte]
    return crc


def ticks_to_us(ticks: int) -> float:
    """Linear CRSF tick -> pulse width map (172..1811 <-> 988..2012 us)."""
    return PULSE_MIN_US + (ticks - TICKS_MIN) * (PULSE_MAX_US - PULSE_MIN_US) / (TICKS_MAX - TICKS_MIN)


def us_to_ticks(pulse_us: float) -> int:
    ticks = TICKS_MIN + (pulse_us - PULSE_MIN_US) * (TICKS_MAX - TICKS_MIN) / (PULSE_MAX_US - PULSE_MIN_US)
    return max(0, min(0x7FF, int(round(ticks))))


@dataclass(frozen=True)
class RcChannels:
    ticks: tuple[int, ...]

    def us(self, channel: int) -> float:
        """Pulse width of a 1-based channel number (channel 1 = first)."""
        return ticks_to_us(self.ticks[channel - 1])


@dataclass(frozen=True)
class LinkStatistics:
    uplink_rssi_ant1_dbm: int
    uplink_rssi_ant2_dbm: int
    uplink_link_quality: int
    uplink_snr_db: int
    active_antenna: int
    rf_mode: int
    uplink_tx_power: int
    downlink_rssi_dbm: int
    downlink_link_quality: int
    downlink_snr_db: int


def pack_rc_channels(ticks: list[int] | tuple[int, ...]) -> bytes:
    if len(ticks) != RC_CHANNEL_COUNT:
        raise ValueError(f"expected {RC_CHANNEL_COUNT} channels, got {len(ticks)}")
    bits = 0
    for index, value in enumerate(ticks):
        bits |= (int(value) & 0x7FF) << (11 * index)
    return bits.to_bytes(RC_CHANNELS_PAYLOAD_SIZE, "little")


def unpack_rc_channels(payload: bytes) -> RcChannels:
    if len(payload) != RC_CHANNELS_PAYLOAD_SIZE:
        raise ValueError("RC_CHANNELS_PACKED payload must be 22 bytes")
    bits = int.from_bytes(payload, "little")
    return RcChannels(tuple((bits >> (11 * i)) & 0x7FF for i in range(RC_CHANNEL_COUNT)))


def _int8(value: int) -> int:
    return value - 256 if value > 127 else value


def pack_link_statistics(stats: LinkStatistics) -> bytes:
    # RSSI values are transmitted as positive magnitudes of negative dBm.
    return bytes([
        -stats.uplink_rssi_ant1_dbm & 0xFF, -stats.uplink_rssi_ant2_dbm & 0xFF,
        stats.uplink_link_quality & 0xFF, stats.uplink_snr_db & 0xFF, stats.active_antenna & 0xFF,
        stats.rf_mode & 0xFF, stats.uplink_tx_power & 0xFF, -stats.downlink_rssi_dbm & 0xFF,
        stats.downlink_link_quality & 0xFF, stats.downlink_snr_db & 0xFF,
    ])


def unpack_link_statistics(payload: bytes) -> LinkStatistics:
    if len(payload) != LINK_STATISTICS_PAYLOAD_SIZE:
        raise ValueError("LINK_STATISTICS payload must be 10 bytes")
    p = payload
    return LinkStatistics(-p[0], -p[1], p[2], _int8(p[3]), p[4], p[5], p[6], -p[7], p[8], _int8(p[9]))


def encode_frame(frame_type: int, payload: bytes, address: int = ADDRESS_FLIGHT_CONTROLLER) -> bytes:
    if len(payload) + 2 > MAX_LENGTH_FIELD:
        raise ValueError("CRSF payload too long")
    body = bytes([frame_type]) + payload
    return bytes([address, len(body) + 1]) + body + bytes([crc8_dvb_s2(body)])


def encode_rc_channels_us(pulses_us: list[float]) -> bytes:
    """Encode up to 16 channel pulse widths (missing channels are centred)."""
    pulses = list(pulses_us) + [1500.0] * (RC_CHANNEL_COUNT - len(pulses_us))
    return encode_frame(FRAME_TYPE_RC_CHANNELS_PACKED, pack_rc_channels([us_to_ticks(p) for p in pulses]))


def encode_link_statistics(stats: LinkStatistics) -> bytes:
    return encode_frame(FRAME_TYPE_LINK_STATISTICS, pack_link_statistics(stats))


@dataclass(frozen=True)
class Frame:
    address: int
    frame_type: int
    payload: bytes


class CrsfParser:
    """Incremental CRSF byte-stream parser that resynchronises on bad frames."""

    def __init__(self, addresses: tuple[int, ...] = (ADDRESS_FLIGHT_CONTROLLER,)) -> None:
        self._addresses = frozenset(addresses)
        self._buffer = bytearray()
        self.frames_ok = 0
        self.crc_errors = 0
        self.framing_errors = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        buffer = self._buffer
        while len(buffer) >= 2:
            if buffer[0] not in self._addresses:
                self.framing_errors += 1
                del buffer[0]
                continue
            length = buffer[1]
            if length < 2 or length > MAX_LENGTH_FIELD:
                self.framing_errors += 1
                del buffer[0]
                continue
            total = length + 2
            if len(buffer) < total:
                break
            body = bytes(buffer[2:total - 1])
            if crc8_dvb_s2(body) != buffer[total - 1]:
                self.crc_errors += 1
                del buffer[0]
                continue
            frames.append(Frame(buffer[0], body[0], body[1:]))
            self.frames_ok += 1
            del buffer[:total]
        return frames


def decode(frame: Frame) -> RcChannels | LinkStatistics | None:
    """Decode a known frame type; returns None for unhandled types or bad sizes."""
    try:
        if frame.frame_type == FRAME_TYPE_RC_CHANNELS_PACKED:
            return unpack_rc_channels(frame.payload)
        if frame.frame_type == FRAME_TYPE_LINK_STATISTICS:
            return unpack_link_statistics(frame.payload)
    except ValueError:
        return None
    return None
