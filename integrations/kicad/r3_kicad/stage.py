"""Pipeline stage ``pcb``: every board in ``robot.yaml`` ``pcb.boards``.

1. Re-run the headless KiCad flow (``r3_kicad.flow``: schematic, ERC, netlist,
   pcbnew board build, Freerouting, pours, DRC) when the design intent or the
   automation changed since the committed KiCad project was produced; otherwise
   reuse the committed project. ``R3_KICAD_REGENERATE=0`` disables regeneration.
2. ``kicad-cli``: ERC, DRC with schematic parity (zones refilled), netlist,
   Gerbers + drill, pick-and-place, BOM, STEP, schematic PDF, 3D renders. No
   KiCad window is ever opened (checked by :class:`r3_kicad.headless.GuiWatch`).
3. Read back KiCad's netlist and (through pcbnew) the board, and write
   ``build/pcb/<board>/board.json``.
4. Check the connector pin->net maps against the catalog (``board_catalog_mismatch``),
   the netlist against the intent, regulator design margins, and power copper.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from r3_model.diagnostics import Diagnostic, StageResult

from . import ipc2152, pours
from .arbiter import ArbiterParams
from .board_json import catalog_check, connector_geometry, connectors_map, nets_map, power_summary, traces
from .board_read import read_board
from .cli import KicadCli, report_counts, report_items
from .flow import Flow, FlowError, inputs_digest, load_intent_module
from .headless import GuiWatch
from .intent import BoardIntent
from .netlist import compare, read_netlist
from .notes import design_notes
from .regulators import hold_up_time, pi_rail, servo_rail
from .spice import power_subcircuit

STAGE = "pcb"
DT_SIGNAL = 10.0


def run(project) -> StageResult:  # project: r3_model.project.Project
    started = time.time()
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    result.assume("pcb", "The schematic and board are produced headlessly with KiCad 10's own engine: official library "
                         "symbols/footprints, kicad-sch-api for schematic capture (KiCad has no headless schematic API) "
                         "normalized by kicad-cli, the pcbnew Python module for the board, Freerouting CLI for signals, "
                         "kicad-cli for ERC/DRC/exports. R3 only reads KiCad's outputs back for board.json and the checks.")
    result.assume("pcb", "Trace ratings use the IPC-2152 external-conductor fit (Brooks 2015) at 10 C rise for signals and "
                         "20 C for hand-laid power copper, without the plane modifier (the opposite GND pour lowers the rise).")
    result.assume("pcb", "Regulator data are TI datasheet values (TPS568230 SLVSEY5C, TPS56528 SLVSBV3B); capacitor DC-bias "
                         "retention, switching transition time and the 2-layer thermal resistance are estimates.")
    cli = KicadCli.find()
    if cli is None:
        result.add(Diagnostic.error("kicad_cli_unavailable", "KiCad (kicad-cli + pcbnew) is not installed",
                                    hint="install KiCad 10 or set R3_KICAD_APP"))
    else:
        result.metrics["kicad_version"] = cli.version()
    for board in project.spec.pcb.boards:
        if board.tool != "kicad":
            continue
        _run_board(project, board, cli, result)
    result.duration_s = round(time.time() - started, 3)
    result.write(project.build_dir(STAGE) / "result.json")
    return result


def _rel(project, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(project.root))
    except ValueError:
        return str(path)


def _automation(source: Path, result: StageResult, name: str, cli: KicadCli | None) -> None:
    """Regenerate the KiCad project headlessly when the intent/automation changed; otherwise reuse it."""
    digest = inputs_digest(source)
    log_path = source / "flow-log.json"
    record = json.loads(log_path.read_text()) if log_path.exists() else {}
    metrics = result.metrics.setdefault(name, {})
    metrics["inputs_digest"] = digest
    present = (source / f"{name}.kicad_sch").exists() and (source / f"{name}.kicad_pcb").exists()
    if record.get("inputs_digest") == digest and present:
        metrics["kicad_run"] = "reused (inputs unchanged)"
    elif cli is not None and os.environ.get("R3_KICAD_REGENERATE", "1") != "0":
        try:
            record = Flow(source, log=lambda m: None).run()
            metrics["kicad_run"] = "regenerated"
        except FlowError as error:
            metrics["kicad_run"] = "regeneration failed"
            result.add(Diagnostic.error("pcb_flow_failed", f"{name}: headless KiCad flow failed: {error}", subjects=[name]))
            record = json.loads(log_path.read_text()) if log_path.exists() else {}
    else:
        metrics["kicad_run"] = "reused (stale)"
        result.add(Diagnostic.warning("pcb_kicad_stale", f"{name}: design intent or automation changed since KiCad last produced "
                                      "the project; the committed KiCad files were checked as they are",
                                      subjects=[name], data={"recorded": record.get("inputs_digest"), "current": digest},
                                      hint="run `python -m r3_kicad.flow <board dir>` (headless)"))
    metrics["flow_seconds"] = {k: v.get("seconds") for k, v in record.get("steps", {}).items()}
    metrics["flow_total_seconds"] = record.get("total_seconds")
    metrics["flow_headless"] = record.get("headless")
    route = record.get("steps", {}).get("route", {}).get("result") or {}
    metrics["freerouting"] = {k: route.get(k) for k in ("freerouting", "java", "max_passes", "attempts", "attempt_results")}
    metrics["freerouting"]["rounds"] = len(route.get("rounds", []))


def _run_board(project, board, cli: KicadCli | None, result: StageResult) -> None:
    source = project.root / board.source
    name = Path(board.source).name
    out = project.build_dir(f"{STAGE}/{name}")
    try:
        module = load_intent_module(source)
    except FileNotFoundError as error:
        result.add(Diagnostic.error("pcb_intent_missing", str(error), subjects=[board.component]))
        return
    intent: BoardIntent = module.build()
    _automation(source, result, name, cli)
    sch, pcb = source / f"{name}.kicad_sch", source / f"{name}.kicad_pcb"
    if not sch.exists() or not pcb.exists():
        result.add(Diagnostic.error("pcb_kicad_missing", f"{name}: KiCad project files are missing", subjects=[name]))
        return
    if board.layers != 2 or abs(board.copper_oz - intent.copper_oz) > 1e-6:
        result.add(Diagnostic.error("pcb_stackup_mismatch", "robot.yaml stackup differs from the board design", subjects=[name],
                                    data={"spec_layers": board.layers, "board_layers": 2, "spec_oz": board.copper_oz,
                                          "board_oz": intent.copper_oz}))
    files: dict[str, str] = {"kicad_sch": _rel(project, sch), "kicad_pcb": _rel(project, pcb),
                             "kicad_pro": _rel(project, source / f"{name}.kicad_pro")}
    renders: dict[str, str] = {}
    erc: dict[str, Any] = {}
    drc: dict[str, Any] = {}
    netlist = None
    if cli is not None:
        with GuiWatch() as watch:
            erc, drc, netlist, more_files, renders = _kicad_outputs(project, cli, name, sch, pcb, out, result)
        files.update(more_files)
        result.metrics.setdefault(name, {})["exports_headless"] = watch.summary()
        if not watch.clean:
            result.add(Diagnostic.error("kicad_gui_opened", "a KiCad GUI process or window appeared during the exports",
                                        subjects=[name], data={"processes": len(watch.seen), "windows": len(watch.windows)},
                                        hint="; ".join(watch.seen + watch.windows)[:500]))
    if netlist is None:
        return
    for problem in compare(intent.nets(), netlist):
        result.add(Diagnostic.error("pcb_netlist_mismatch", problem, subjects=[name]))
    board_file = read_board(out / "kicad" / pcb.name, out)
    component = project.spec.components.get(board.component)
    try:
        ctype = project.catalog.component(component.type) if component else None
    except KeyError:
        ctype = None
    nets = nets_map(netlist, intent)
    connectors = connectors_map(netlist, intent)
    result.extend(catalog_check(board.component, ctype, connectors, {n: m for n, m in netlist.nets.items()}, intent))
    dt_power = float(intent.rules.get("delta_t_power", 20.0))
    trace_list = traces(board_file, intent.copper_oz, DT_SIGNAL, dt_power)
    power = power_summary(board_file, intent, trace_list, dt_power)
    _power_checks(intent, board_file, power, result, name)
    _segment_checks(intent, result, name)
    pour_report = _pour_checks(intent, board_file, result, name)
    pi, servo = pi_rail(), servo_rail()
    _regulator_checks(pi, servo, result, name)
    _tvs_check(intent, ctype, pi, servo, result, name)
    geometry = connector_geometry(board_file, intent, ctype)
    for cid, entry in geometry.items():
        if entry.get("deviation_xy", 0.0) > 0.002:
            result.add(Diagnostic.warning("pcb_connector_position_mismatch",
                                          f"{cid} pad centroid is {entry['deviation_xy'] * 1000:.1f} mm from the catalog position",
                                          subjects=[board.component, cid],
                                          data={"board": entry["pad_centroid"], "catalog": entry.get("catalog_position"),
                                                "deviation": entry["deviation_xy"], "limit": 0.002},
                                          hint="update the catalog connector position from board.json connector_geometry"))
    spice = out / f"{name}_power.cir"
    tvs_part = next((p.value for p in intent.parts if p.lib_id.startswith("Diode:SMAJ")), "SMAJ10A")
    spice.write_text(power_subcircuit(pi, servo, tvs=tvs_part))
    files["spice"] = _rel(project, spice)
    notes = design_notes(intent, pi, servo, power, erc, drc, pour_report)
    (out / "design-notes.md").write_text(notes)
    (source / "design-notes.md").write_text(notes)
    files["design_notes"] = _rel(project, source / "design-notes.md")
    arbiter = ArbiterParams()
    board_json = {
        "board": name,
        "component": board.component,
        "catalog_type": intent.catalog_type,
        "outline": {"width": round(board_file.size[0] / 1000, 6), "height": round(board_file.size[1] / 1000, 6),
                    "corner_radius": intent.corner_radius / 1000, "thickness": intent.thickness / 1000},
        "stackup": {"layers": 2, "copper_oz": intent.copper_oz,
                    "copper_thickness": round(ipc2152.copper_thickness_mil(intent.copper_oz) * 2.54e-5, 7)},
        "nets": nets,
        "connectors": connectors,
        "connector_geometry": geometry,
        "traces": trace_list,
        "vias": [{"net": v.net, "position": [round(v.x / 1000, 6), round(v.y / 1000, 6)], "drill": v.drill / 1000,
                  "max_current": round(ipc2152.via_max_current(v.drill), 3)} for v in board_file.vias],
        "zones": [{"net": z.net, "layer": z.layer, "name": z.name, "area": round(z.area * 1e-6, 9)} for z in board_file.zones],
        "power": power,
        "pours": pour_report,
        "regulators": {
            "pi_5v": _regulator_json(pi), "servo_6v": _regulator_json(servo),
            "hold_up_5v_s": round(hold_up_time(pi.values["c_out_effective"], pi.values["v_out_nominal"], 4.63, 6.5), 6),
        },
        "mounting": _mounting_json(intent, ctype),
        "arbiter": {"part": arbiter.part, "timeout": arbiter.timeout, "neutral_us": arbiter.neutral_us,
                    "frame": arbiter.frame, "rearm_edges": arbiter.rearm_edges, "rearm_band_us": arbiter.rearm_band_us,
                    "heartbeat_pin": arbiter.heartbeat_pin, "input_pin": arbiter.input_pin, "output": "J_ESC.1",
                    "supply": arbiter.supply},
        "erc": erc,
        "drc": drc,
        "files": files,
        "renders": renders,
        "units": "SI (m, A, ohm); trace coordinates board-local (origin top-left corner, y down); "
                 "connector_geometry in the component frame (origin board centre, y toward the GPIO edge)",
    }
    (out / "board.json").write_text(json.dumps(board_json, indent=2))
    result.artifact(_rel(project, out / "board.json"), "json", "board hand-off for wiring/electrical stages")
    result.metrics[name].update({"tracks": len(board_file.segments), "vias": len(board_file.vias),
                                 "zones": len(board_file.zones), "erc_errors": erc.get("errors"),
                                 "drc_errors": drc.get("errors"), "drc_unconnected": drc.get("unconnected")})


def _mounting_json(intent: BoardIntent, catalog_component) -> dict[str, Any]:
    """Screw attachment and the envelopes the CAD stage must respect (SI, component frame)."""
    m = intent.mounting
    holes = [p for p in intent.parts if p.lib_id.startswith("Mechanical:MountingHole") and p.pcb is not None]
    return {
        "screw": m.screw, "drill": m.drill / 1000, "thread": "M2.5",
        "holes": [{"ref": p.ref, "position": list(intent.to_frame(p.pcb.x, p.pcb.y))} for p in holes],
        "head": {"diameter": m.head_diameter / 1000, "height": m.head_height / 1000, "side": "top"},
        "printed_cap": {"inner_diameter": round(m.cap_inner_diameter / 1000, 6), "print_clearance": m.print_clearance / 1000,
                        "note": "FDM fit clearance per side (estimate); cap bore = head diameter + 2 x clearance"},
        "standoff": {"description": m.standoff, "across_corners": m.standoff_across_corners / 1000,
                     "height": m.standoff_height / 1000, "side": "bottom"},
        "keepouts": {"top_components_radius": round(m.top_part_radius / 1000, 6),
                     "top_copper_radius": round(m.top_copper_radius / 1000, 6),
                     "bottom_components_radius": round(m.bottom_part_radius / 1000, 6),
                     "bottom_copper_radius": round(m.bottom_copper_radius / 1000, 6),
                     "enforced_by": "KiCad rule areas on each hole footprint, checked by kicad-cli DRC"},
        "board_thickness": intent.thickness / 1000,
    }


def _segment_checks(intent: BoardIntent, result: StageResult, name: str) -> None:
    """Hand-routed segments against IPC-2152 for the current each one carries (``PowerTrack.current``)."""
    dt = float(intent.rules.get("delta_t_power", 20.0))
    th = ipc2152.copper_thickness_mil(intent.copper_oz)
    for track in intent.power_tracks:
        if track.current <= 0:
            continue
        rating = ipc2152.max_current(track.width, intent.copper_oz, dt)
        if rating + 1e-9 < track.current:
            # Rise at the actual current from the same fit: dT = 215.3 I^2 W^-1.15 Th^-1 (mil).
            rise = 215.3 * track.current ** 2 / ((track.width / ipc2152.MIL) ** 1.15 * th)
            points = [p for p in track.points if not isinstance(p, str)]
            length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            result.add(Diagnostic.warning(
                "pcb_trace_over_ipc2152", f"{name}: {track.net} {track.width:g} mm {track.layer} segment carries "
                f"{track.current:g} A, IPC-2152 rating {rating:.2f} A at {dt:g} C rise (estimated rise {rise:.0f} C, "
                "no plane modifier)", subjects=[name, track.net],
                data={"width": track.width / 1000, "current": track.current, "rating": round(rating, 3),
                      "delta_t_rated": dt, "delta_t_estimated": round(rise, 1), "length": round(length / 1000, 6)},
                hint="short necks between pours run cooler than the long-trace fit; widen if space allows"))


def _pour_checks(intent: BoardIntent, board_file, result: StageResult, name: str) -> list[dict[str, Any]]:
    """Power pours (zones with a design current) against IPC-2152 at their narrowest cross-section,
    measured on KiCad's filled copper between the zone's terminals (:mod:`r3_kicad.pours`)."""
    dt = float(intent.rules.get("delta_t_power", 20.0))
    out: list[dict[str, Any]] = []
    for zone in intent.zones:
        if zone.current <= 0 or len(zone.terminals) < 2:
            continue
        zone_name = zone.name or f"{zone.net} {zone.layer}"
        fills = [z for z in board_file.zones if z.net == zone.net and z.layer == zone.layer and z.name == zone_name]
        polygons = [poly for z in fills for poly in z.polygons]
        groups: list[list[Any]] = []
        for group in zone.terminals:
            boxes: list[Any] = []
            for terminal in group:
                if isinstance(terminal, str):
                    ref, number = terminal.split(".", 1)
                    boxes += [pad.bbox for pad in board_file.pads if pad.ref == ref and pad.number == number
                              and zone.layer in pad.layers and pad.bbox is not None]
                else:
                    boxes.append(tuple(terminal))
            groups.append(boxes)
        width = pours.bottleneck_width(polygons, groups) if polygons and all(groups) else 0.0
        rating = ipc2152.max_current(width, intent.copper_oz, dt) if width > 0 else 0.0
        entry = {"zone": zone_name, "net": zone.net, "layer": zone.layer, "current": zone.current,
                 "terminals": [[t if isinstance(t, str) else list(t) for t in g] for g in zone.terminals],
                 "bottleneck_width": round(width / 1000, 6), "rating": round(rating, 3), "delta_t_rated": dt,
                 "area": round(sum(z.area for z in fills) * 1e-6, 9)}
        out.append(entry)
        if width <= 0:
            result.add(Diagnostic.error("pcb_pour_disconnected", f"{name}: pour {zone_name} does not connect its terminals",
                                        subjects=[name, zone.net], data={"terminal_groups": len(groups)}))
        elif rating + 1e-9 < zone.current:
            result.add(Diagnostic.warning(
                "pcb_pour_over_ipc2152", f"{name}: {zone_name} narrows to {width:.2f} mm between its terminals; "
                f"carries {zone.current:g} A, IPC-2152 rating {rating:.2f} A at {dt:g} C rise", subjects=[name, zone.net],
                data={"width": width / 1000, "current": zone.current, "rating": round(rating, 3), "delta_t_rated": dt}))
    return out


def _regulator_json(design) -> dict[str, Any]:
    v = design.values
    return {"part": design.ic.part, "v_out": round(v["v_out_nominal"], 4), "i_design": design.req.i_out,
            "i_limit_min": round(v["i_out_limit_min"], 3), "i_limit_max": round(v["i_out_limit_max"], 3),
            "f_sw": design.ic.f_sw, "inductance": design.inductor.inductance, "c_out_effective": v["c_out_effective"],
            "uvlo_rising_max": design.ic.uvlo_rising_max, "uvlo_falling_min": design.ic.uvlo_falling_min,
            "v_in_dropout": round(v["v_in_dropout"], 3), "max_duty": round(design.ic.max_duty, 4),
            "efficiency_nominal": round(v["efficiency"], 4)}


def _kicad_outputs(project, cli: KicadCli, name: str, sch: Path, pcb: Path, out: Path, result: StageResult):
    files: dict[str, str] = {}
    renders: dict[str, str] = {}
    cli.log.clear()
    # Work on a copy so kicad-cli's zone refill never rewrites the committed board.
    work = out / "kicad"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for suffix in (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".kicad_prl"):
        src = pcb.with_suffix(suffix)
        if src.exists():
            shutil.copy2(src, work / src.name)
    wsch, wpcb = work / sch.name, work / pcb.name
    erc: dict[str, Any] = {"errors": None}
    cli.erc(wsch, out / "erc.json")
    if (out / "erc.json").exists():
        erc = {**report_counts(out / "erc.json"), "report": _rel(project, out / "erc.json")}
        files["erc_report"] = erc["report"]
        if erc["errors"]:
            result.add(Diagnostic.error("kicad_erc_errors", f"{name}: KiCad ERC reports {erc['errors']} errors", subjects=[name],
                                        data={"errors": erc["errors"]},
                                    hint="; ".join(i.get("description", "") for i in report_items(out / "erc.json")
                                                   if i.get("severity") == "error")[:500]))
    else:
        result.add(Diagnostic.error("kicad_erc_failed", "kicad-cli sch erc produced no report", subjects=[name]))
    drc: dict[str, Any] = {"errors": None}
    cli.drc(wpcb, out / "drc.json")
    if (out / "drc.json").exists():
        drc = {**report_counts(out / "drc.json"), "report": _rel(project, out / "drc.json")}
        files["drc_report"] = drc["report"]
        if drc["errors"] or drc.get("unconnected"):
            items = [f"{i.get('type')}: {i.get('description')}" for i in report_items(out / "drc.json")
                     if i.get("severity") == "error"][:15]
            result.add(Diagnostic.error("kicad_drc_errors", f"{name}: KiCad DRC reports {drc['errors']} errors, "
                                        f"{drc.get('unconnected')} unconnected", subjects=[name],
                                        data={"errors": drc["errors"], "unconnected": drc.get("unconnected")},
                                        hint="; ".join(items)[:800]))
    else:
        result.add(Diagnostic.error("kicad_drc_failed", "kicad-cli pcb drc produced no report", subjects=[name]))
    netlist = None
    net_xml = out / f"{name}.xml"
    if cli.netlist(wsch, net_xml).ok:
        netlist = read_netlist(net_xml)
        cli.netlist(wsch, out / f"{name}.net", fmt="kicadsexpr")
        files["netlist"] = _rel(project, out / f"{name}.net")
    gerbers = out / "gerbers"
    if gerbers.exists():
        shutil.rmtree(gerbers)
    if cli.gerbers(wpcb, gerbers).ok and cli.drill(wpcb, gerbers).ok:
        archive = out / f"{name}-fab.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(gerbers.iterdir()):
                zf.write(path, path.name)
        files["gerbers"] = _rel(project, archive)
        files["gerber_dir"] = _rel(project, gerbers)
        result.artifact(files["gerbers"], "gerber_zip", "Gerbers + Excellon drill + drill map")
    else:
        result.add(Diagnostic.error("kicad_export_failed", "Gerber/drill export failed", subjects=[name]))
    for kind, path, run in (("pos", out / f"{name}-pos.csv", lambda p: cli.pos(wpcb, p)),
                            ("bom", out / f"{name}-bom.csv", lambda p: cli.bom(wsch, p)),
                            ("schematic_pdf", out / f"{name}-schematic.pdf", lambda p: cli.sch_pdf(wsch, p)),
                            ("step", out / f"{name}.step", lambda p: cli.step(wpcb, p))):
        if run(path).ok and path.exists():
            files[kind] = _rel(project, path)
            result.artifact(files[kind], kind, f"KiCad {kind} export")
        else:
            result.add(Diagnostic.warning("kicad_export_failed", f"{kind} export failed", subjects=[name]))
    if "step" in files:
        shutil.copy2(out / f"{name}.step", out / "board.step")
        files["board_step"] = _rel(project, out / "board.step")
    for side in ("top", "bottom"):
        target = out / f"render_{side}.png"
        if cli.render(wpcb, target, side).ok and target.exists():
            renders[f"render_{side}"] = _rel(project, target)
            result.artifact(renders[f"render_{side}"], "render_png", f"KiCad 3D render ({side})")
        else:
            result.add(Diagnostic.warning("kicad_render_failed", f"{side} render failed", subjects=[name]))
    for side, layers, mirror in (("top", "F.Cu,F.Mask,F.Silkscreen,Edge.Cuts", False),
                                 ("bottom", "B.Cu,B.Mask,B.Silkscreen,Edge.Cuts", True)):
        target = out / f"copper_{side}.svg"
        if cli.pcb_svg(wpcb, target, layers, mirror).ok:
            renders[f"copper_{side}"] = _rel(project, target)
    (out / "kicad-cli.log").write_text("\n\n".join(f"$ {' '.join(r.command)}\n[{r.returncode}]\n{r.stdout}\n{r.stderr}"
                                                   for r in cli.log))
    return erc, drc, netlist, files, renders


def _power_checks(intent: BoardIntent, board, power: dict[str, Any], result: StageResult, name: str) -> None:
    for net, entry in power.items():
        if not entry["pour_area_mm2"] and "narrowest_deliberate_rating" not in entry and net != "GND":
            result.add(Diagnostic.warning("pcb_power_copper_missing", f"{net} has no pour or deliberate track",
                                          subjects=[name, net], data=entry))


def _regulator_checks(pi, servo, result: StageResult, name: str) -> None:
    for ref, design in (("U2", pi), ("U3", servo)):
        for check, ok, detail in design.checks:
            if not ok:
                result.add(Diagnostic.warning("buck_design_margin", f"{ref} {design.ic.part}: {check}: {detail}",
                                              subjects=[name, ref], hint=check))
    v = servo.values
    if v["v_in_dropout"] > 7.4:
        result.add(Diagnostic.info("servo_rail_dropout", f"servo rail is in dropout below {v['v_in_dropout']:.2f} V input at "
                                   f"{servo.req.i_out:g} A (TPS56528 Dmax {servo.ic.max_duty:.2f}); a nominal 2S pack gives "
                                   "~5.8-5.9 V at the servo", subjects=[name, "U3"],
                                   data={"v_in_dropout": round(v["v_in_dropout"], 3), "v_nominal_in": 7.4,
                                         "max_duty": round(servo.ic.max_duty, 4)}))


def _tvs_check(intent: BoardIntent, catalog_component, pi, servo, result: StageResult, name: str) -> None:
    """The input TVS on the board against the catalog's TVS data and the regulators' VIN absolute maximum.
    V_C(I_PP) is the datasheet clamp at the part's rated peak pulse current, the worst case it is specified for
    (the electrical twin also checks it at the expected surge current)."""
    tvs_parts = [p for p in intent.parts if p.lib_id.startswith("Diode:SMAJ")]
    params = (catalog_component.behavior.params if catalog_component and catalog_component.behavior else {}) or {}
    tvs = params.get("tvs") if isinstance(params.get("tvs"), dict) else None
    if not tvs_parts or tvs is None:
        return
    part = tvs_parts[0]
    if str(tvs.get("part")) != part.value:
        result.add(Diagnostic.error("board_catalog_mismatch", f"{part.ref} is {part.value}, catalog tvs.part is {tvs.get('part')}",
                                    subjects=[name, part.ref], data={"kind": "tvs_part"}))
        return
    clamp, i_pp = float(tvs["v_clamp"]), float(tvs["i_pp"])
    abs_max = min(pi.ic.v_in_abs_max, servo.ic.v_in_abs_max)
    data = {"clamp_voltage": clamp, "abs_max": abs_max, "surge_current": i_pp}
    if clamp > abs_max:
        result.add(Diagnostic.warning("pcb_tvs_clamp_exceeds_abs_max", f"{part.value} clamps at {clamp:g} V at its {i_pp:g} A "
                                      f"peak pulse current, above the regulators' {abs_max:g} V VIN absolute maximum",
                                      subjects=[name, part.ref, "U2", "U3"], data=data,
                                      hint="choose a TVS whose clamp at I_PP is below the absolute maximum"))
    else:
        result.add(Diagnostic.info("pcb_tvs_clamp_ok", f"{part.value} clamps at {clamp:g} V at its {i_pp:g} A peak pulse current, "
                                   f"{abs_max - clamp:.1f} V below the regulators' {abs_max:g} V VIN absolute maximum",
                                   subjects=[name, part.ref], data=data))
