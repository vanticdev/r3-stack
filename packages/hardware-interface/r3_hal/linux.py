"""Linux backend: real Raspberry Pi peripherals, standard library only.

* I2C: ``/dev/i2c-N`` with the ``I2C_RDWR`` ioctl (combined write/read transfer).
* PWM: ``/sys/class/pwm/pwmchipN/pwmM`` (export, period, duty_cycle, enable).
* GPIO: GPIO character device uAPI v2 (``GPIO_V2_GET_LINE_IOCTL``); edge events are
  read from the line fd by a background thread that counts rising edges.
* UART: termios raw mode; non-standard rates (CRSF 420000 baud) use ``termios2``
  with ``BOTHER`` via ``TCGETS2``/``TCSETS2``.
* SPI: spidev ``SPI_IOC_MESSAGE(1)``.
* Clock: ``time.monotonic`` (CLOCK_MONOTONIC, the same clock as GPIO edge timestamps).

Struct layouts follow the Linux UAPI headers (``linux/i2c-dev.h``, ``linux/i2c.h``,
``linux/gpio.h``, ``asm-generic/termbits.h``, ``linux/spi/spidev.h``) for the generic
ioctl encoding used by arm64, arm and x86. The module imports on any POSIX system;
device access only works on Linux.
"""

from __future__ import annotations

import ctypes
import errno
import os
import select
import sys
import threading
import time
from pathlib import Path

from .api import Clock, GpioLine, Hal, HalError, I2cBus, PwmChannel, SpiDevice, Uart

IS_LINUX = sys.platform.startswith("linux")

try:  # POSIX only; guarded so the module still imports on any platform.
    import fcntl
    import termios
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]


# --- generic ioctl number encoding (asm-generic/ioctl.h) ----------------------

_IOC_NRBITS, _IOC_TYPEBITS, _IOC_SIZEBITS = 8, 8, 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _ioc(direction: int, kind: int, number: int, size: int) -> int:
    return (direction << _IOC_DIRSHIFT) | (kind << _IOC_TYPESHIFT) | (number << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT)


def _io(kind: int, number: int) -> int:
    return _ioc(_IOC_NONE, kind, number, 0)


def _ior(kind: int, number: int, size: int) -> int:
    return _ioc(_IOC_READ, kind, number, size)


def _iow(kind: int, number: int, size: int) -> int:
    return _ioc(_IOC_WRITE, kind, number, size)


def _iowr(kind: int, number: int, size: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, kind, number, size)


# --- I2C (linux/i2c.h, linux/i2c-dev.h) ---------------------------------------

I2C_RDWR = 0x0707
EREMOTEIO = getattr(errno, "EREMOTEIO", 121)  # Linux-only errno; absent on macOS
I2C_M_RD = 0x0001
I2C_RDWR_IOCTL_MAX_MSGS = 42


class I2cMsg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16), ("len", ctypes.c_uint16),
                ("buf", ctypes.POINTER(ctypes.c_uint8))]


class I2cRdwrIoctlData(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(I2cMsg)), ("nmsgs", ctypes.c_uint32)]


def build_i2c_transfer(addr: int, write: bytes, read_len: int) -> tuple[I2cRdwrIoctlData, ctypes.Array, ctypes.Array | None, ctypes.Array]:
    """Build the I2C_RDWR argument: optional write message then optional read
    message (repeated start between them). Returns (ioctl data, msgs, read buffer,
    write buffer); the caller must keep all of them alive across the ioctl."""
    messages: list[I2cMsg] = []
    write_buffer = (ctypes.c_uint8 * max(1, len(write))).from_buffer_copy(write or b"\0")
    read_buffer = (ctypes.c_uint8 * read_len)() if read_len > 0 else None
    if write:
        messages.append(I2cMsg(addr, 0, len(write), ctypes.cast(write_buffer, ctypes.POINTER(ctypes.c_uint8))))
    if read_buffer is not None:
        messages.append(I2cMsg(addr, I2C_M_RD, read_len, ctypes.cast(read_buffer, ctypes.POINTER(ctypes.c_uint8))))
    if not messages:  # zero-length write: an address probe
        messages.append(I2cMsg(addr, 0, 0, ctypes.cast(write_buffer, ctypes.POINTER(ctypes.c_uint8))))
    array = (I2cMsg * len(messages))(*messages)
    data = I2cRdwrIoctlData(ctypes.cast(array, ctypes.POINTER(I2cMsg)), len(messages))
    return data, array, read_buffer, write_buffer


