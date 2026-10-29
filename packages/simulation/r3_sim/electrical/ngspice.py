"""ngspice shared-library driver (``libngspice``) for lockstep co-simulation.

Uses only the official shared API (``sharedspice.h``) through ctypes:

* ``ngSpice_Init`` with ``SendChar`` (log capture), ``ControlledExit``,
  ``SendData`` (results of every accepted time point) and ``BGThreadRunning``;
* ``ngSpice_Init_Sync`` with ``GetVSRCData``/``GetISRCData`` (values of
  ``EXTERNAL`` voltage/current sources: quantities owned by MuJoCo, the
  firmware or R3's device models) and ``GetSyncData`` (time-step control);
* ``ngSpice_Circ``, ``ngSpice_Command`` (``bg_run``, ``bg_halt``, ``op``).

Lockstep transient: ``bg_run`` starts one long ``.tran`` in ngspice's
background thread. Before every new time step ngspice calls ``GetSyncData``
(``loc = 0``, and ``loc = 1`` after a rejected step, i.e. a Newton failure or
a truncation-error rejection that ngspice backs up from). When the last
accepted point is the master's target time the callback blocks until the
master supplies the next inputs and target. It also caps ``CKTdelta`` so that
no step crosses the target (or a corner of a time-dependent source), landing
exactly on it; the last two steps before it are equalised like ngspice does
for breakpoints. (Registering every target with ``ngSpice_SetBkpt`` instead
makes ngspice's own breakpoint equalisation halve the step forever when the
proposed step lands exactly on the breakpoint.) Inside one master step
ngspice chooses its own time points (LTE control, rejections, backtracking).
``.save none`` keeps every output vector at length 1, so memory stays constant
over arbitrarily long runs; ``SendData`` delivers the values.

EXTERNAL sources are declared without a ``dc`` value: with one, ngspice uses
the DC value instead of the callback in every DC operating point.

Instances: ngspice keeps global state per loaded library. Several concurrent
circuits therefore each load a private copy of the library file (the
mechanism ngspice documents for parallel instances, distinguished by the
``ident`` passed to ``ngSpice_Init_Sync``). Copies are pooled and reused.
Everything is headless and deterministic: the background thread only ever
runs while the master thread waits for it.
"""

from __future__ import annotations

import bisect
import ctypes
import ctypes.util
import os
import platform
import shutil
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Sequence

LIBRARY_CANDIDATES = (
    "/opt/homebrew/lib/libngspice.dylib",
    "/usr/local/lib/libngspice.dylib",
    "/usr/lib/x86_64-linux-gnu/libngspice.so.0",
    "/usr/lib/aarch64-linux-gnu/libngspice.so.0",
    "/usr/lib/x86_64-linux-gnu/libngspice.so",
    "/usr/lib/aarch64-linux-gnu/libngspice.so",
    "/usr/local/lib/libngspice.so",
    "/usr/lib/libngspice.so",
)


class NgSpiceError(RuntimeError):
    """ngspice reported an error (parse failure, no convergence, ...)."""


def find_library() -> str | None:
    """Path of libngspice: ``$R3_NGSPICE_LIBRARY``, the usual install locations, then the linker search path."""
    explicit = os.environ.get("R3_NGSPICE_LIBRARY")
    if explicit:
        return explicit if Path(explicit).exists() else None
    for candidate in LIBRARY_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ctypes.util.find_library("ngspice")


class _VecValues(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("creal", ctypes.c_double), ("cimag", ctypes.c_double),
                ("is_scale", ctypes.c_bool), ("is_complex", ctypes.c_bool)]


class _VecValuesAll(ctypes.Structure):
    _fields_ = [("veccount", ctypes.c_int), ("vecindex", ctypes.c_int),
                ("vecsa", ctypes.POINTER(ctypes.POINTER(_VecValues)))]


class _VectorInfo(ctypes.Structure):
    _fields_ = [("v_name", ctypes.c_char_p), ("v_type", ctypes.c_int), ("v_flags", ctypes.c_short),
                ("v_realdata", ctypes.POINTER(ctypes.c_double)), ("v_compdata", ctypes.c_void_p),
                ("v_length", ctypes.c_int)]


_SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
_SendStat = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
_ControlledExit = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
_SendData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(_VecValuesAll), ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
class _VecInfo(ctypes.Structure):
    _fields_ = [("number", ctypes.c_int), ("vecname", ctypes.c_char_p), ("is_real", ctypes.c_bool),
                ("pdvec", ctypes.c_void_p), ("pdvecscale", ctypes.c_void_p)]


class _VecInfoAll(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("title", ctypes.c_char_p), ("date", ctypes.c_char_p),
                ("type", ctypes.c_char_p), ("veccount", ctypes.c_int), ("vecs", ctypes.POINTER(ctypes.POINTER(_VecInfo)))]


_SendInitData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(_VecInfoAll), ctypes.c_int, ctypes.c_void_p)
_BGThreadRunning = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
_GetSourceData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_double), ctypes.c_double, ctypes.c_void_p,
                                  ctypes.c_int, ctypes.c_void_p)
_GetSyncData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_double, ctypes.POINTER(ctypes.c_double), ctypes.c_double,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)

_PyGILState_Ensure = ctypes.pythonapi.PyGILState_Ensure
_PyGILState_Ensure.restype = ctypes.c_int
_PyGILState_Release = ctypes.pythonapi.PyGILState_Release
_PyGILState_Release.argtypes = [ctypes.c_int]

_POOL_LOCK = threading.Lock()
_POOL: list["NgSpice"] = []
_COPIES: list[str] = []
_COPY_DIR: str | None = None


def _library_copy(source: str, index: int) -> str:
    """Instance 0 uses the installed library; others a private copy of the file."""
    global _COPY_DIR
    if index == 0:
        return source
    if _COPY_DIR is None:
        _COPY_DIR = tempfile.mkdtemp(prefix="r3_ngspice_")
    suffix = ".dylib" if platform.system() == "Darwin" else ".so"
    target = Path(_COPY_DIR) / f"libngspice_r3_{index}{suffix}"
    if not target.exists():
        shutil.copy2(os.path.realpath(source), target)
    return str(target)


