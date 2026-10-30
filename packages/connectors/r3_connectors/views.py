"""Reviewer close-ups of connectors and pins (headless MuJoCo renders)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .frames import Frame
from .render import RenderScene, render

if TYPE_CHECKING:  # pragma: no cover
    from .stage import ConnectorStage

MASKED_COPPER = {"_copper": "#2f7a3e", "_via": "#2f7a3e"}
BINDING_COLORS = {"pwm": "#ff7a00", "uart": "#00a6d6", "i2c": "#b03cd6", "gpio": "#2bb24c", "spi": "#d6b300"}


def _frame(entry: dict[str, Any]) -> Frame:
    f = entry["frame"]
    return Frame.from_axes(f["origin"], f["x"], f["z"])


def _box_around(center: np.ndarray, half: float) -> tuple[np.ndarray, np.ndarray]:
    return center - half, center + half


def _add_component(stage: "ConnectorStage", scene: RenderScene, cid: str, clip: tuple | None, alpha: float = 1.0) -> bool:
    comp = stage.data["components"].get(cid)
    model = stage.component_scenes.get(cid) if comp else None
    if model is None:
        return False
    # STEP exports carry copper but no solder mask: show copper under green mask, as on the real board.
    scene.add_scene(model, _frame(comp), alpha=alpha, clip=clip, recolor=MASKED_COPPER if comp["kind"] == "pcb" else None)
    return True


def _add_connector(stage: "ConnectorStage", scene: RenderScene, ref: str, alpha: float = 1.0) -> None:
    entry = stage.data["connectors"][ref]
    model = stage.component_scenes.get(ref)
    if model is not None:
        pose = stage.pulled.poses[ref]
        scene.add_scene(model, Frame(pose[:3, :3], pose[:3, 3]), alpha=alpha)


def _add_wires(stage: "ConnectorStage", scene: RenderScene, wire_ids: list[str], near: np.ndarray, radius: float,
               labels: bool = True) -> None:
    routes = stage.routes or {"wires": {}}
    for harness in stage.spec.electrical.harnesses:
        for wire in harness.wires:
            if wire.id not in wire_ids or wire.id not in routes["wires"]:
                continue
            path = stage.display.get(wire.id)
            path = np.asarray(routes["wires"][wire.id]["path"], dtype=float) if path is None else path
            keep = np.linalg.norm(path - near, axis=1) < radius
            if keep.sum() < 2:
                continue
            idx = np.nonzero(keep)[0]
            segment = path[idx[0]: idx[-1] + 1]
            od = float(stage.catalog.wire(wire.awg).outer_diameter)
            scene.add_wire(segment, od / 2, wire.color)


def _harness_wires(stage: "ConnectorStage", ref: str) -> list[str]:
    return [p["wire"] for p in stage.data["connectors"].get(ref, {}).get("pins", {}).values() if p.get("wire")]


def pi_header(stage: "ConnectorStage", out: Path) -> Path | None:
    pi = next((cid for cid, c in stage.data["components"].items() if c["kind"] == "computer"), None)
    ref = f"{pi}.J8" if pi else None
    if ref not in stage.data["connectors"]:
        return None
    entry = stage.data["connectors"][ref]
    frame = _frame(entry)
    scene = RenderScene()
    center = frame.origin
    if not _add_component(stage, scene, pi, _box_around(center, 0.03)):
        _add_connector(stage, scene, ref)
    for number, pin in entry["pins"].items():
        tip = np.asarray(pin["tip"])
        if pin["bindings"]:
            interface = pin["bindings"][0].split("(")[-1].rstrip(")")
            scene.add_marker(tip + frame.z * 0.0004, 0.00055, BINDING_COLORS.get(interface, "#ff0000"))
            scene.label(tip + frame.z * 0.0006, f"{number} {pin['signal']} - {', '.join(b.split(' ')[0] for b in pin['bindings'])}")
        elif number in ("1", "2"):
            scene.add_marker(tip + frame.z * 0.0004, 0.00045, "#ffffff")
            scene.label(tip + frame.z * 0.0006, f"pin {number} {pin['signal'] or ''}".strip())
    used = sum(1 for p in entry["pins"].values() if p["bindings"])
    return render(scene, out / "pi_header_closeup.png", center + frame.z * 0.004, 0.085, 120.0, -42.0,
                  title="Raspberry Pi 5 J8: 40 posts at 2.54 mm on the official Raspberry Pi STEP",
                  subtitle=f"{used} pins used by software bindings (coloured by bus); pin 1 inner row toward the SD card; "
                           f"HAT removed for the view")


def pi_hat_stack(stage: "ConnectorStage", out: Path) -> Path | None:
    pair = next((p for p in stage.data["mated_pairs"] if p["kind"] == "direct"), None)
    if pair is None:
        return None
    scene = RenderScene()
    a, b = stage.data["connectors"][pair["a"]], stage.data["connectors"][pair["b"]]
    center = _frame(a).origin
    _add_component(stage, scene, a["component"], _box_around(center, 0.06))
    _add_component(stage, scene, b["component"], _box_around(center, 0.06))
    for ref in (pair["a"], pair["b"]):
        if not stage.data["connectors"][ref]["embedded"]:
            _add_connector(stage, scene, ref)
    for harness_ref, conn in stage.data["connectors"].items():
        if conn["owner"] == "harness" and str(conn.get("mates", "")).startswith(b["component"] + "."):
            _add_connector(stage, scene, harness_ref)
            scene.label(_frame(conn).origin, f"{conn['mates'].split('.')[1]} <- {harness_ref}")
            _add_wires(stage, scene, _harness_wires(stage, harness_ref), _frame(conn).origin, 0.03)
    return render(scene, out / "pi_hat_connectors.png", center + np.array([0.0, -0.025, 0.012]), 0.2, 135.0, -35.0,
                  title=f"{a['component']} + {b['component']}: direct connector stack",
                  subtitle=f"{pair['a']}/{pair['b']}: lateral {pair['lateral_offset'] * 1e3:.3f} mm, faces {pair['axial_gap'] * 1e3:.2f} mm "
                           f"apart, posts overlap socket contacts {pair['contact_overlap'] * 1e3:.2f} mm")


def mated_closeup(stage: "ConnectorStage", out: Path, filename: str, half: str, title: str, distance: float,
                  azimuth: float, elevation: float, board: str | None = None) -> Path | None:
    entry = stage.data["connectors"].get(half)
    if entry is None:
        return None
    frame = _frame(entry)
    center = frame.origin
    scene = RenderScene()
    if board:
        _add_component(stage, scene, board, _box_around(center, 0.02))
    pair = next((p for p in stage.data["mated_pairs"] if half in (p["a"], p["b"])), None)
    partner = entry.get("mates") or (None if pair is None else (pair["b"] if pair["a"] == half else pair["a"]))
    for ref in (half, partner):
        if ref in stage.data["connectors"]:
            other = stage.data["connectors"][ref]
            if other["owner"] == "harness" or not other.get("embedded") or not board:
                _add_connector(stage, scene, ref)
    wires = _harness_wires(stage, half) + (_harness_wires(stage, partner) if partner in stage.data["connectors"] and
                                           stage.data["connectors"][partner]["owner"] == "harness" else [])
    _add_wires(stage, scene, wires, center, 0.035)
    for ref in (half, partner):
        other = stage.data["connectors"].get(ref)
        if other is None or other["owner"] != "harness":
            continue
        for number, pin in other["pins"].items():
            point = pin.get("wire_entry") or pin.get("contact")
            if point is not None and pin.get("wire"):
                scene.label(np.asarray(point), f"{other['name']}.{number}: {pin['wire']} {pin.get('signal', '')}")
    subtitle = ""
    if pair is not None:
        subtitle = (f"{pair['types'][0]} + {pair['types'][1]}: worst pin offset {pair['max_pin_lateral_error'] * 1e3:.3f} mm, "
                    f"wires end on their pins (max {stage.result.metrics.get('max_wire_pin_error_m', 0) * 1e3:.4f} mm)")
    return render(scene, out / filename, center - frame.z * 0.004, distance, azimuth, elevation, title=title,
                  subtitle=subtitle)


def render_closeups(stage: "ConnectorStage") -> list[Path]:
    out = stage.out
    paths = [pi_header(stage, out), pi_hat_stack(stage, out)]
    floating = next((p for p in stage.data["mated_pairs"] if p["kind"] == "floating" and "xt60" in p["types"][0]), None)
    if floating:
        paths.append(mated_closeup(stage, out, "xt60_pair_closeup.png", floating["a"],
                                   f"XT60 pair {floating['a']} / {floating['b']} (KiCad Amass XT60 models)", 0.075, 120, -25))
    for harness_ref, conn in stage.data["connectors"].items():
        if conn["owner"] != "harness" or not conn.get("mates"):
            continue
        board = conn["mates"].split(".")[0]
        if conn["type"] == "jst-gh-4-housing":
            paths.append(mated_closeup(stage, out, f"jst_gh_{harness_ref.replace('.', '_')}_closeup.png", harness_ref,
                                       f"JST GH {conn['mates']} (KiCad SM04B-GHS-TB) with {harness_ref} (GHR-04V-S proxy)",
                                       0.05, 145, -32, board=board))
        if conn["type"] == "servo-3-female":
            paths.append(mated_closeup(stage, out, f"servo_{harness_ref.replace('.', '_')}_closeup.png", harness_ref,
                                       f"Servo plug {harness_ref} on {conn['mates']} (KiCad 1x3 header)", 0.06, 150, -30,
                                       board=board))
    return [p for p in paths if p is not None]


__all__ = ["render_closeups"]