# --- GPIO character device uAPI v2 (linux/gpio.h) -----------------------------

GPIO_V2_LINES_MAX = 64
GPIO_MAX_NAME_SIZE = 32
GPIO_V2_LINE_NUM_ATTRS_MAX = 10

GPIO_V2_LINE_FLAG_USED = 1 << 0
GPIO_V2_LINE_FLAG_ACTIVE_LOW = 1 << 1
GPIO_V2_LINE_FLAG_INPUT = 1 << 2
GPIO_V2_LINE_FLAG_OUTPUT = 1 << 3
GPIO_V2_LINE_FLAG_EDGE_RISING = 1 << 4
GPIO_V2_LINE_FLAG_EDGE_FALLING = 1 << 5
GPIO_V2_LINE_FLAG_OPEN_DRAIN = 1 << 6
GPIO_V2_LINE_FLAG_OPEN_SOURCE = 1 << 7
GPIO_V2_LINE_FLAG_BIAS_PULL_UP = 1 << 8
GPIO_V2_LINE_FLAG_BIAS_PULL_DOWN = 1 << 9
GPIO_V2_LINE_FLAG_BIAS_DISABLED = 1 << 10

GPIO_V2_LINE_ATTR_ID_FLAGS = 1
GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES = 2
GPIO_V2_LINE_ATTR_ID_DEBOUNCE = 3

GPIO_V2_LINE_EVENT_RISING_EDGE = 1
GPIO_V2_LINE_EVENT_FALLING_EDGE = 2


class GpioV2LineAttribute(ctypes.Structure):
    class _Value(ctypes.Union):
        _fields_ = [("flags", ctypes.c_uint64), ("values", ctypes.c_uint64), ("debounce_period_us", ctypes.c_uint32)]

    _anonymous_ = ("value",)
    _fields_ = [("id", ctypes.c_uint32), ("padding", ctypes.c_uint32), ("value", _Value)]


class GpioV2LineConfigAttribute(ctypes.Structure):
    _fields_ = [("attr", GpioV2LineAttribute), ("mask", ctypes.c_uint64)]


class GpioV2LineConfig(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("num_attrs", ctypes.c_uint32), ("padding", ctypes.c_uint32 * 5),
                ("attrs", GpioV2LineConfigAttribute * GPIO_V2_LINE_NUM_ATTRS_MAX)]


class GpioV2LineRequest(ctypes.Structure):
    _fields_ = [("offsets", ctypes.c_uint32 * GPIO_V2_LINES_MAX), ("consumer", ctypes.c_char * GPIO_MAX_NAME_SIZE),
                ("config", GpioV2LineConfig), ("num_lines", ctypes.c_uint32), ("event_buffer_size", ctypes.c_uint32),
                ("padding", ctypes.c_uint32 * 5), ("fd", ctypes.c_int32)]


class GpioV2LineValues(ctypes.Structure):
    _fields_ = [("bits", ctypes.c_uint64), ("mask", ctypes.c_uint64)]


class GpioV2LineEvent(ctypes.Structure):
    _fields_ = [("timestamp_ns", ctypes.c_uint64), ("id", ctypes.c_uint32), ("offset", ctypes.c_uint32),
                ("seqno", ctypes.c_uint32), ("line_seqno", ctypes.c_uint32), ("padding", ctypes.c_uint32 * 6)]


GPIO_V2_GET_LINE_IOCTL = _iowr(0xB4, 0x07, ctypes.sizeof(GpioV2LineRequest))
GPIO_V2_LINE_SET_CONFIG_IOCTL = _iowr(0xB4, 0x0D, ctypes.sizeof(GpioV2LineConfig))
GPIO_V2_LINE_GET_VALUES_IOCTL = _iowr(0xB4, 0x0E, ctypes.sizeof(GpioV2LineValues))
GPIO_V2_LINE_SET_VALUES_IOCTL = _iowr(0xB4, 0x0F, ctypes.sizeof(GpioV2LineValues))