class NgSpice:
    """One ngspice instance (one loaded copy of libngspice)."""

    TIME_EPS = 1e-12  # s, tolerance when comparing ngspice time with the master's target

    def __init__(self, library: str, ident: int) -> None:
        self.ident = ident
        self.library_path = library
        self.lib = ctypes.CDLL(library, mode=getattr(ctypes, "RTLD_LOCAL", 0))
        self._declare()
        self.log: deque[str] = deque(maxlen=400)
        self.errors: list[str] = []
        self._running = False
        self._gil_state: int | None = None
        self._exit_requested = False
        # lockstep state
        self._cond = threading.Condition()
        self._target = 0.0
        self._at_target = False  # ngspice has accepted the target time point and waits
        self._released = False
        self._closing = False
        self._time = 0.0
        self.source_values: dict[str, float] = {}
        self.time_sources: dict[str, Callable[[float], float]] = {}  # EXTERNAL sources that depend on ngspice time
        self.edges: list[float] = []  # sorted corner times of the time sources within the current step
        self.plot_vectors: list[str] = []
        self.plot_serial = 0  # incremented for every new output plot (vector storage is reallocated)
        self._source_by_address: dict[int, str] = {}
        self._capture: Callable[[ctypes.POINTER(_VecValuesAll)], None] | None = None
        self.accepted_points = 0
        self.rejected_steps = 0
        self._keep = [
            _SendChar(self._on_char), _SendStat(0), _ControlledExit(self._on_exit), _SendData(self._on_data),
            _SendInitData(self._on_init_data), _BGThreadRunning(self._on_bg),
            _GetSourceData(self._on_vsrc), _GetSourceData(self._on_isrc), _GetSyncData(self._on_sync),
        ]
        send_char, send_stat, exit_cb, send_data, send_init, bg, vsrc, isrc, sync = self._keep
        self.lib.ngSpice_Init(send_char, None, exit_cb, send_data, send_init, bg, None)
        ident_c = ctypes.c_int(ident)
        self.lib.ngSpice_Init_Sync(vsrc, isrc, sync, ctypes.byref(ident_c), None)

    def _declare(self) -> None:
        lib = self.lib
        lib.ngSpice_Init.argtypes = [_SendChar, ctypes.c_void_p, _ControlledExit, _SendData, _SendInitData,
                                     _BGThreadRunning, ctypes.c_void_p]
        lib.ngSpice_Init.restype = ctypes.c_int
        lib.ngSpice_Init_Sync.argtypes = [_GetSourceData, _GetSourceData, _GetSyncData, ctypes.POINTER(ctypes.c_int),
                                          ctypes.c_void_p]
        lib.ngSpice_Init_Sync.restype = ctypes.c_int
        lib.ngSpice_Command.argtypes = [ctypes.c_char_p]
        lib.ngSpice_Command.restype = ctypes.c_int
        lib.ngSpice_Circ.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
        lib.ngSpice_Circ.restype = ctypes.c_int
        lib.ngGet_Vec_Info.argtypes = [ctypes.c_char_p]
        lib.ngGet_Vec_Info.restype = ctypes.POINTER(_VectorInfo)
        lib.ngSpice_SetBkpt.argtypes = [ctypes.c_double]
        lib.ngSpice_SetBkpt.restype = ctypes.c_bool
        lib.ngSpice_running.argtypes = []
        lib.ngSpice_running.restype = ctypes.c_bool
        lib.ngSpice_CurPlot.restype = ctypes.c_char_p
        lib.ngSpice_AllVecs.argtypes = [ctypes.c_char_p]
        lib.ngSpice_AllVecs.restype = ctypes.POINTER(ctypes.c_char_p)

    # ----------------------------------------------------------------- pool
    @classmethod
    def acquire(cls) -> "NgSpice":
        library = find_library()
        if library is None:
            raise FileNotFoundError("libngspice not found (set R3_NGSPICE_LIBRARY)")
        with _POOL_LOCK:
            if _POOL:
                instance = _POOL.pop()
                instance._reset()
                return instance
            index = len(_COPIES)
            path = _library_copy(library, index)
            _COPIES.append(path)
        return cls(path, index)

    def release(self) -> None:
        self.halt()
        with _POOL_LOCK:
            if self not in _POOL:
                _POOL.append(self)

    def _reset(self) -> None:
        self.halt()
        self.command("destroy all")
        self.command("remcirc")
        self.errors.clear()
        self.log.clear()
        self.source_values = {}
        self._source_by_address = {}
        self._capture = None
        self.accepted_points = 0
        self.rejected_steps = 0

    # ------------------------------------------------------------ callbacks
    def _on_char(self, text: bytes, ident: int, user: int) -> int:
        line = text.decode(errors="replace")
        self.log.append(line)
        lowered = line.lower()
        if lowered.startswith("stderr error") or "error:" in lowered or "fatal" in lowered:
            self.errors.append(line)
        return 0

    def _on_exit(self, status: int, immediate: bool, quit_: bool, ident: int, user: int) -> int:
        self._exit_requested = True
        self.errors.append(f"ngspice requested exit (status {status})")
        return 0

    def _on_bg(self, exited: bool, ident: int, user: int) -> int:
        # Called on ngspice's worker thread with its ``fl_exited`` flag: False when
        # the thread starts, True just before it ends. The worker is not a Python
        # thread, so every ctypes callback would create and destroy a Python thread
        # state (~5 us each, dozens per time point); holding one PyGILState for the
        # thread's lifetime makes the callbacks cheap.
        if not exited:
            self._gil_state = _PyGILState_Ensure()
            return 0
        if self._gil_state is not None:
            _PyGILState_Release(self._gil_state)
            self._gil_state = None
        with self._cond:
            self._running = False
            self._cond.notify_all()
        return 0

    def _on_init_data(self, info: ctypes.POINTER(_VecInfoAll), ident: int, user: int) -> int:
        plot = info.contents
        self.plot_vectors = [plot.vecs[i].contents.vecname.decode().lower() for i in range(plot.veccount)]
        self.plot_serial += 1
        return 0

    def _on_data(self, values: ctypes.POINTER(_VecValuesAll), count: int, ident: int, user: int) -> int:
        self.accepted_points += 1
        if self._capture is not None:
            self._capture(values)
        return 0

    def _source(self, address: int) -> str:
        name = self._source_by_address.get(address)
        if name is None:
            name = ctypes.cast(address, ctypes.c_char_p).value.decode().lower()
            self._source_by_address[address] = name
        return name

    def _on_vsrc(self, value: ctypes.POINTER(ctypes.c_double), time: float, name: int, ident: int, user: int) -> int:
        source = self._source(name)
        function = self.time_sources.get(source)
        value[0] = function(time) if function is not None else self.source_values.get(source, 0.0)
        return 0

    _on_isrc = _on_vsrc

    def _on_sync(self, time: float, delta: ctypes.POINTER(ctypes.c_double), old_delta: float, redo: int,
                 ident: int, location: int, user: int) -> int:
        if not self._running:  # foreground analyses (``op``) are not synchronized
            return 0
        if redo:
            self.rejected_steps += 1
        if location == 0 and not redo and time >= self._target - self.TIME_EPS and not self._closing:
            with self._cond:
                self._time = time
                self._at_target = True
                self._released = False
                self._cond.notify_all()
                while not self._released and not self._closing:
                    self._cond.wait()
        # Land exactly on the master's target (and on the corners of time-dependent
        # sources, e.g. PWM edges). When one more step of the proposed size cannot
        # reach it, split the rest into two equal steps (ngspice's own breakpoint
        # equalisation) instead of leaving a sliver.
        stop = self._target
        if self.edges:
            k = bisect.bisect_right(self.edges, time + self.TIME_EPS)
            if k < len(self.edges) and self.edges[k] < stop:
                stop = self.edges[k]
        remaining = stop - time
        if remaining > self.TIME_EPS:
            proposed = delta[0]
            if proposed >= remaining:
                delta[0] = remaining
            elif 2.0 * proposed > remaining:
                delta[0] = 0.5 * remaining
        return 0

    # ------------------------------------------------------------- commands
    def command(self, text: str) -> None:
        if self.lib.ngSpice_Command(text.encode()) != 0:
            raise NgSpiceError(f"ngspice command failed: {text}\n" + "\n".join(list(self.log)[-20:]))

    def load(self, lines: Sequence[str]) -> None:
        """Send a netlist (title line first, ``.end`` last)."""
        self.errors.clear()
        encoded = [line.encode() for line in lines] + [None]
        array = (ctypes.c_char_p * len(encoded))(*encoded)
        status = self.lib.ngSpice_Circ(array)
        if status != 0 or self.errors:
            raise NgSpiceError("ngspice rejected the netlist:\n" + "\n".join(self.errors[-20:] or list(self.log)[-20:]))

    # ------------------------------------------------------------- lockstep
    def start_transient(self, capture: Callable[[ctypes.POINTER(_VecValuesAll)], None]) -> None:
        """``bg_run`` the loaded ``.tran``; returns once ngspice has solved t = 0 and waits."""
        self._capture = capture
        self._target = 0.0
        self._at_target = False
        self._closing = False
        self._running = True
        self.errors.clear()
        self.command("bg_run")
        self._wait()

    def advance(self, target: float) -> float:
        """Release ngspice up to ``target`` (s); returns once that point is accepted."""
        with self._cond:
            self._target = target
            self._at_target = False
            self._released = True
            self._cond.notify_all()
        self._wait()
        return self._time

    def _wait(self) -> None:
        with self._cond:
            while not self._at_target and self._running:
                self._cond.wait()
            if not self._at_target:
                raise NgSpiceError("ngspice transient stopped: " + "\n".join(self.errors[-20:] or list(self.log)[-20:]))

    def halt(self) -> None:
        """Stop a background transient (if any) and wait for its thread to exit."""
        if not self._running:
            return
        with self._cond:
            self._closing = True
            self._cond.notify_all()
        self.lib.ngSpice_Command(b"bg_halt")
        with self._cond:
            self._cond.wait_for(lambda: not self._running, timeout=5.0)
        self._running = False
        self._at_target = False
