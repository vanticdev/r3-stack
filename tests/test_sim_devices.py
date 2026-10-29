import numpy as np
import pytest

from r3_sim import crsf
from r3_sim.devices import GpsReceiver, HallEncoder, Ina226, LocalTangentPlane, Mpu6050
from r3_sim.devices.gps import nmea_checksum


def test_crc8_dvb_s2_check_value():
    assert crsf.crc8_dvb_s2(b"123456789") == 0xBC  # CRC-8/DVB-S2 catalogue check value


def test_crsf_rc_channels_frame_roundtrip():
    channels = [1000.0, 1500.0, 2000.0, 1234.0] + [1500.0] * 12
    frame = crsf.rc_channels_frame(channels)
    assert frame[0] == 0xC8 and frame[1] == 24 and frame[2] == crsf.TYPE_RC_CHANNELS_PACKED and len(frame) == 26
    assert frame[-1] == crsf.crc8_dvb_s2(frame[2:-1])
    (kind, payload), = crsf.CrsfParser().feed(b"\x00\x13" + frame)
    ticks = crsf.unpack_channels(payload)
    assert ticks[1] == 992 and ticks[0] == crsf.us_to_ticks(1000.0)
    assert [crsf.ticks_to_us(v) for v in ticks[:4]] == pytest.approx(channels[:4], abs=0.7)


def test_crsf_link_statistics_layout():
    stats = crsf.LinkStatistics(-71, -71, 98, 9, 0, 2, 3, -80, 100, 7)
    frame = stats.frame()
    assert frame[2] == crsf.TYPE_LINK_STATISTICS and frame[1] == 12
    assert frame[3] == 71 and frame[5] == 98 and frame[6] == 9
    corrupted = bytearray(frame)
    corrupted[5] ^= 0x01
    parser = crsf.CrsfParser()
    assert parser.feed(bytes(corrupted)) == [] and parser.crc_errors == 1


def test_ina226_register_math():
    ina = Ina226(0x40, 0.002, np.random.default_rng(0))
    ina.set_powered(True)
    assert ina.transfer(b"\xfe", 2) == b"\x54\x49"
    assert int.from_bytes(ina.transfer(b"\x00", 2), "big") == 0x4127
    cal = round(0.00512 / (1e-3 * 0.002))  # 1 mA current LSB
    assert cal == 2560
    ina.transfer(bytes([0x05]) + cal.to_bytes(2, "big"), 0)
    ina.transfer(bytes([0x00]) + (0x4527).to_bytes(2, "big"), 0)  # 16 averages, 1.1 ms, continuous
    for _ in range(200):
        ina.update(0.001, 1.5, 7.4)
    shunt = int.from_bytes(ina.transfer(b"\x01", 2), "big", signed=True)
    bus = int.from_bytes(ina.transfer(b"\x02", 2), "big")
    current = int.from_bytes(ina.transfer(b"\x04", 2), "big", signed=True)
    power = int.from_bytes(ina.transfer(b"\x03", 2), "big")
    assert shunt == pytest.approx(1200, abs=3)  # 3 mV / 2.5 uV
    assert bus == pytest.approx(5920, abs=3)  # 7.4 V / 1.25 mV
    assert current == int(shunt * cal / 2048)
    assert power == int(current * bus / 20000)
    assert current * 1e-3 == pytest.approx(1.5, abs=0.005)
    assert power * 25e-3 == pytest.approx(11.1, abs=0.1)
    ina.transfer(bytes([0x00, 0x80, 0x00]), 0)  # reset
    assert int.from_bytes(ina.transfer(b"\x05", 2), "big") == 0


def test_mpu6050_registers_and_scaling():
    imu = Mpu6050(0x68, np.random.default_rng(1))
    imu.set_powered(True)
    assert imu.transfer(b"\x75", 1) == b"\x68"
    assert imu.transfer(b"\x6b", 1) == b"\x40"  # asleep after reset
    imu.update(0.001, np.array([0, 0, 9.80665]), np.zeros(3), 30.0)
    assert imu.transfer(b"\x3b", 6) == bytes(6)  # no conversions while sleeping
    imu.transfer(b"\x6b\x00", 0)
    for _ in range(100):
        imu.update(0.001, np.array([0.0, 0.0, 9.80665]), np.array([0.0, 0.0, np.radians(100.0)]), 30.0)
    raw = imu.transfer(b"\x3b", 14)
    az = int.from_bytes(raw[4:6], "big", signed=True)
    gz = int.from_bytes(raw[12:14], "big", signed=True)
    temp = int.from_bytes(raw[6:8], "big", signed=True) / 340.0 + 36.53
    assert az == pytest.approx(16384, abs=16384 * 0.08 + 60)  # +-2 g: 16384 LSB/g, zero-g offset <= 80 mg
    assert gz == pytest.approx(13100, abs=131 * 20)  # +-250 dps: 131 LSB/(deg/s), ZRO <= 20 deg/s
    assert temp == pytest.approx(30.0, abs=0.01)
    imu.transfer(b"\x1c\x08", 0)  # +-4 g
    imu.update(0.001, np.array([0.0, 0.0, 9.80665]), np.zeros(3), 30.0)
    assert int.from_bytes(imu.transfer(b"\x3f", 2), "big", signed=True) == pytest.approx(8192, abs=8192 * 0.1)


def test_gps_nmea_sentences_and_local_frame():
    ltp = LocalTangentPlane(37.4275, -122.1697, 30.0)
    lat, lon, _ = ltp.to_geodetic(100.0, 50.0, 0.0)
    east, north = ltp.to_local(lat, lon)
    assert (east, north) == pytest.approx((100.0, 50.0), abs=1e-6)
    gps = GpsReceiver({"update_rate": 10, "baud": 115200, "horizontal_cep": 1.5}, (37.4275, -122.1697, 30.0), np.random.default_rng(2))
    out = gps.advance(0.25, np.array([10.0, 5.0, 0.2]), np.array([3.0, 0.0, 0.0]), True)
    times = [t for t, _ in out]
    text = bytes(b for _, b in out).decode()
    sentences = [line for line in text.split("\r\n") if line]
    assert [s[3:6] for s in sentences] == ["GGA", "RMC", "VTG"] * 3  # epochs at 0.0, 0.1, 0.2 s
    for s in sentences:
        body, cs = s[1:].split("*")
        assert nmea_checksum(body) == cs
    assert times[1] - times[0] == pytest.approx(10 / 115200)


def test_hall_encoder_four_rising_edges_per_rev():
    encoder = HallEncoder(4, 0.0, np.random.default_rng(3))
    edges = []
    encoder.update(0.0, 0.0, 0.0)
    for k in range(1, 1001):
        edges += encoder.update((k - 1) * 0.001, k * 0.001, 2 * np.pi * k / 1000)
    assert sum(1 for _, rising in edges if rising) == 4
    assert len(edges) == 8