_BIAS_FLAGS = {"none": 0, "as-is": 0, "pull-up": GPIO_V2_LINE_FLAG_BIAS_PULL_UP, "pull_up": GPIO_V2_LINE_FLAG_BIAS_PULL_UP,
               "pull-down": GPIO_V2_LINE_FLAG_BIAS_PULL_DOWN, "pull_down": GPIO_V2_LINE_FLAG_BIAS_PULL_DOWN,
               "disabled": GPIO_V2_LINE_FLAG_BIAS_DISABLED}
_EDGE_FLAGS = {"none": 0, "rising": GPIO_V2_LINE_FLAG_EDGE_RISING, "falling": GPIO_V2_LINE_FLAG_EDGE_FALLING,
               "both": GPIO_V2_LINE_FLAG_EDGE_RISING | GPIO_V2_LINE_FLAG_EDGE_FALLING}


def build_gpio_line_request(line: int, direction: str, bias: str = "none", edge: str = "none",
                            consumer: str = "r3_hal", output_value: int = 0, event_buffer_size: int = 0) -> GpioV2LineRequest:
    """Request one line. Edge detection requires an input line (kernel rule)."""
    if direction not in ("in", "out"):
        raise HalError("bad_argument", f"gpio direction {direction!r}")
    if bias not in _BIAS_FLAGS or edge not in _EDGE_FLAGS:
        raise HalError("bad_argument", f"gpio bias {bias!r} / edge {edge!r}")
    if direction == "out" and edge != "none":
        raise HalError("bad_argument", "edge detection needs an input line")
    request = GpioV2LineRequest()
    request.offsets[0] = line
    request.num_lines = 1
    request.consumer = consumer.encode()[:GPIO_MAX_NAME_SIZE - 1]
    flags = GPIO_V2_LINE_FLAG_INPUT if direction == "in" else GPIO_V2_LINE_FLAG_OUTPUT
    request.config.flags = flags | _BIAS_FLAGS[bias] | _EDGE_FLAGS[edge]
    request.event_buffer_size = event_buffer_size
    if direction == "out":
        request.config.num_attrs = 1
        attribute = request.config.attrs[0]
        attribute.attr.id = GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES
        attribute.attr.values = 1 if output_value else 0
        attribute.mask = 1  # bit 0 = first requested line
    return request


def parse_gpio_events(data: bytes) -> list[GpioV2LineEvent]:
    size = ctypes.sizeof(GpioV2LineEvent)
    return [GpioV2LineEvent.from_buffer_copy(data, i) for i in range(0, len(data) - size + 1, size)]


# --- termios2 (asm-generic/termbits.h) ----------------------------------------

NCCS2 = 19
BOTHER = 0o010000
CBAUD = 0o010017
IBSHIFT = 16


class Termios2(ctypes.Structure):
    _fields_ = [("c_iflag", ctypes.c_uint32), ("c_oflag", ctypes.c_uint32), ("c_cflag", ctypes.c_uint32),
                ("c_lflag", ctypes.c_uint32), ("c_line", ctypes.c_uint8), ("c_cc", ctypes.c_uint8 * NCCS2),
                ("c_ispeed", ctypes.c_uint32), ("c_ospeed", ctypes.c_uint32)]


TCGETS2 = _ior(ord("T"), 0x2A, ctypes.sizeof(Termios2))
TCSETS2 = _iow(ord("T"), 0x2B, ctypes.sizeof(Termios2))


def apply_custom_baud(settings: Termios2, baud: int) -> Termios2:
    """Select an arbitrary rate: c_cflag speed bits = BOTHER, speeds in c_[io]speed."""
    settings.c_cflag &= ~(CBAUD | (CBAUD << IBSHIFT)) & 0xFFFFFFFF
    settings.c_cflag |= BOTHER | (BOTHER << IBSHIFT)
    settings.c_ispeed = baud
    settings.c_ospeed = baud
    return settings


# --- spidev (linux/spi/spidev.h) ----------------------------------------------

SPI_IOC_MAGIC = ord("k")


class SpiIocTransfer(ctypes.Structure):
    _fields_ = [("tx_buf", ctypes.c_uint64), ("rx_buf", ctypes.c_uint64), ("len", ctypes.c_uint32),
                ("speed_hz", ctypes.c_uint32), ("delay_usecs", ctypes.c_uint16), ("bits_per_word", ctypes.c_uint8),
                ("cs_change", ctypes.c_uint8), ("tx_nbits", ctypes.c_uint8), ("rx_nbits", ctypes.c_uint8),
                ("word_delay_usecs", ctypes.c_uint8), ("pad", ctypes.c_uint8)]


