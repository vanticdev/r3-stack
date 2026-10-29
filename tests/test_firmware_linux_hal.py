"""Linux HAL: UAPI struct layouts, ioctl numbers and argument packing.

Sizes and ioctl values are checked against the kernel headers for 64-bit
targets (arm64 Raspberry Pi OS, x86-64): ``linux/gpio.h``, ``linux/i2c-dev.h``,
``asm-generic/termbits.h``, ``linux/spi/spidev.h``.
"""

from __future__ import annotations

import ctypes
import struct
from pathlib import Path

import pytest
from r3_hal import linux
from r3_hal.api import HalError

POINTER_64 = ctypes.sizeof(ctypes.c_void_p) == 8


@pytest.mark.skipif(not POINTER_64, reason="reference sizes are for 64-bit ABIs")
def test_struct_sizes_match_kernel_uapi() -> None:
    sizes = {
        linux.I2cMsg: 16, linux.I2cRdwrIoctlData: 16,
        linux.GpioV2LineAttribute: 16, linux.GpioV2LineConfigAttribute: 24, linux.GpioV2LineConfig: 272,
        linux.GpioV2LineRequest: 592, linux.GpioV2LineValues: 16, linux.GpioV2LineEvent: 48,
        linux.Termios2: 44, linux.SpiIocTransfer: 32,
    }
    assert {cls.__name__: ctypes.sizeof(cls) for cls in sizes} == {cls.__name__: size for cls, size in sizes.items()}


def test_ioctl_numbers() -> None:
    assert linux.I2C_RDWR == 0x0707
    assert linux.GPIO_V2_GET_LINE_IOCTL == 0xC250B407
    assert linux.GPIO_V2_LINE_SET_CONFIG_IOCTL == 0xC110B40D
    assert linux.GPIO_V2_LINE_GET_VALUES_IOCTL == 0xC010B40E
    assert linux.GPIO_V2_LINE_SET_VALUES_IOCTL == 0xC010B40F
    assert linux.TCGETS2 == 0x802C542A and linux.TCSETS2 == 0x402C542B
    assert linux.spi_ioc_message(1) == 0x40206B00 and linux.spi_ioc_message(2) == 0x40406B00
    assert linux.SPI_IOC_WR_MODE == 0x40016B01 and linux.SPI_IOC_WR_MAX_SPEED_HZ == 0x40046B04


@pytest.mark.skipif(not POINTER_64, reason="64-bit layout")
def test_i2c_rdwr_messages_pack_write_then_read() -> None:
    data, messages, read_buffer, write_buffer = linux.build_i2c_transfer(0x68, b"\x3b", 14)
    assert data.nmsgs == 2
    raw = bytes(messages)
    addr, flags, length = struct.unpack_from("<HHH", raw, 0)
    assert (addr, flags, length) == (0x68, 0, 1)
    addr, flags, length = struct.unpack_from("<HHH", raw, 16)
    assert (addr, flags, length) == (0x68, linux.I2C_M_RD, 14)
    write_pointer = struct.unpack_from("<Q", raw, 8)[0]
    read_pointer = struct.unpack_from("<Q", raw, 24)[0]
    assert write_pointer == ctypes.addressof(write_buffer) and read_pointer == ctypes.addressof(read_buffer)
    assert ctypes.string_at(write_pointer, 1) == b"\x3b"
    assert ctypes.addressof(messages) == ctypes.cast(data.msgs, ctypes.c_void_p).value
    # Write-only and probe forms.
    assert linux.build_i2c_transfer(0x40, b"\x05\x14\x00", 0)[0].nmsgs == 1
    assert linux.build_i2c_transfer(0x40, b"", 0)[1][0].len == 0


