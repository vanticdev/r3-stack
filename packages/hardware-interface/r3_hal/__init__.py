"""R3 hardware abstraction layer: one API for real Raspberry Pi hardware and
the R3 digital twin. See ``PROTOCOL.md`` for the virtual wire protocol."""

from __future__ import annotations

import os

from .api import Camera, Clock, Frame, GpioLine, Hal, HalError, I2cBus, PwmChannel, SpiDevice, Uart


def open_hal(module: str = "") -> Hal:
    """Open the backend selected by ``R3_HAL`` (``linux`` or ``virtual``)."""
    backend = os.environ.get("R3_HAL", "linux")
    if backend == "virtual":
        from .virtual import VirtualHal

        return VirtualHal(os.environ.get("R3_HAL_ENDPOINT", "tcp://127.0.0.1:7600"), module=module)
    if backend == "linux":
        from .linux import LinuxHal

        return LinuxHal()
    raise HalError("unknown_backend", f"R3_HAL={backend!r}")


__all__ = ["Camera", "Clock", "Frame", "GpioLine", "Hal", "HalError", "I2cBus", "PwmChannel", "SpiDevice", "Uart", "open_hal"]