def spi_ioc_message(count: int) -> int:
    size = ctypes.sizeof(SpiIocTransfer) * count
    return _iow(SPI_IOC_MAGIC, 0, size if size < (1 << _IOC_SIZEBITS) else 0)


SPI_IOC_WR_MODE = _iow(SPI_IOC_MAGIC, 1, 1)
SPI_IOC_WR_BITS_PER_WORD = _iow(SPI_IOC_MAGIC, 3, 1)
SPI_IOC_WR_MAX_SPEED_HZ = _iow(SPI_IOC_MAGIC, 4, 4)


# --- helpers ------------------------------------------------------------------

def _require_posix() -> None:
    if fcntl is None:
        raise HalError("unsupported_platform", "the linux backend needs a POSIX system")


def _os_error(error: OSError, device: str) -> HalError:
    if error.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
        return HalError("no_such_device", f"{device}: {error.strerror}")
    if error.errno in (errno.EACCES, errno.EPERM):
        return HalError("permission_denied", f"{device}: {error.strerror}")
    return HalError("io_error", f"{device}: {error.strerror}")


def _device_path(name: str, prefix: str) -> str:
    return name if name.startswith("/") else f"{prefix}{name}"


# --- implementations ----------------------------------------------------------

class MonotonicClock(Clock):
    def now(self) -> float:
        return time.monotonic()

    def sleep_until(self, t: float) -> None:
        while True:
            remaining = t - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(remaining)


class LinuxI2c(I2cBus):
    def __init__(self, bus: str) -> None:
        _require_posix()
        self.path = _device_path(bus, "/dev/i2c-")
        try:
            self._fd = os.open(self.path, os.O_RDWR)
        except OSError as error:
            raise HalError("no_such_device", f"{self.path}: {error.strerror}") from error
        self._lock = threading.Lock()

    def transfer(self, addr: int, write: bytes = b"", read_len: int = 0) -> bytes:
        data, _messages, read_buffer, _write_buffer = build_i2c_transfer(addr, bytes(write), read_len)
        with self._lock:
            try:
                fcntl.ioctl(self._fd, I2C_RDWR, data)
            except OSError as error:
                # ENXIO / EREMOTEIO (121): address not acknowledged.
                if error.errno in (errno.ENXIO, EREMOTEIO, errno.EIO):
                    raise HalError("i2c_nack", f"{self.path} addr 0x{addr:02x}") from error
                raise _os_error(error, self.path) from error
        return bytes(read_buffer) if read_buffer is not None else b""

    def close(self) -> None:
        os.close(self._fd)


