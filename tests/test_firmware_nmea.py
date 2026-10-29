"""NMEA-0183: checksum validation, GGA/RMC/VTG parsing, stream reading, projection."""

from __future__ import annotations

import firmware_fake_hal  # noqa: F401
import nmea
import pytest

# Classic NMEA reference sentences (checksums verified independently).
GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
VTG = "$GPVTG,054.7,T,034.4,M,005.5,N,010.2,K*48"


def test_checksum_of_reference_sentences() -> None:
    for sentence in (GGA, RMC, VTG):
        body, given = sentence[1:-3], sentence[-2:]
        assert nmea.checksum(body) == int(given, 16)
        assert nmea.validate(sentence) == body


def test_bad_checksum_and_malformed_rejected() -> None:
    assert nmea.parse(GGA[:-2] + "48") is None
    assert nmea.parse(GGA.replace("545.4", "545.5")) is None
    assert nmea.parse("$GPGGA,123519*") is None
    assert nmea.parse("GPGGA,123519,4807.038,N*47") is None


def test_parse_gga() -> None:
    fix = nmea.parse(GGA)
    assert isinstance(fix, nmea.Gga)
    assert fix.lat_deg == pytest.approx(48 + 7.038 / 60)
    assert fix.lon_deg == pytest.approx(11 + 31.0 / 60)
    assert (fix.fix_quality, fix.satellites, fix.hdop, fix.altitude_m) == (1, 8, 0.9, 545.4)
    assert fix.utc_s == 12 * 3600 + 35 * 60 + 19


def test_parse_rmc_and_vtg_units() -> None:
    rmc = nmea.parse(RMC)
    assert isinstance(rmc, nmea.Rmc) and rmc.valid
    assert rmc.speed_mps == pytest.approx(22.4 * 1852 / 3600)
    assert rmc.course_deg == pytest.approx(84.4)
    vtg = nmea.parse(VTG)
    assert isinstance(vtg, nmea.Vtg)
    assert vtg.speed_mps == pytest.approx(10.2 / 3.6)
    assert vtg.course_deg == pytest.approx(54.7)


def test_southern_western_hemispheres_and_formatter_round_trip() -> None:
    sentence = nmea.format_gga(45296.5, -33.8688, -151.2093, 12.3).decode()
    fix = nmea.parse(sentence)
    assert fix.lat_deg == pytest.approx(-33.8688, abs=1e-7) and fix.lon_deg == pytest.approx(-151.2093, abs=1e-7)
    rmc = nmea.parse(nmea.format_rmc(0.0, 1.0, 2.0, 3.25, 271.5).decode())
    assert rmc.speed_mps == pytest.approx(3.25, abs=1e-3) and rmc.course_deg == pytest.approx(271.5)
    vtg = nmea.parse(nmea.format_vtg(4.0, 10.0).decode())
    assert vtg.speed_mps == pytest.approx(4.0, abs=1e-3)


def test_stream_reader_handles_split_lines_and_noise() -> None:
    reader = nmea.NmeaReader()
    stream = (b"\x00\xffgarbage\r\n" + GGA.encode() + b"\r\n" + RMC[:-2].encode() + b"00\r\n" + VTG.encode() + b"\r\n")
    out = []
    for i in range(0, len(stream), 7):
        out += reader.feed(stream[i:i + 7])
    assert [type(s).__name__ for s in out] == ["Gga", "Vtg"]
    assert reader.checksum_errors == 1


def test_local_tangent_plane_round_trip_and_scale() -> None:
    plane = nmea.LocalTangentPlane(47.3977, 8.5456)
    lat, lon = plane.to_geodetic(1234.5, -678.9)
    x, y = plane.to_local(lat, lon)
    assert (x, y) == pytest.approx((1234.5, -678.9), abs=1e-6)
    # One arc-minute of latitude is ~1852 m (WGS-84 meridian radius at 47 deg: 1853.4 m).
    _, y = plane.to_local(47.3977 + 1 / 60, 8.5456)
    assert y == pytest.approx(1853.4, abs=1.0)