def test_gpio_line_request_packing() -> None:
    request = linux.build_gpio_line_request(17, "in", bias="pull-up", edge="rising", consumer="encoder",
                                            event_buffer_size=256)
    raw = bytes(request)
    assert struct.unpack_from("<I", raw, 0)[0] == 17                       # offsets[0]
    assert raw[256:256 + 8] == b"encoder\0"                                # consumer
    flags = struct.unpack_from("<Q", raw, 288)[0]                          # config.flags
    assert flags == linux.GPIO_V2_LINE_FLAG_INPUT | linux.GPIO_V2_LINE_FLAG_BIAS_PULL_UP | linux.GPIO_V2_LINE_FLAG_EDGE_RISING
    assert flags == (1 << 2) | (1 << 8) | (1 << 4)
    assert struct.unpack_from("<I", raw, 288 + 272)[0] == 1                # num_lines
    assert struct.unpack_from("<I", raw, 288 + 276)[0] == 256              # event_buffer_size
    output = linux.build_gpio_line_request(27, "out", output_value=1)
    attr = output.config.attrs[0]
    assert output.config.num_attrs == 1 and attr.attr.id == linux.GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES
    assert attr.attr.values == 1 and attr.mask == 1
    with pytest.raises(HalError):
        linux.build_gpio_line_request(5, "out", edge="rising")


def test_gpio_event_parsing() -> None:
    packed = b"".join(struct.pack("<QIIII24x", ts, event_id, 17, seq, seq) for ts, event_id, seq in
                      ((1_000_000_000, 1, 1), (1_000_500_000, 2, 2), (1_001_000_000, 1, 3)))
    events = linux.parse_gpio_events(packed)
    assert [(e.timestamp_ns, e.id, e.offset, e.seqno) for e in events] == [
        (1_000_000_000, 1, 17, 1), (1_000_500_000, 2, 17, 2), (1_001_000_000, 1, 17, 3)]


def test_termios2_custom_baud() -> None:
    settings = linux.Termios2()
    settings.c_cflag = 0o000017 | 0o000060 | 0o000200  # B38400 | CS8 | CREAD
    linux.apply_custom_baud(settings, 420000)
    assert settings.c_cflag & linux.CBAUD == linux.BOTHER
    assert (settings.c_cflag >> linux.IBSHIFT) & linux.CBAUD == linux.BOTHER
    assert settings.c_cflag & 0o000060 == 0o000060, "character size bits untouched"
    assert settings.c_ispeed == settings.c_ospeed == 420000
    raw = bytes(settings)
    assert struct.unpack_from("<II", raw, 36) == (420000, 420000)


@pytest.mark.skipif(not POINTER_64, reason="64-bit layout")
def test_spi_transfer_struct() -> None:
    message = linux.SpiIocTransfer(tx_buf=0x1000, rx_buf=0x2000, len=3, speed_hz=1_000_000, bits_per_word=8)
    assert struct.unpack("<QQIIHBBBBBB", bytes(message)) == (0x1000, 0x2000, 3, 1_000_000, 0, 8, 0, 0, 0, 0, 0)


def test_sysfs_pwm_write_order(tmp_path: Path) -> None:
    chip = tmp_path / "pwmchip0"
    channel = chip / "pwm2"
    channel.mkdir(parents=True)
    (chip / "export").write_text("")
    for name, value in (("period", "0"), ("duty_cycle", "0"), ("enable", "0")):
        (channel / name).write_text(value)
    writes: list[tuple[str, str]] = []
    pwm = linux.LinuxPwm("pwmchip0", 2, root=tmp_path)
    original = pwm._write

    def record(path: Path, value: str) -> None:
        if path.name == "duty_cycle" and int(value) > int((channel / "period").read_text()):
            raise AssertionError("duty_cycle written above period (kernel returns EINVAL)")
        writes.append((path.name, value))
        original(path, value)

    pwm._write = record  # type: ignore[method-assign]
    pwm.set_pulse_us(1500.0, 50.0)
    assert writes == [("period", "20000000"), ("duty_cycle", "1500000"), ("enable", "1")]
    writes.clear()
    pwm.set_pulse_us(1500.0, 50.0)
    assert writes == [], "unchanged settings are not rewritten"
    pwm.configure(1_000_000, 900_000)  # shrinking the period below the duty: duty first
    assert writes == [("duty_cycle", "900000"), ("period", "1000000")]


def test_backend_imports_and_reports_missing_devices() -> None:
    hal = linux.LinuxHal()
    assert hal.bindings() == {}
    t0 = hal.clock.now()
    hal.clock.sleep_until(t0 + 0.002)
    assert hal.clock.now() >= t0 + 0.002
    with pytest.raises(HalError) as error:
        hal.i2c("/dev/i2c-r3-does-not-exist")
    assert error.value.code == "no_such_device"
    with pytest.raises(HalError):
        hal.pwm("pwmchip-r3-missing", 0)
