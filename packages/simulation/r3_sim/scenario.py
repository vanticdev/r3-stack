"""Scenario runner: real robot software (subprocess) against the digital twin.

``run_scenario`` launches the software with ``R3_HAL=virtual`` and
``R3_HAL_ENDPOINT`` pointing at the twin HAL server, then runs the lockstep
loop. While the computer is unpowered (brownout) or booting, the twin runs
freely; after power returns the software is relaunched once the modeled boot
time has elapsed. A crashed program is restarted by the target-side watchdog
after ``WATCHDOG_RESTART_DELAY`` (BLITZ watchdog behavior), up to
``MAX_RESTARTS`` times. PWM outputs keep their last configuration when the
process exits (Linux sysfs PWM semantics) and only stop on power loss.

Fault ``software_freeze`` (``scenario.faults``): at its time the program is
stopped with SIGSTOP while it waits for a HAL reply (that reply never comes),
exactly as a hung main loop on the Pi: the PWM peripheral keeps its last
duty, GPIO lines keep their level (the heartbeat stops), and the twin runs on
freely. A frozen process is not restarted (the hang is not a crash).
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.project import Project

from .evaluation import evaluate, rc_gaps
from .hal_server import TwinHalServer
from .kernel import CoSimKernel
from .vehicle.mjcf import write_mjcf

WATCHDOG_RESTART_DELAY = 2.0
MAX_RESTARTS = 3
CONNECT_TIMEOUT = 60.0

CHECK_CODES = {
    "laps_completed": "mission_incomplete",
    "max_cross_track_error_m": "cross_track_error_exceeded",
    "failsafe_events": "unexpected_failsafe",
    "min_battery_voltage_v": "battery_undervoltage",
    "brownouts": "computer_brownout",
    "min_top_speed_mps": "top_speed_below_requirement",
    "link_lost_beyond_m": "radio_range_insufficient",
    "stop_within_m_of_link_loss": "failsafe_stop_distance_exceeded",
    "stop_within_m_of_fault": "failsafe_stop_distance_exceeded",
    "arbiter_neutral_within_s": "arbiter_failsafe_too_slow",
    "min_cell_voltage_v": "battery_undervoltage",
    "low_voltage_cutoff_engaged": "low_voltage_cutoff_not_engaged",
    "min_wire_clearance_m": "wire_clearance_violation",
}


@dataclass
class ScenarioOutcome:
    scenario: str
    passed: bool
    summary: dict[str, Any]
    diagnostics: list[Diagnostic] = field(default_factory=list)
    assumptions: list[tuple[str, str]] = field(default_factory=list)
    artifacts: list[tuple[str, str, str]] = field(default_factory=list)  # (path rel to build/, kind, description)
    out_dir: Path | None = None


def default_software_command(project: Project) -> list[str] | None:
    modules = project.spec.software.modules
    if not modules:
        return None
    module = modules[0]
    entry = project.root / "backend" / module.language / module.name / (module.entrypoint or "main.py")
    if module.language != "python" or not entry.exists():
        return None
    return [sys.executable, str(entry)]


def _python_path(extra: list[Path]) -> str:
    import r3_hal

    paths = [str(Path(r3_hal.__file__).resolve().parent.parent), *(str(p) for p in extra)]
    current = os.environ.get("PYTHONPATH")
    return os.pathsep.join(paths + ([current] if current else []))


class _Software:
    """The robot program as an OS process (one per boot)."""

    def __init__(self, cmd: list[str] | None, kernel: CoSimKernel, server: TwinHalServer, log_path: Path, cwd: Path) -> None:
        # Relative paths that exist from the caller's directory are made absolute (the program runs in the project root).
        self.cmd = [str(Path(arg).resolve()) if not Path(arg).is_absolute() and Path(arg).exists() else arg
                    for arg in cmd] if cmd else cmd
        self.kernel = kernel
        self.server = server
        self.log_path = log_path
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.launches: list[dict[str, Any]] = []
        self.restarts = 0
        self.gave_up = cmd is None
        self.restart_at: float | None = None
        self.frozen = False
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")

    def launch(self) -> bool:
        assert self.cmd is not None
        env = os.environ.copy()
        script_dirs = [Path(arg).resolve().parent for arg in self.cmd if arg.endswith(".py")]
        env.update({
            "R3_HAL": "virtual", "R3_HAL_ENDPOINT": self.server.endpoint, "PYTHONUNBUFFERED": "1",
            "R3_SOFTWARE_CONFIG": json.dumps(self.kernel.software_config()), "R3_SIM_SCENARIO": self.kernel.scenario.id,
            "R3_PROJECT_ROOT": str(self.kernel.project.root), "PYTHONPATH": _python_path(script_dirs),
        })
        with self.log_path.open("a") as log:
            log.write(f"=== boot {len(self.launches) + 1} at sim t={self.kernel.t:.3f}s: {' '.join(self.cmd)}\n")
        log = self.log_path.open("a")
        self.proc = subprocess.Popen(self.cmd, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=str(self.cwd))
        log.close()
        record = {"t": round(self.kernel.t, 6), "pid": self.proc.pid, "connected": False}
        self.launches.append(record)
        deadline = time.monotonic() + CONNECT_TIMEOUT
        while time.monotonic() < deadline:
            if self.server.accept(0.25):
                record["connected"] = True
                return True
            if self.proc.poll() is not None:
                break
        record["exit_code"] = self.proc.poll()
        self.kill()
        return False

    def freeze(self) -> None:
        """Stop the program (SIGSTOP): its main loop hangs in the HAL call it is blocked in."""
        if self.proc is not None and self.proc.poll() is None:
            os.kill(self.proc.pid, signal.SIGSTOP)
        self.frozen = True
        self.gave_up = True  # a hung process is not restarted by the watchdog's exit handling
        if self.launches:
            self.launches[-1]["frozen_t"] = round(self.kernel.t, 6)

    def kill(self, reason: str = "") -> None:
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                if self.frozen:
                    os.kill(self.proc.pid, signal.SIGCONT)  # deliver the pending SIGTERM
                try:
                    self.proc.wait(2.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            if self.launches:
                self.launches[-1].setdefault("exit_code", self.proc.returncode)
                if reason:
                    self.launches[-1]["ended"] = reason
                self.launches[-1]["end_t"] = round(self.kernel.t, 6)
        self.proc = None
        self.server.close_connection()
        self.kernel.software_running = False


def run_scenario(project: Project, scenario_id: str, software_cmd: list[str] | None = None, *,
                 out_dir: Path | None = None, render: bool = True, seed: int | None = None,
                 progress: Callable[[str], None] | None = None) -> ScenarioOutcome:
    """Run one scenario of ``project`` and write ``build/sim/<scenario>/``."""
    scenario = next((s for s in project.spec.simulation.scenarios if s.id == scenario_id), None)
    if scenario is None:
        raise KeyError(f"scenario {scenario_id!r} not in robot.yaml")
    out_dir = out_dir or project.build_dir(f"sim/{scenario_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    wall_start = time.monotonic()
    kernel = CoSimKernel(project, scenario, seed=seed)
    setup = time.monotonic() - wall_start
    diagnostics = list(kernel.diagnostics)
    server = TwinHalServer(kernel)
    cmd = software_cmd if software_cmd is not None else default_software_command(project)
    if cmd is None:
        diagnostics.append(Diagnostic.warning(
            "software_missing", "no runnable robot software; the twin runs with no software outputs",
            subjects=[m.name for m in project.spec.software.modules], data={"modules": len(project.spec.software.modules)}))
    software = _Software(cmd, kernel, server, out_dir / "software.log", project.root)
    kernel.on_brownout = lambda t: None

    duration = kernel.duration
    last_report = 0.0
    hung = False
    while kernel.t < duration - 1e-9 and not server.ended:
        if progress and kernel.t - last_report >= 10.0:
            last_report = kernel.t
            progress(f"{scenario_id}: t={kernel.t:.1f}s / {duration:.0f}s")
        if server.connected and not software.frozen:
            status = server.serve_one()
            if status == "ok":
                continue
            if status == "end":
                break
            if status == "freeze":
                software.freeze()
                kernel.software_frozen, kernel.frozen_at = True, kernel.t
                kernel.recorder.events.append({"t": kernel.t, "event": "software_freeze", "method": "SIGSTOP",
                                               "pid": software.proc.pid if software.proc else None})
                continue
            if status == "brownout":
                software.kill("brownout")
                continue
            if status == "timeout":
                hung = True
                software.kill("hung")
                software.gave_up = True
                continue
            # closed: the program exited.
            exit_code = software.proc.wait() if software.proc else None
            software.kill("exited")
            if software.launches:
                software.launches[-1]["exit_code"] = exit_code
            kernel.recorder.events.append({"t": kernel.t, "event": "software_exit", "exit_code": exit_code})
            if software.restarts < MAX_RESTARTS and kernel.pi_powered:
                software.restarts += 1
                software.restart_at = kernel.t + WATCHDOG_RESTART_DELAY
            else:
                software.gave_up = True
            continue
        # No software connected (or frozen): decide whether to (re)launch, otherwise free-run one step.
        due = (kernel.pi_powered and not software.gave_up and kernel.boot_ready_t is not None and kernel.t + 1e-9 >= kernel.boot_ready_t
               and (software.restart_at is None or kernel.t + 1e-9 >= software.restart_at))
        if due:
            kernel.boot_ready_t = kernel.t  # booted; stays set until the next power loss
            software.restart_at = None
            if not software.launch():
                diagnostics.append(Diagnostic.error(
                    "software_not_connected", f"robot software did not connect to the twin within {CONNECT_TIMEOUT:.0f} s",
                    subjects=[scenario_id], data={"t": kernel.t, "exit_code": software.launches[-1].get("exit_code") or 0}))
                software.gave_up = True
            continue
        kernel.step()
    software.kill("scenario_end")
    server.close()
    wall = time.monotonic() - wall_start

    outcome = _finish(project, scenario, kernel, server, software, diagnostics, out_dir, render, wall, hung, setup)
    return outcome


def _fault(kernel: CoSimKernel) -> dict[str, Any] | None:
    """The first injected fault that took effect: {"type", "t"}."""
    faults = []
    if kernel.frozen_at is not None:
        faults.append({"type": "software_freeze", "t": float(kernel.frozen_at)})
    if kernel.transmitter_off is not None and kernel.transmitter_off[0] <= kernel.t:
        faults.append({"type": "transmitter_off", "t": float(kernel.transmitter_off[0])})
    return min(faults, key=lambda f: f["t"]) if faults else None


def _interp_position(rec: dict[str, np.ndarray], t: float) -> tuple[float, float]:
    return float(np.interp(t, rec["t"], rec["x"])), float(np.interp(t, rec["t"], rec["y"]))


def _finish(project: Project, scenario: Any, kernel: CoSimKernel, server: TwinHalServer, software: _Software,
            diagnostics: list[Diagnostic], out_dir: Path, render: bool, wall: float, hung: bool,
            setup: float = 0.0) -> ScenarioOutcome:
    for arbiter in set(kernel.arbiters.values()):
        kernel.recorder.events.extend(arbiter.events)
    kernel.recorder.events.sort(key=lambda e: e.get("t", 0.0))
    rec = kernel.recorder.arrays()
    config = project.spec.software.config
    failsafe_timeout = float(config.get("radio_failsafe_timeout_s", 0.25))
    first_frame = kernel.receiver.rc_frame_times[0] if kernel.receiver.rc_frame_times else 0.0
    gaps = [g for g in rc_gaps(kernel.receiver.rc_frame_times, kernel.t, failsafe_timeout) if g[0] >= first_frame]
    link_lost = None
    if kernel.receiver.link_lost_events:
        t_loss = kernel.receiver.link_lost_events[0]
        x, y = _interp_position(rec, t_loss)
        tx = kernel.radio.tx_position
        link_lost = {"t": float(t_loss), "x": x, "y": y, "distance": float(math.hypot(x - tx[0], y - tx[1])),
                     "source": "receiver"}
    elif gaps:
        # At the edge of range the receiver can keep catching single packets (LQ of a few %), so its own
        # (1 s) loss timeout never fires; the software still fails safe on the first frame gap longer than its
        # radio failsafe timeout. Score that gap, while moving, as the loss (it can only come earlier).
        moving = [g for g in gaps if abs(float(np.interp(g[0], rec["t"], rec["speed"]))) > 0.2]
        if moving:
            t_loss = moving[0][0]
            x, y = _interp_position(rec, t_loss)
            tx = kernel.radio.tx_position
            link_lost = {"t": float(t_loss), "x": x, "y": y, "distance": float(math.hypot(x - tx[0], y - tx[1])),
                         "source": "software_failsafe_gap"}
    radio = kernel.radio
    tx = radio.tx_position
    h_rx = float(kernel.rx_antenna_offset[2])
    curve = radio_curves(radio, max(2.0, float(np.max(rec["tx_distance"])) * 1.2, float(project.spec.requirements.radio_range or 0.0) * 1.2),
                         h_rx)
    esc_params = kernel.project.catalog.component(project.spec.components[kernel.esc_id].type).behavior.params \
        if kernel.esc_id in project.spec.components else {}
    from r3_model.units import to_si

    context = {
        "scenario": scenario.id, "mission": scenario.mission, "failsafe_gaps": gaps, "failsafe_timeout": failsafe_timeout,
        "brownouts": [round(t, 4) for t in kernel.brownout_times], "link_lost": link_lost, "logs": kernel.recorder.logs,
        "min_wire_clearance": kernel.min_wire_clearance, "events": kernel.recorder.events,
        "esc_neutral_us": to_si(esc_params.get("neutral_pulse", 1500e-6)) * 1e6,
        "obstacles": [{"id": o.get("id"), "center": [to_si(v) for v in o.get("center", [0, 0, 0])]}
                      for o in project.spec.simulation.environment.obstacles],
        "fault": _fault(kernel),
        "tx_position": tx.tolist(), "rssi_curve": curve, "radio_backend": radio.backend, "radio_label": radio.model.label,
        "sensitivity_dbm": radio.sensitivity_dbm, "noise_dbm": radio.noise_dbm,
        "snr_required_db": radio.snr_required_db,
    }
    checks = evaluate(scenario.expect or {}, rec, context)
    for check in checks:
        if check.passed:
            continue
        measured = check.measured
        data = {"measured": float(measured) if isinstance(measured, (int, float, bool)) else None,
                "limit": float(check.expected) if isinstance(check.expected, (int, float, bool)) else None}
        data.update({k: v for k, v in check.data.items() if isinstance(v, (int, float))})
        diagnostics.append(Diagnostic.error(
            CHECK_CODES.get(check.key, "scenario_expectation_failed"),
            f"{scenario.id}: {check.key} expected {check.expected}, measured {check.measured} ({check.detail})",
            subjects=[scenario.id, check.key], data=data))
    if hung:
        diagnostics.append(Diagnostic.error("software_hung", "software stopped answering the twin", subjects=[scenario.id],
                                            data={"t": kernel.t}))
    crashes = [launch for launch in software.launches if launch.get("ended") == "exited" and launch.get("exit_code")]
    if crashes:
        diagnostics.append(Diagnostic.error("software_crashed", f"robot software exited with an error {len(crashes)} time(s); see software.log",
                                            subjects=[scenario.id], data={"crashes": len(crashes), "first_t": crashes[0]["t"]}))
    for uart in kernel.uarts.values():
        if uart.overruns:
            diagnostics.append(Diagnostic.warning("uart_overrun", f"{uart.device}: {uart.overruns} bytes dropped (software not reading)",
                                                  subjects=[uart.binding], data={"bytes_dropped": uart.overruns}))
    for event in kernel.recorder.events:
        if event.get("event") == "uart_baud_mismatch":
            diagnostics.append(Diagnostic.error("uart_baud_mismatch", f"{event['device']}: software opened {event['software_baud']} baud, "
                                                f"device sends {event['device_baud']}", subjects=[event["device"]],
                                                data={"software_baud": event["software_baud"], "device_baud": event["device_baud"]}))

    dt_rec = float(np.median(np.diff(rec["t"]))) if len(rec["t"]) > 1 else 0.01
    energy_wh = float(np.sum(rec["battery_v"] * rec["battery_i"]) * dt_rec / 3600.0)
    distance = float(np.sum(np.hypot(np.diff(rec["x"]), np.diff(rec["y"])))) if len(rec["x"]) > 1 else 0.0
    smooth = np.convolve(rec["speed"], np.ones(10) / 10.0, mode="same") if len(rec["speed"]) >= 10 else rec["speed"]
    metrics = {
        "top_speed_mps": round(float(np.max(smooth)), 3), "distance_m": round(distance, 2),
        "min_battery_voltage_v": round(float(np.min(rec["battery_v"])), 3), "final_soc": round(float(rec["soc"][-1]), 4),
        "energy_wh": round(energy_wh, 3), "peak_battery_current_a": round(float(np.max(rec["battery_i"])), 2),
        "peak_motor_current_a": round(float(np.max(np.abs(rec["motor_current"]))), 2),
        "min_rail_5v": round(float(np.min(rec["rail_5v"])), 3), "brownouts": len(kernel.brownout_times),
        "max_tx_distance_m": round(float(np.max(rec["tx_distance"])), 1),
        "min_rssi_dbm": round(float(np.nanmin(rec["rssi"])), 1) if np.isfinite(rec["rssi"]).any() else None,
        "link_lost": link_lost, "rc_frames": kernel.receiver.frames_rc,
        "fault": context["fault"],
        "arbiter": {f"{a.board}": {"heartbeat_edges": a.edges, "failsafe": a.failsafe, "timeout": a.params.timeout,
                                   "transitions": [[round(tt, 4), s.value] for tt, s in a.model.transitions]}
                    for a in set(kernel.arbiters.values())},
        "packets_ok": kernel.receiver.packets_ok, "packets_lost": kernel.receiver.packets_lost,
        "min_wire_clearance_m": (round(kernel.min_wire_clearance[0], 5) if math.isfinite(kernel.min_wire_clearance[0]) else None),
        "min_wire_clearance_wire": kernel.min_wire_clearance[1], "min_wire_clearance_component": kernel.min_wire_clearance[2],
        "vehicle_mass_kg": round(kernel.vm.total_mass, 4), "sim_time_s": round(kernel.t, 3), "wall_time_s": round(wall, 2),
        "realtime_factor": round(kernel.t / wall, 2) if wall > 0 else None,
        # Setup (model build, cable layout and settling) is a fixed cost; the loop factor is what
        # long scenarios approach.
        "setup_time_s": round(setup, 2),
        "loop_realtime_factor": round(kernel.t / (wall - setup), 2) if wall > setup else None,
        "hal_requests": server.requests,
        "hal_errors": dict(server.errors),
    }
    metrics.update(radio_metrics(radio, rec, project.spec.requirements.radio_range, h_rx))
    metrics.update(_loss_breakdown(project, kernel, rec))
    diagnostics.extend(_sensor_capability(project, kernel, scenario))
    passed = all(c.passed for c in checks) and not any(d.severity.value == "error" and d.code in ("software_not_connected", "software_hung")
                                                       for d in diagnostics)

    artifacts: list[tuple[str, str, str]] = []
    def rel(path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to((project.root / "build").resolve()))
        except ValueError:
            return str(Path(path).resolve())

    mjcf_path = write_mjcf(kernel.vm, out_dir / "vehicle.xml")
    artifacts.append((rel(mjcf_path), "mjcf", "generated MuJoCo vehicle model"))
    npz = out_dir / "recording.npz"
    np.savez_compressed(
        npz, **rec, snapshot_t=np.asarray(kernel.recorder.snapshots_t), snapshot_qpos=np.asarray(kernel.recorder.snapshots_qpos),
        rc_frame_times=np.asarray(kernel.receiver.rc_frame_times),
        firmware_logs=np.asarray([json.dumps(log) for log in kernel.recorder.logs] or [""]),
        events=np.asarray([json.dumps(e) for e in kernel.recorder.events] or [""]))
    artifacts.append((rel(npz), "recording", "100 Hz time series, 30 Hz qpos snapshots, firmware logs, events"))
    from .plots import plot_all
    from .radio_plots import plot_coverage

    for path in plot_all(rec, context, out_dir):
        artifacts.append((rel(path), "plot_png", path.stem.replace("_", " ")))
    coverage = plot_coverage(radio, rec, context, out_dir, project.build_dir("sim") / "radio", h_rx)
    if coverage is not None:
        artifacts.append((rel(coverage), "plot_png", f"radio coverage map ({radio.model.label})"))
    renders: list[str] = []
    if render:
        try:
            from .render import render_frames, render_video

            times = kernel.recorder.snapshots_t
            at = [0.5, kernel.t * 0.25, kernel.t * 0.5, kernel.t * 0.75, kernel.t - 0.05]
            if link_lost:
                at.append(link_lost["t"])
            for path in render_frames(kernel.vehicle.model, times, kernel.recorder.snapshots_qpos, out_dir / "renders", sorted(set(at))):
                artifacts.append((rel(path), "render_png", "MuJoCo offscreen render"))
                renders.append(str(path))
            video = render_video(kernel.vehicle.model, times, kernel.recorder.snapshots_qpos, out_dir / "renders" / "clip")
            if video:
                artifacts.append((rel(video), "video", "chase-camera clip of the first 12 s"))
                renders.append(str(video))
        except Exception as error:  # noqa: BLE001 - rendering needs an OpenGL context
            diagnostics.append(Diagnostic.warning("render_unavailable", f"offscreen rendering failed: {error}", subjects=[scenario.id],
                                                  data={"frames": 0}))
    log_path = out_dir / "software.log"
    if log_path.exists():
        artifacts.append((rel(log_path), "log", "robot software stdout/stderr"))

    summary = {
        "scenario": scenario.id, "description": scenario.description, "passed": passed, "seed": kernel.seed,
        "timestep": kernel.dt, "duration": kernel.duration, "checks": [c.as_dict() for c in checks], "metrics": metrics,
        "software": {"command": software.cmd, "launches": software.launches, "module": server.module,
                     "firmware_log_count": len(kernel.recorder.logs)},
        "fallbacks": {"electrical": bool(getattr(kernel.electrical, "is_fallback", False)),
                      "cables": bool(getattr(kernel.cables, "is_fallback", False))},
        "events": kernel.recorder.events[:200],
        "firmware_logs": kernel.recorder.logs[:200],
        "diagnostics": [d.model_dump(mode="json") for d in diagnostics],
        "assumptions": [{"domain": d, "statement": s} for d, s in kernel.assumptions],
        "artifacts": [{"path": p, "kind": k, "description": d} for p, k, d in artifacts],
        "renders": renders,
        "inputs": project.fingerprint(),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    artifacts.insert(0, (rel(out_dir / "summary.json"), "json", "scenario summary"))
    return ScenarioOutcome(scenario.id, passed, summary, diagnostics, kernel.assumptions, artifacts, out_dir)


def radio_curves(radio: Any, max_distance: float, h_rx: float, points: int = 160) -> dict[str, Any]:
    """Mean RX power along the +x radial from the transmitter (static scene, no shadowing): active model
    (coherent at the carrier and local mean) and the previous analytic two-ray model."""
    radial = np.logspace(0, math.log10(max(2.0, max_distance)), points)
    tx = radio.tx_position
    xyz = np.column_stack([tx[0] + radial, np.full_like(radial, tx[1]), np.full_like(radial, h_rx)])
    coherent, mean = radio.mean_rx_power_dbm(xyz)
    analytic, _ = radio.mean_rx_power_dbm(xyz, model=radio.analytic)
    return {"distance": radial.tolist(), "rssi": coherent.tolist(), "rssi_local_mean": mean.tolist(),
            "rssi_analytic": analytic.tolist(), "backend": radio.backend, "label": radio.model.label}


def radio_metrics(radio: Any, rec: dict[str, np.ndarray], radio_range: float | None, h_rx: float) -> dict[str, Any]:
    """Link metrics at the range requirement, small-scale fading statistics and solver statistics."""
    out: dict[str, Any] = {"radio_backend": radio.backend, "radio_solver": radio.model.stats()}
    tx = radio.tx_position
    required = radio.noise_dbm + radio.snr_required_db
    if radio_range:
        r = float(radio_range)
        near = np.abs(rec["tx_distance"] - r) < 5.0
        if near.any():
            out["rssi_at_range_requirement_dbm"] = round(float(np.nanmean(rec["rssi"][near])), 1)
            out["snr_at_range_requirement_db"] = round(float(np.nanmean(rec["snr"][near])), 1)
        point = np.array([[tx[0] + r, tx[1], h_rx]])
        model_rx = float(radio.mean_rx_power_dbm(point)[0][0])
        analytic_rx = float(radio.mean_rx_power_dbm(point, model=radio.analytic)[0][0])
        out["model_rssi_at_range_requirement_dbm"] = round(model_rx, 1)
        out["two_ray_rssi_at_range_requirement_dbm"] = round(analytic_rx, 1)  # previous analytic model, reference
        out["link_margin_at_range_requirement_db"] = round(model_rx - required, 1)
    # Small-scale fading: coherent RSSI (without shadowing) about its 2 m running mean in power, while moving.
    if "multipath" in rec and len(rec["rssi"]) > 20:
        unshadowed = rec["rssi"] + np.nan_to_num(rec.get("shadowing", np.zeros_like(rec["rssi"])))
        d = rec["tx_distance"]
        moving = np.abs(np.gradient(d)) > 1e-4
        if moving.sum() > 50:
            order = np.argsort(d[moving])
            dd, pp = d[moving][order], 10.0 ** (unshadowed[moving][order] / 10.0)
            cumsum = np.concatenate([[0.0], np.cumsum(pp)])
            lo = np.searchsorted(dd, dd - 1.0)
            hi = np.searchsorted(dd, dd + 1.0, side="right")
            local = (cumsum[hi] - cumsum[lo]) / np.maximum(hi - lo, 1)
            fading = 10.0 * np.log10(pp / local)
            out["small_scale_fading_db"] = {"std": round(float(np.std(fading)), 2), "p01": round(float(np.percentile(fading, 1)), 2),
                                            "p50": round(float(np.median(fading)), 2), "window_m": 2.0}
        out["multipath_vs_incoherent_db_median"] = round(float(np.nanmedian(rec["multipath"])), 2)
    return out


def _loss_breakdown(project: Project, kernel: CoSimKernel, rec: dict[str, np.ndarray]) -> dict[str, Any]:
    """Road-load power split at the top-speed sample: aero 1/2 rho CdA v^3, rolling C_rr m g v,
    and viscous spin-joint damping sum(c w^2) from robot.yaml."""
    if not len(rec["speed"]):
        return {}
    k = int(np.argmax(rec["speed"]))
    v = float(rec["speed"][k])
    vehicle = kernel.vehicle
    aero = 0.5 * vehicle.rho * vehicle.drag_area * v ** 3
    wheels = list(kernel.vm.wheels.values())
    crr = wheels[0].rolling_resistance if wheels else 0.0
    rolling = crr * kernel.vm.total_mass * project.spec.simulation.environment.gravity * v
    damping = sum(j.damping * (v / w.radius) ** 2 for w in wheels for j in project.spec.mechanical.joints if j.id == w.joint)
    return {"loss_breakdown_at_top_speed_w": {"speed_mps": round(v, 3), "aero": round(aero, 2), "rolling": round(rolling, 2),
                                              "spin_joint_damping": round(damping, 2)}}


def _sensor_capability(project: Project, kernel: CoSimKernel, scenario: Any) -> list[Diagnostic]:
    """Static check: can the navigation sensor meet the scenario's cross-track tolerance?
    A 2-D Gaussian GPS error with CEP50 has per-axis sigma = CEP / 1.1774; 95 % of
    lateral errors stay within 1.96 sigma."""
    limit = (scenario.expect or {}).get("max_cross_track_error_m")
    if limit is None or kernel.gps is None:
        return []
    lateral95 = 1.96 * kernel.gps.cep / 1.1774
    if lateral95 <= float(limit):
        return []
    return [Diagnostic.warning(
        "gps_accuracy_insufficient",
        f"{kernel.gps_id}: CEP {kernel.gps.cep} m gives a 95 % lateral error of {lateral95:.2f} m, above the "
        f"{limit} m cross-track tolerance of {scenario.id}; GPS-only navigation cannot meet it reliably",
        subjects=[kernel.gps_id, scenario.id], data={"cep_m": kernel.gps.cep, "lateral_error_95_m": round(lateral95, 3),
                                                     "limit_m": float(limit)},
        hint="Use an RTK/dual-band receiver, fuse odometry against a surveyed start pose, or relax the tolerance.")]
