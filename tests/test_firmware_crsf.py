"""CRSF codec: CRC-8/DVB-S2, channel packing, link statistics, stream resync."""

from __future__ import annotations

import random

import firmware_fake_hal  # noqa: F401  (puts the firmware module folder on sys.path)
import crsf
import pytest


def test_crc8_dvb_s2_reference_vectors() -> None:
    # CRC-8/DVB-S2 catalogue check value: crc("123456789") = 0xBC.
    assert crsf.crc8_dvb_s2(b"123456789") == 0xBC
    assert crsf.crc8_dvb_s2(b"") == 0x00


def test_tick_pulse_mapping_endpoints() -> None:
    assert crsf.ticks_to_us(172) == pytest.approx(988.0)
    assert crsf.ticks_to_us(1811) == pytest.approx(2012.0)
    assert crsf.ticks_to_us(992) == pytest.approx(1500.0, abs=0.5)
    for us in (988.0, 1100.0, 1500.0, 1873.0, 2012.0):
        assert crsf.ticks_to_us(crsf.us_to_ticks(us)) == pytest.approx(us, abs=0.32)  # half a tick = 0.31 us


def test_rc_channels_round_trip_with_crc() -> None:
    rng = random.Random(3)
    ticks = [rng.randint(0, 0x7FF) for _ in range(16)]
    frame = crsf.encode_frame(crsf.FRAME_TYPE_RC_CHANNELS_PACKED, crsf.pack_rc_channels(ticks))
    assert len(frame) == 26 and frame[0] == 0xC8 and frame[1] == 24 and frame[2] == 0x16
    assert frame[-1] == crsf.crc8_dvb_s2(frame[2:-1])
    frames = crsf.CrsfParser().feed(frame)
    assert len(frames) == 1
    decoded = crsf.decode(frames[0])
    assert isinstance(decoded, crsf.RcChannels)
    assert list(decoded.ticks) == ticks


def test_channel_bit_packing_matches_reference_layout() -> None:
    # Channel 1 occupies bits 0-10, channel 2 bits 11-21 (little-endian across bytes).
    ticks = [0x7FF, 0] + [0] * 14
    payload = crsf.pack_rc_channels(ticks)
    assert payload[0] == 0xFF and payload[1] == 0x07 and payload[2] == 0
    ticks = [0, 0x7FF] + [0] * 14
    payload = crsf.pack_rc_channels(ticks)
    assert payload[1] == 0xF8 and payload[2] == 0x3F


def test_link_statistics_round_trip() -> None:
    stats = crsf.LinkStatistics(-71, -80, 87, -3, 1, 5, 2, -65, 100, 7)
    frames = crsf.CrsfParser().feed(crsf.encode_link_statistics(stats))
    assert crsf.decode(frames[0]) == stats


def test_parser_resynchronises_after_noise_and_corruption() -> None:
    good = crsf.encode_rc_channels_us([1500.0] * 16)
    corrupted = bytearray(good)
    corrupted[10] ^= 0x40
    stream = b"\x00\x55\xc8\xff" + bytes(corrupted) + good + good[:7]
    parser = crsf.CrsfParser()
    frames = parser.feed(stream)
    assert len(frames) == 1
    assert parser.crc_errors >= 1 and parser.framing_errors >= 1
    frames = parser.feed(good[7:])  # the split frame completes on the next read
    assert len(frames) == 1 and parser.frames_ok == 2


def test_byte_at_a_time_feed() -> None:
    parser = crsf.CrsfParser()
    stream = crsf.encode_rc_channels_us([1000.0, 2000.0]) * 3
    count = sum(len(parser.feed(bytes([b]))) for b in stream)
    assert count == 3
