"""Crossfire (CRSF) serial protocol as emitted by ExpressLRS receivers.

Frame: ``[addr 0xC8][len][type][payload...][crc8]`` where ``len`` counts type +
payload + crc and the CRC is CRC-8/DVB-S2 (poly 0xD5, init 0) over type + payload.
"""

from __future__ import annotations

from dataclasses import dataclass

SYNC_FC = 0xC8
TYPE_LINK_STATISTICS = 0x14
TYPE_RC_CHANNELS_PACKED = 0x16

#: CRSF channel ticks: 172 -> 988 us, 992 -> 1500 us, 1811 -> 2012 us (us = 1500 + (ticks - 992) * 5 / 8).
TICKS_MIN, TICKS_MID, TICKS_MAX = 172, 992, 1811


def _crc_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table


_TABLE = _crc_table()


def crc8_dvb_s2(data: bytes | bytearray) -> int:
    crc = 0
    for byte in data:
        crc = _TABLE[crc ^ byte]
    return crc


def frame(frame_type: int, payload: bytes, address: int = SYNC_FC) -> bytes:
    body = bytes([frame_type]) + payload
    return bytes([address, len(body) + 1]) + body + bytes([crc8_dvb_s2(body)])


def us_to_ticks(pulse_us: float) -> int:
    return max(TICKS_MIN, min(TICKS_MAX, int(round(TICKS_MID + (pulse_us - 1500.0) * 8.0 / 5.0))))


def ticks_to_us(ticks: int) -> float:
    return 1500.0 + (ticks - TICKS_MID) * 5.0 / 8.0


def pack_channels(channels_us: list[float]) -> bytes:
    """16 x 11-bit channels, little-endian bit order (22 bytes)."""
    values = [us_to_ticks(v) for v in (list(channels_us) + [1500.0] * 16)[:16]]
    bits = 0
    for index, value in enumerate(values):
        bits |= (value & 0x7FF) << (11 * index)
    return bits.to_bytes(22, "little")


def unpack_channels(payload: bytes) -> list[int]:
    bits = int.from_bytes(payload[:22], "little")
    return [(bits >> (11 * i)) & 0x7FF for i in range(16)]


def rc_channels_frame(channels_us: list[float]) -> bytes:
    return frame(TYPE_RC_CHANNELS_PACKED, pack_channels(channels_us))


@dataclass
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

    def payload(self) -> bytes:
        """RSSI fields carry -dBm as uint8; SNR fields are int8 dB."""
        def u8(v: int) -> int:
            return max(0, min(255, int(v)))

        def s8(v: int) -> int:
            return max(-128, min(127, int(v))) & 0xFF

        return bytes([u8(-self.uplink_rssi_ant1_dbm), u8(-self.uplink_rssi_ant2_dbm), u8(self.uplink_link_quality),
                      s8(self.uplink_snr_db), u8(self.active_antenna), u8(self.rf_mode), u8(self.uplink_tx_power),
                      u8(-self.downlink_rssi_dbm), u8(self.downlink_link_quality), s8(self.downlink_snr_db)])

    def frame(self) -> bytes:
        return frame(TYPE_LINK_STATISTICS, self.payload())


class CrsfParser:
    """Incremental CRSF frame parser (used by tests and the reference controller)."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 4:
            if self.buffer[0] not in (SYNC_FC, 0xEA, 0xEE):
                del self.buffer[0]
                continue
            length = self.buffer[1]
            if length < 2 or length > 62:
                del self.buffer[0]
                continue
            if len(self.buffer) < length + 2:
                break
            body = bytes(self.buffer[2:2 + length - 1])
            crc = self.buffer[1 + length]
            if crc8_dvb_s2(body) == crc:
                frames.append((body[0], body[1:]))
                del self.buffer[:length + 2]
            else:
                self.crc_errors += 1
                del self.buffer[0]
        return frames


#: ELRS rf_mode enumeration index for the 2.4 GHz packet rates (index into the rate table).
ELRS_RF_MODE = {500: 0, 250: 1, 150: 2, 50: 3}
#: CRSF uplink_tx_power enumeration: 0=0mW 1=10mW 2=25mW 3=100mW 4=500mW 5=1000mW 6=2000mW 7=250mW 8=50mW.
_TX_POWER_ENUM = [(0, 0), (10, 1), (25, 2), (50, 8), (100, 3), (250, 7), (500, 4), (1000, 5), (2000, 6)]


def tx_power_enum(power_dbm: float) -> int:
    mw = 10 ** (power_dbm / 10.0)
    return min(_TX_POWER_ENUM, key=lambda item: abs(item[0] - mw))[1]