class LinuxPwm(PwmChannel):
    """sysfs PWM. ``duty_cycle`` must never exceed ``period``, so the write order
    depends on whether the period grows or shrinks."""

    root = Path("/sys/class/pwm")

    def __init__(self, chip: str, channel: int, root: Path | None = None) -> None:
        self.root = root or self.root
        self.chip_path = self.root / (chip if chip.startswith("pwmchip") else f"pwmchip{chip}")
        self.path = self.chip_path / f"pwm{channel}"
        if not self.chip_path.is_dir():
            raise HalError("no_such_device", str(self.chip_path))
        if not self.path.is_dir():
            self._write(self.chip_path / "export", str(channel))
            deadline = time.monotonic() + 2.0
            # udev applies group permissions shortly after export.
            while not (self.path / "enable").exists() or not os.access(self.path / "period", os.W_OK):
                if time.monotonic() > deadline:
                    raise HalError("no_such_device", f"{self.path} did not appear after export")
                time.sleep(0.01)
        self._period = self._read_int("period")
        self._duty = self._read_int("duty_cycle")
        self._enabled = self._read_int("enable") == 1

    def _write(self, path: Path, value: str) -> None:
        try:
            path.write_text(value)
        except OSError as error:
            raise _os_error(error, str(path)) from error

    def _read_int(self, name: str) -> int:
        try:
            return int((self.path / name).read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def configure(self, period_ns: int, duty_ns: int, enabled: bool = True) -> None:
        period_ns, duty_ns = int(period_ns), max(0, min(int(duty_ns), int(period_ns)))
        if period_ns != self._period:
            if self._duty > period_ns:  # shrinking below the current duty: lower the duty first
                self._write(self.path / "duty_cycle", str(duty_ns))
                self._duty = duty_ns
            self._write(self.path / "period", str(period_ns))
            self._period = period_ns
        if duty_ns != self._duty:
            self._write(self.path / "duty_cycle", str(duty_ns))
            self._duty = duty_ns
        if bool(enabled) != self._enabled:
            self._write(self.path / "enable", "1" if enabled else "0")
            self._enabled = bool(enabled)


class LinuxGpio(GpioLine):
    def __init__(self, chip: str, line: int, direction: str = "in", bias: str = "none", edge: str = "none") -> None:
        _require_posix()
        self.chip_path = _device_path(chip, "/dev/")
        self.line, self.direction, self.edge = line, direction, edge
        request = build_gpio_line_request(line, direction, bias, edge, event_buffer_size=256 if edge != "none" else 0)
        try:
            chip_fd = os.open(self.chip_path, os.O_RDWR | os.O_CLOEXEC)
        except OSError as error:
            raise HalError("no_such_device", f"{self.chip_path}: {error.strerror}") from error
        try:
            fcntl.ioctl(chip_fd, GPIO_V2_GET_LINE_IOCTL, request)
        except OSError as error:
            code = "pin_busy" if error.errno == errno.EBUSY else "io_error"
            raise HalError(code, f"{self.chip_path} line {line}: {error.strerror}") from error
        finally:
            os.close(chip_fd)
        self._fd = request.fd
        self._lock = threading.Lock()
        self._count = 0
        self._last_t = 0.0
        self._closing = False
        self._thread: threading.Thread | None = None
        if edge != "none":
            self._thread = threading.Thread(target=self._edge_loop, name=f"gpio-edges-{line}", daemon=True)
            self._thread.start()

    def _edge_loop(self) -> None:
        size = ctypes.sizeof(GpioV2LineEvent)
        poller = select.poll()
        poller.register(self._fd, select.POLLIN)
        while not self._closing:
            if not poller.poll(100):
                continue
            try:
                data = os.read(self._fd, size * 64)
            except OSError:
                return
            for event in parse_gpio_events(data):
                if event.id == GPIO_V2_LINE_EVENT_RISING_EDGE:
                    with self._lock:
                        self._count += 1
                        self._last_t = event.timestamp_ns / 1e9  # CLOCK_MONOTONIC by default

    def read(self) -> int:
        values = GpioV2LineValues(0, 1)
        fcntl.ioctl(self._fd, GPIO_V2_LINE_GET_VALUES_IOCTL, values)
        return int(values.bits & 1)

    def write(self, value: int) -> None:
        if self.direction != "out":
            raise HalError("bad_argument", "line is not an output")
        fcntl.ioctl(self._fd, GPIO_V2_LINE_SET_VALUES_IOCTL, GpioV2LineValues(1 if value else 0, 1))

    def count_edges(self) -> tuple[int, float]:
        if self.edge == "none":
            raise HalError("bad_argument", "line was configured without edge detection")
        with self._lock:
            return self._count, self._last_t

    def close(self) -> None:
        self._closing = True
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        os.close(self._fd)


_STANDARD_BAUDS = (50, 75, 110, 134, 150, 200, 300, 600, 1200, 1800, 2400, 4800, 9600, 19200, 38400, 57600,
                   115200, 230400, 460800, 500000, 576000, 921600, 1000000, 1152000, 1500000, 2000000)


class LinuxUart(Uart):
    def __init__(self, device: str, baud: int) -> None:
        _require_posix()
        self.device = device
        try:
            self._fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as error:
            raise _os_error(error, device) from error
        self._configure_raw()
        self._set_baud(int(baud))
        termios.tcflush(self._fd, termios.TCIOFLUSH)

    def _configure_raw(self) -> None:
        # Equivalent of cfmakeraw() plus 8N1, receiver on, no modem control, no flow control.
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(self._fd)
        iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR | termios.IGNCR
                   | termios.ICRNL | termios.IXON | termios.IXOFF | termios.IXANY)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
        cc[termios.VMIN], cc[termios.VTIME] = 0, 0
        termios.tcsetattr(self._fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])

    def _set_baud(self, baud: int) -> None:
        constant = getattr(termios, f"B{baud}", None)
        if baud in _STANDARD_BAUDS and constant is not None:
            attributes = termios.tcgetattr(self._fd)
            attributes[4] = attributes[5] = constant
            termios.tcsetattr(self._fd, termios.TCSANOW, attributes)
            return
        if not IS_LINUX:
            raise HalError("unsupported_baud", f"{baud} baud needs Linux termios2")
        settings = Termios2()
        try:
            fcntl.ioctl(self._fd, TCGETS2, settings)
            fcntl.ioctl(self._fd, TCSETS2, apply_custom_baud(settings, baud))
        except OSError as error:
            raise HalError("unsupported_baud", f"{self.device} {baud} baud: {error.strerror}") from error

    def write(self, data: bytes) -> int:
        view, written = memoryview(bytes(data)), 0
        while written < len(view):
            try:
                written += os.write(self._fd, view[written:])
            except BlockingIOError:
                select.select([], [self._fd], [], 0.1)
        return written

    def read(self, max_bytes: int = 4096, timeout: float = 0.0) -> bytes:
        readable, _, _ = select.select([self._fd], [], [], max(0.0, timeout))
        if not readable:
            return b""
        try:
            return os.read(self._fd, max_bytes)
        except BlockingIOError:
            return b""

    def close(self) -> None:
        os.close(self._fd)


