"""Proof that the KiCad flow never opens a window.

The flow only ever starts ``kicad-cli``, KiCad's Python interpreter (``pcbnew``
module) and Java (Freerouting with ``-Djava.awt.headless=true``). A
:class:`GuiWatch` samples the process table while the flow runs and records any
KiCad GUI application (the project manager, eeschema, pcbnew and the other
editors, on macOS or Linux) that appears. The flow fails if one does.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

# Executables of KiCad's GUI programs (macOS bundles and Linux binaries); kicad-cli is not among them.
GUI_PATTERN = re.compile(
    r"(KiCad\.app/Contents/MacOS/kicad(\s|$)|/(PCB Editor|Schematic Editor|GerbView|Image Converter|PCB Calculator|"
    r"Page Layout Editor)\.app/|(^|/)(kicad|eeschema|pcbnew|gerbview|pl_editor|bitmap2component|pcb_calculator)(\s|$)|"
    r"KiCad\.app/Contents/Applications/)")


def gui_processes() -> list[str]:
    """Command lines of running KiCad GUI programs (empty when none)."""
    # ``comm`` is the executable path only, so a shell whose arguments mention "pcbnew" is not a match.
    out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        exe = line.strip().split(None, 1)[-1] if line.strip() else ""
        if exe.endswith("kicad-cli") or "python" in exe.lower().rsplit("/", 1)[-1]:
            continue
        if GUI_PATTERN.search(exe + " "):
            found.append(line.strip())
    return found


# macOS: read the window server's list (CoreGraphics, read-only) for windows owned by anything the flow runs.
_WINDOWS_JXA = """
ObjC.import('CoreGraphics');
var list = ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionAll, 0));
var out = [];
for (var i = 0; i < list.count; i++) {
  var w = list.objectAtIndex(i);
  out.push(ObjC.unwrap(w.objectForKey('kCGWindowOwnerPID')) + '\\t' + ObjC.unwrap(w.objectForKey('kCGWindowOwnerName')));
}
out.join('\\n');
"""
WINDOW_OWNER = re.compile(r"kicad|pcbnew|eeschema|python|java|freerouting", re.I)


def tool_windows() -> list[str]:
    """``pid<TAB>owner`` of on-screen or off-screen windows owned by KiCad, Python or Java (macOS only)."""
    if sys.platform != "darwin":
        return []
    out = subprocess.run(["osascript", "-l", "JavaScript", "-e", _WINDOWS_JXA], capture_output=True, text=True).stdout
    return sorted({line for line in out.splitlines() if WINDOW_OWNER.search(line)})


@dataclass
class GuiWatch:
    """Context manager sampling for KiCad GUI processes every ``interval`` seconds."""

    interval: float = 0.25
    seen: list[str] = field(default_factory=list)
    windows: list[str] = field(default_factory=list)
    samples: int = 0
    window_samples: int = 0
    preexisting: list[str] = field(default_factory=list)
    preexisting_windows: list[str] = field(default_factory=list)

    def __enter__(self) -> "GuiWatch":
        self.preexisting = gui_processes()
        self.preexisting_windows = tool_windows()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            for proc in gui_processes():
                if proc not in self.preexisting and proc not in self.seen:
                    self.seen.append(proc)
            self.samples += 1
            if self.samples % 4 == 1:          # window list every ~4 process samples (osascript is slower)
                for window in tool_windows():
                    if window not in self.preexisting_windows and window not in self.windows:
                        self.windows.append(window)
                self.window_samples += 1
            time.sleep(self.interval)

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()

    def summary(self) -> dict:
        return {"gui_processes_started": self.seen, "samples": self.samples,
                "preexisting_gui_processes": self.preexisting, "tool_windows_opened": self.windows,
                "window_samples": self.window_samples}

    @property
    def clean(self) -> bool:
        return not self.seen and not self.windows
