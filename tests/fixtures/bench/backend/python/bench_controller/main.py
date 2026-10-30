"""Minimal sensor-rig firmware exercising the public HAL and lockstep clock."""
from __future__ import annotations
import argparse
import os
import vendor
from r3_hal import HalError
from r3_hal.virtual import VirtualHal
from devices import Ina226, Mpu6050


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=1.0)
    args = parser.parse_args()
    hal = VirtualHal(os.environ["R3_HAL_ENDPOINT"], module="bench_controller")
    hal.log("info", "boot", mission=False, config_sources=["R3_FIRMWARE_CONFIG"])
    for name, driver in (("ina226", Ina226(hal.i2c("/dev/i2c-1"))),
                         ("mpu6050", Mpu6050(hal.i2c("/dev/i2c-1")))):
        try:
            driver.init()
        except HalError as error:
            hal.log("warning", "device_init_failed", device=name, code=error.code)
    hal.pwm("pwmchip0", 2).configure(20_000_000, 1_500_000, True)
    hal.pwm("pwmchip0", 3).configure(20_000_000, 1_500_000, True)
    hal.uart("/dev/ttyAMA0", 420000)
    hal.uart("/dev/ttyAMA2", 115200)
    start = hal.clock.now()
    for step in range(round(args.duration * 100)):
        hal.clock.sleep_until(start + (step + 1) / 100)
    hal.log("info", "shutdown")
    hal.close()


if __name__ == "__main__":
    main()