class LinuxSpi(SpiDevice):
    def __init__(self, bus: str, cs: int, speed_hz: int, mode: int) -> None:
        _require_posix()
        if bus.startswith("/dev/"):
            self.path = bus if "." in Path(bus).name else f"{bus}.{cs}"
        else:
            self.path = f"/dev/spidev{bus.removeprefix('spi')}.{cs}"
        self.speed_hz = int(speed_hz)
        try:
            self._fd = os.open(self.path, os.O_RDWR)
            fcntl.ioctl(self._fd, SPI_IOC_WR_MODE, ctypes.c_uint8(mode))
            fcntl.ioctl(self._fd, SPI_IOC_WR_BITS_PER_WORD, ctypes.c_uint8(8))
            fcntl.ioctl(self._fd, SPI_IOC_WR_MAX_SPEED_HZ, ctypes.c_uint32(self.speed_hz))
        except OSError as error:
            raise _os_error(error, self.path) from error

    def transfer(self, data: bytes) -> bytes:
        tx = ctypes.create_string_buffer(bytes(data), len(data))
        rx = ctypes.create_string_buffer(len(data))
        message = SpiIocTransfer(tx_buf=ctypes.addressof(tx), rx_buf=ctypes.addressof(rx), len=len(data),
                                 speed_hz=self.speed_hz, bits_per_word=8)
        try:
            fcntl.ioctl(self._fd, spi_ioc_message(1), message)
        except OSError as error:
            raise _os_error(error, self.path) from error
        return rx.raw

    def close(self) -> None:
        os.close(self._fd)


class LinuxHal(Hal):
    """Real-hardware HAL. ``bindings()`` is empty: on the Pi, bindings come from the
    module's deployed configuration."""

    def __init__(self) -> None:
        self.clock = MonotonicClock()
        self._open: list[object] = []
        self._i2c: dict[str, LinuxI2c] = {}

    def gpio(self, chip: str, line: int, direction: str = "in", bias: str = "none", edge: str = "none") -> GpioLine:
        line_handle = LinuxGpio(chip, line, direction, bias, edge)
        self._open.append(line_handle)
        return line_handle

    def pwm(self, chip: str, channel: int) -> PwmChannel:
        return LinuxPwm(chip, channel)

    def i2c(self, bus: str) -> I2cBus:
        if bus not in self._i2c:  # one fd per adapter, shared by all devices on it
            self._i2c[bus] = LinuxI2c(bus)
            self._open.append(self._i2c[bus])
        return self._i2c[bus]

    def spi(self, bus: str, cs: int, speed_hz: int = 1_000_000, mode: int = 0) -> SpiDevice:
        device = LinuxSpi(bus, cs, speed_hz, mode)
        self._open.append(device)
        return device

    def uart(self, device: str, baud: int) -> Uart:
        port = LinuxUart(device, baud)
        self._open.append(port)
        return port

    def close(self) -> None:
        for handle in reversed(self._open):
            try:
                handle.close()  # type: ignore[attr-defined]
            except OSError:
                pass
        self._open.clear()
        self._i2c.clear()
