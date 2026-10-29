"""INA226 / MPU-6050 drivers against register-level fakes; estimator pieces."""

from __future__ import annotations

import math

import firmware_fake_hal as fake
import pytest
from devices import GyroBiasCalibrator, Ina226, Mpu6050, ina226_calibration, ina226_config_word
from estimator import VehicleEstimator, WheelSpeedEstimator, nmea_course_to_enu
from r3_hal import HalError


def test_ina226_calibration_math() -> None:
    # Datasheet eq. 1/2 with the HAT's 2 mOhm shunt and a 10 A full scale (fuse rating).
    cal, lsb = ina226_calibration(0.002, 10.0)
    assert lsb == pytest.approx(5e-4) and cal == 5120
    assert ina226_config_word() == 0x4527  # AVG=16, 1.1 ms conversions, continuous shunt+bus
    with pytest.raises(ValueError):
        ina226_calibration(0.0001, 0.01)


def test_ina226_driver_reads_voltage_and_current() -> None:
    hal = fake.FakeCarHal(lambda t: {})
    ina = Ina226(hal.i2c("/dev/i2c-1"), 0x40, 0.002, 10.0)
    ina.init()
    assert hal.i2c_devices[0x40].registers[0x05] == 5120
    assert hal.i2c_devices[0x40].registers[0x00] == 0x4527
    reading = ina.read()
    expected_v = hal.battery_voltage()
    assert reading.bus_voltage_v == pytest.approx(expected_v, abs=1.25e-3)
    assert reading.current_a == pytest.approx(hal.params.hat_current, abs=5e-4)
    assert reading.shunt_voltage_v == pytest.approx(hal.params.hat_current * 0.002, abs=2.5e-6)


def test_mpu6050_init_and_burst_read_scale() -> None:
    hal = fake.FakeCarHal(lambda t: {}, params=fake.CarParams(gyro_noise=0.0, gyro_bias=0.0))
    imu = Mpu6050(hal.i2c("/dev/i2c-1"))
    imu.init()
    registers = hal.i2c_devices[0x68].registers
    assert registers[0x6B] == 0x01 and registers[0x1B] == 1 << 3 and registers[0x1C] == 1 << 3
    hal.car.yaw_rate = 1.0  # rad/s
    sample = imu.read()
    assert sample.gyro_rps[2] == pytest.approx(1.0, abs=math.radians(1 / 65.5))
    assert sample.accel_mps2[2] == pytest.approx(9.80665, abs=0.01)
    assert sample.temperature_c == pytest.approx(25.0, abs=0.01)


def test_missing_device_reports_nack() -> None:
    hal = fake.FakeCarHal(lambda t: {})
    del hal.i2c_devices[0x68]
    with pytest.raises(HalError) as error:
        Mpu6050(hal.i2c("/dev/i2c-1")).init()
    assert error.value.code == "i2c_nack"


def test_gyro_bias_calibration_restarts_on_motion() -> None:
    calibrator = GyroBiasCalibrator(50)
    for i in range(30):
        calibrator.add(0.02, moving=False)
    calibrator.add(0.02, moving=True)
    assert not calibrator.done and calibrator.restarts == 1
    for i in range(50):
        calibrator.add(0.02 + (0.001 if i % 2 else -0.001), moving=False)
    assert calibrator.done and calibrator.bias_rps == pytest.approx(0.02, abs=1e-4)


def test_wheel_speed_from_edge_timestamps() -> None:
    spacing = 2 * math.pi * 0.055 / 4
    wheel = WheelSpeedEstimator(spacing)
    speed, count, t = 3.0, 0, 0.0
    period = spacing / speed
    estimates = []
    for tick in range(1, 200):
        now = tick * 0.01
        while (count + 1) * period <= now:
            count += 1
        estimates.append(wheel.update(now, count, count * period))
    assert estimates[-1] == pytest.approx(3.0, rel=0.02)
    for tick in range(200, 300):  # wheel stops: speed decays to zero
        estimate = wheel.update(tick * 0.01, count, count * period)
    assert estimate == 0.0


def test_estimator_fuses_gps_and_wheel_speed() -> None:
    est = VehicleEstimator(initial_yaw=0.3)
    est.update_gps_position(0.0, 0.0)
    for step in range(1000):  # 10 s heading north-east at 2 m/s, gyro reading 0
        t = step * 0.01
        est.predict(0.01, 0.0)
        est.update_wheel_speed(2.0)
        if step % 10 == 0:
            heading = math.radians(45.0)
            est.update_gps_position(2.0 * t * math.cos(heading), 2.0 * t * math.sin(heading))
            est.update_gps_velocity(2.0, nmea_course_to_enu(45.0))
    assert est.yaw == pytest.approx(math.radians(45.0), abs=0.02)
    assert est.speed == pytest.approx(2.0, abs=0.05)
    assert math.hypot(est.x - 2.0 * 9.99 * math.cos(math.pi / 4), est.y - 2.0 * 9.99 * math.sin(math.pi / 4)) < 0.3
    assert nmea_course_to_enu(90.0) == pytest.approx(0.0)  # due east
