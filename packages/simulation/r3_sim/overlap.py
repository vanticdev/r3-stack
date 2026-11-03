"""Run MuJoCo's two step halves concurrently with the co-simulation's Python work.

``mj_step`` is ``mj_step1`` (kinematics, collision, constraint assembly,
velocity-dependent forces) followed by ``mj_step2`` (actuation, constraint
solve, integration); MuJoCo documents the split so controls can be computed
in between. The kernel uses it to overlap, without changing any result:

* ``mj_step1`` of step k with the electrical solve of step k (whose inputs were
  read before), or with the deferred sensor/record work of step k-1;
* ``mj_step2`` of step k with the electrical solve of step k+1, whose inputs
  (software outputs, motor speed from ``mj_step1``) are final at that point.

The Python bindings keep the GIL during MuJoCo calls, so the worker calls the
same C functions through ``ctypes`` (which releases the GIL). The GIL switch
interval is shortened only while a call is in flight, so the worker gets the
GIL promptly to start it and to report completion. ``R3_SIM_OVERLAP=0`` runs
everything serially in the calling thread (bit-identical results).
"""

from __future__ import annotations

import ctypes
import glob
import os
import sys
import threading
from typing import Any

import mujoco

OVERLAP_SWITCH_INTERVAL = 5e-6  # s, GIL hand-over latency while a MuJoCo call runs in the worker (measured best)


def _library() -> Any | None:
    paths = sorted(glob.glob(os.path.join(os.path.dirname(mujoco.__file__), "libmujoco*")))
    if not paths:
        return None
    try:
        lib = ctypes.CDLL(paths[-1])
        for name in ("mj_step1", "mj_step2"):
            getattr(lib, name).argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            getattr(lib, name).restype = None
        return lib
    except (OSError, AttributeError):
        return None


def overlap_enabled() -> bool:
    return os.environ.get("R3_SIM_OVERLAP", "1") != "0"


class MujocoWorker:
    """Background ``mj_step1`` / ``mj_step2`` for one (model, data) pair; serial if unavailable."""

    def __init__(self, model: Any, data: Any) -> None:
        self.model, self.data = model, data
        self._lib = _library() if overlap_enabled() else None
        address = (getattr(model, "_address", None), getattr(data, "_address", None))
        self.enabled = self._lib is not None and None not in address
        self._pending = False
        self._closed = False
        self._error: BaseException | None = None
        if not self.enabled:
            return
        self._args = (ctypes.c_void_p(address[0]), ctypes.c_void_p(address[1]))
        self._function: Any = None
        self._request = threading.Event()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mujoco-step", daemon=True)
        self._thread.start()
        self._interval = sys.getswitchinterval()

    def _run(self) -> None:
        while True:
            self._request.wait()
            self._request.clear()
            if self._function is None:
                return
            try:
                self._function(*self._args)
            except BaseException as error:
                self._error = error
            finally:
                self._done.set()

    def start(self, name: str) -> None:
        """Begin ``mj_step1`` or ``mj_step2`` (runs inline when the worker is disabled)."""
        if self._closed:
            raise RuntimeError('MuJoCo worker is closed')
        self.wait()
        if not self.enabled:
            getattr(mujoco, name)(self.model, self.data)
            return
        self._interval = sys.getswitchinterval()
        sys.setswitchinterval(OVERLAP_SWITCH_INTERVAL)
        self._function = getattr(self._lib, name)
        self._done.clear()
        self._pending = True
        self._request.set()

    def wait(self) -> None:
        if not self._pending:
            return
        self._done.wait()
        self._pending = False
        sys.setswitchinterval(self._interval)
        if self._error is not None:
            error, self._error = self._error, None
            raise error

    def close(self) -> None:
        """Finish pending native work, stop the waiting thread and release its references."""
        if self._closed:
            return
        try:
            self.wait()
        finally:
            self._closed = True
            if self.enabled:
                self._function = None
                self._request.set()
                self._thread.join()


__all__ = ["MujocoWorker", "overlap_enabled"]
