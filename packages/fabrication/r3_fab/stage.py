"""``fabrication`` stage: 3D printing as the manufacturing process of printed parts.

For every fabricated component whose material is an FDM filament: print geometry,
suggested orientation, printability checks (bed, overhang, walls, threads,
inserts, nut traps, holes), strength of printed frame load paths (scikit-fem),
and print-ready STL/3MF files plus ``build/fabrication/plan.json``.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from . import checks, export, orientation, plots, strength
from .geometry import build_geometry

STAGE = "fabrication"
GENERIC_PRINTED = ("print", "fdm", "filament")  # catalog material strings that take spec.fabrication.material


@dataclass
class PrintedPart:
    cid: str
    ctype: Any
    material: Any  # r3_catalog.schema.Material
    print_fit: Any  # r3_catalog.schema.PrintFit | None
    material_source: str
    options: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------- selection


def resolve_material(catalog: Any, text: str | None) -> Any | None:
    """Material by id or by its keywords as whole words of a ``mechanical.material`` string
    (so "plastic" is not PLA and "Aluminum plate" is not PLA); longest keyword wins."""
    if not text:
        return None
    if text in catalog.materials and catalog.materials[text].process == "fdm":
        return catalog.materials[text]
    lowered = text.lower()
    if lowered in catalog.materials and catalog.materials[lowered].process == "fdm":
        return catalog.materials[lowered]
    best: tuple[int, Any] | None = None
    for material in catalog.materials.values():
        if material.process != "fdm":
            continue
        for word in material.keywords:
            if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", lowered) and (best is None or len(word) > best[0]):
                best = (len(word), material)
    return best[1] if best else None


def select_parts(project: Project, result: StageResult) -> dict[str, PrintedPart]:
    """Fabricated components printed in an FDM material; the others are reported as not printed."""
    from r3_model.pulled import load_pulled
    pulled = load_pulled(project.root)
    fab = project.spec.fabrication
    catalog = project.catalog
    printed: dict[str, PrintedPart] = {}
    not_printed: dict[str, str] = {}
    unknown: list[str] = []
    for cid, comp in project.spec.components.items():
        if comp.type not in catalog.components:
            unknown.append(cid)
            continue
        ctype = catalog.component(comp.type)
        options = dict(fab.parts.get(cid, {}))
        if cid not in pulled.parts or pulled.parts[cid].purchased:
            continue
        if options.get("print") is False:
            not_printed[cid] = f"{ctype.mechanical.material or 'unknown material'} (print: false)"
            continue
        text = pulled.parts[cid].material or ""
        material, source = None, ""
        if options.get("material"):
            material, source = resolve_material(catalog, str(options["material"])), "fabrication.parts override"
        if material is None:
            material, source = resolve_material(catalog, text), "pulled material"
        if material is None and fab.material and (not text or any(word in text.lower() for word in GENERIC_PRINTED)):
            material, source = resolve_material(catalog, fab.material), "fabrication.material default"
        if material is None or material.process != "fdm":
            fit = catalog.print_fit(text) if text else None
            not_printed[cid] = f"{text or 'no material'}" + (f" ({fit.process.upper()})" if fit and fit.process != "fdm" else "")
            continue
        printed[cid] = PrintedPart(cid=cid, ctype=ctype, material=material, print_fit=catalog.print_fit(material.keywords[0] if material.keywords else material.id)
                                   or catalog.print_fits.get("fdm"), material_source=source, options=options)
    if unknown:
        result.add(Diagnostic.warning("component_type_unknown", f"{len(unknown)} component(s) have types not in the catalog; skipped",
                                      subjects=unknown, data={"components": unknown}))
    if not_printed:
        result.add(Diagnostic.info("not_printed", f"{len(not_printed)} fabricated part(s) are not FDM printed (other process or material)",
                                   subjects=sorted(not_printed), data={"parts": not_printed}))
    return printed


def load_cases(project: Project) -> tuple[list[dict[str, Any]], bool]:
    cases = [case.model_dump() for case in project.spec.fabrication.load_cases]
    return (cases, False) if cases else ([dict(c) for c in strength.DEFAULT_LOAD_CASES], True)


def _cad_parts(project: Project) -> dict[str, Any]:
    from r3_model.pulled_view import cad_view
    data = cad_view(project, mass_required=True)
    return data.get("parts", {}) if isinstance(data, dict) else {}


def _orientation_override(options: dict[str, Any]) -> list[float] | None:
    value = options.get("orientation")
    if value is None or value == "auto":
        return None
    return [float(v) for v in value]


def _fraction(value: Any, default: float) -> float:
    if value is None:
        return default
    text = str(value).strip()
    return float(text[:-1]) / 100.0 if text.endswith("%") else float(text)


# ----------------------------------------------------------------------------- per part


def _material_printer(part: PrintedPart, printer: Any, result: StageResult) -> None:
    material = part.material
    problems = []
    if material.enclosure and not printer.enclosed:
        problems.append(f"{material.name} needs an enclosure; {printer.name} is open")
    if material.bed_temp_c and printer.max_bed_temp_c is not None and printer.max_bed_temp_c < material.bed_temp_c[0]:
        problems.append(f"bed {printer.max_bed_temp_c:g} deg C < {material.bed_temp_c[0]:g} deg C required")
    if material.nozzle_temp_c and printer.max_nozzle_temp_c is not None and printer.max_nozzle_temp_c < material.nozzle_temp_c[0]:
        problems.append(f"nozzle {printer.max_nozzle_temp_c:g} deg C < {material.nozzle_temp_c[0]:g} deg C required")
    if problems:
        result.add(Diagnostic.warning("print_material_printer_mismatch", f"{part.cid}: " + "; ".join(problems),
                                      subjects=[part.cid, printer.id], data={"material": material.id, "printer": printer.id},
                                      hint="Choose an enclosed printer (fabrication.printer) or another material."))


def _strength_diagnostics(part: PrintedPart, analysis: strength.StrengthResult, chosen: orientation.Orientation, min_sf: float,
                          result: StageResult) -> dict[str, Any]:
    cid, material = part.cid, part.material
    if not analysis.applicable:
        result.add(Diagnostic.info("print_strength_not_applicable", f"{cid}: strength not evaluated: {analysis.reason}",
                                   subjects=[cid], data={"supports": len(analysis.supports), "loads": len(analysis.loads)}))
        return {"applicable": False, "reason": analysis.reason}
    build = np.asarray(chosen.build_direction)
    cases = []
    worst_sf, worst_case, worst_across = math.inf, None, False
    for case in analysis.cases:
        sf, across = strength.safety_factor(case, material.tensile_strength, material.z_strength, build)
        cases.append({"case": case.case, "accel_g": case.accel_g, "max_von_mises_mpa": round(case.max_von_mises_mpa, 4),
                      "location_mm": np.round(case.location_mm, 3).tolist(), "principal_direction": np.round(case.principal_direction, 4).tolist(),
                      "max_principal_mpa": round(case.max_principal_mpa, 4), "max_deflection_mm": round(case.max_deflection_mm, 5),
                      "carried_load_n": round(case.load_n, 3), "safety_factor": round(sf, 3), "critical_across_layers": across})
        if sf < worst_sf:
            worst_sf, worst_case, worst_across = sf, case, across
    assert worst_case is not None
    allowable = material.z_strength if worst_across else material.tensile_strength
    data = {"safety_factor": worst_sf, "required": min_sf, "case": worst_case.case, "max_von_mises": worst_case.max_von_mises_mpa * 1e6,
            "strength": allowable, "strength_direction": "z" if worst_across else "xy", "max_deflection": worst_case.max_deflection_mm * 1e-3,
            "location": (np.asarray(worst_case.location_mm) * 1e-3).tolist(), "voxel_pitch": [h * 1e-3 for h in analysis.pitch_mm],
            "elements": analysis.elements, "supports": [r.join for r in analysis.supports],
            "loads": {r.join: r.mass_kg for r in analysis.loads}, "frame": "component"}
    text = (f"{cid}: {worst_case.case} max von Mises {worst_case.max_von_mises_mpa:.2f} MPa vs {allowable / 1e6:.0f} MPa "
            f"({'Z, across layers' if worst_across else 'XY'}) -> safety factor {worst_sf:.2f}; deflection {worst_case.max_deflection_mm:.3f} mm")
    hint = ("Thicken the load path, add ribs, rotate the print so the main stress runs along the layers, or use a stronger material "
            "(materials.yaml).")
    if abs(analysis.volume_error) > 0.15:
        result.add(Diagnostic.warning(
            "print_strength_resolution_estimated",
            f"{cid}: the voxel model ({' x '.join(f'{h:.2f}' for h in analysis.pitch_mm)} mm) has {analysis.volume_error:+.0%} of the "
            "part volume; thin walls are not resolved and the strength result is an estimate", subjects=[cid],
            data={"volume_error": analysis.volume_error, "voxel_pitch": data["voxel_pitch"]},
            hint="Thicken thin walls (they are also below fabrication.min_wall) or accept the estimate."))
    if worst_sf < 1.0:
        result.add(Diagnostic.error("print_strength_insufficient", text, subjects=[cid], data=data, hint=hint))
    elif worst_sf < min_sf:
        result.add(Diagnostic.warning("print_strength_margin_low", text + f" (< {min_sf:g} required)", subjects=[cid], data=data, hint=hint))
    else:
        result.add(Diagnostic.info("print_strength_ok", text, subjects=[cid], data=data))
    return {"applicable": True, "safety_factor": round(worst_sf, 3), "required": min_sf, "worst_case": worst_case.case,
            "strength_direction": data["strength_direction"], "voxel_pitch_mm": [round(h, 4) for h in analysis.pitch_mm],
            "elements": analysis.elements, "dropped_elements": analysis.dropped_elements, "runtime_s": round(analysis.runtime_s, 2),
            "voxel_volume_error": round(analysis.volume_error, 4),
            "supports": [{"join": r.join, "neighbour": r.neighbour, "interface": r.interface} for r in analysis.supports],
            "loads": [{"join": r.join, "neighbour": r.neighbour, "interface": r.interface, "carried_mass_kg": round(r.mass_kg, 5)}
                      for r in analysis.loads], "cases": cases}


def process_part(project: Project, part: PrintedPart, printer: Any, cases: list[dict[str, Any]], cad_parts: dict[str, Any],
                 out_dir: Path, result: StageResult) -> dict[str, Any]:
    """Geometry, strength, orientation, printability checks and files of one printed part."""
    cid, fab = part.cid, project.spec.fabrication
    entry: dict[str, Any] = {"component": cid, "type": part.ctype.id, "material": part.material.id, "material_name": part.material.name,
                             "material_source": part.material_source, "printer": printer.id if printer else fab.printer}
    try:
        geometry = build_geometry(project, cid)
    except Exception as error:  # noqa: BLE001 - one bad part must not stop the others
        result.add(Diagnostic.warning("print_geometry_failed", f"{cid}: print geometry could not be built: {error}", subjects=[cid]))
        entry["error"] = str(error)
        return entry
    mesh = geometry.mesh
    entry["geometry_source"] = geometry.source
    if geometry.source != "cad":
        result.add(Diagnostic.warning(
            "print_geometry_estimated", f"{cid}: print geometry from the catalog {geometry.source} (no usable CAD mesh)",
            subjects=[cid], data={"source": geometry.source, "notes": geometry.notes, "holes_cut": len(geometry.holes)},
            hint="Run the cad stage so the printed file is the Onshape part (build/cad/meshes)."))
    for note in geometry.notes:
        result.assume("fabrication", f"{cid}: {note}")
    nozzle_mm = (printer.nozzle if printer else 0.0004) * 1e3
    walls = checks.wall_thickness(mesh)
    result.extend(checks.check_walls(cid, walls, nozzle_mm, float(fab.min_wall) * 1e3))
    try:
        analysis = strength.analyze(project, cid, mesh, part.material, walls.typical_mm if math.isfinite(walls.typical_mm) else 2.0,
                                    cases, cad_parts)
    except Exception as error:  # noqa: BLE001
        analysis = strength.StrengthResult(applicable=False, reason=f"solver failed: {error}")
    for note in analysis.notes:
        result.assume("fabrication", f"{cid}: {note}")
    stress_direction, strength_ok = None, None
    if analysis.applicable and analysis.worst is not None:
        worst = analysis.worst
        stress_direction = np.asarray(worst.principal_direction)
        strength_ok = lambda build: all(  # noqa: E731
            strength.safety_factor(c, part.material.tensile_strength, part.material.z_strength, build)[0] >= fab.min_safety_factor
            for c in analysis.cases)
    chosen, candidates = orientation.choose(mesh, fab.max_overhang_deg, _orientation_override(part.options), stress_direction, strength_ok)
    oriented = orientation.oriented(mesh, chosen.rotation)
    entry["orientation"] = {**chosen.to_json(), "override": _orientation_override(part.options) is not None,
                            "candidates": [c.to_json() for c in candidates[:8]]}
    bed = [v * 1e3 for v in printer.build_volume] if printer else None
    z_turn = 0.0
    if printer:
        fits, z_turn, extents = orientation.fit_on_bed(oriented, bed)
        entry["bed_fit"] = {"fits": fits, "z_rotation_deg": z_turn, "extents_mm": [round(v, 3) for v in extents], "build_volume_mm": bed}
        if not fits:
            result.add(Diagnostic.error(
                "print_bed_exceeded", f"{cid}: {extents[0]:.1f} x {extents[1]:.1f} x {extents[2]:.1f} mm does not fit the "
                f"{printer.name} build volume {bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm in the chosen orientation",
                subjects=[cid, printer.id], data={"extents": [v * 1e-3 for v in extents], "build_volume": list(printer.build_volume),
                                                  "orientation_deg": chosen.euler_deg},
                hint="Split the part with a bolted or dovetail joint, choose a larger printer, or another orientation."))
    if chosen.support_area_mm2 > 1e-6:
        result.add(Diagnostic.warning(
            "print_overhang", f"{cid}: {chosen.support_area_mm2:.1f} mm^2 needs support in the chosen orientation (steepest "
            f"{chosen.max_overhang_deg:.0f} deg from vertical > {fab.max_overhang_deg:g} deg)", subjects=[cid],
            data={"support_area": chosen.support_area_mm2 * 1e-6, "max_angle_deg": chosen.max_overhang_deg, "limit_deg": fab.max_overhang_deg,
                  "orientation_deg": chosen.euler_deg},
            hint="Add 45-degree chamfers under overhangs, split the part, or accept slicer supports (removal marks)."))
    if printer:
        _material_printer(part, printer, result)
    entry["walls"] = {"min_mm": round(walls.min_mm, 4) if math.isfinite(walls.min_mm) else None,
                      "typical_mm": round(walls.typical_mm, 4) if math.isfinite(walls.typical_mm) else None,
                      "min_location_mm": np.round(walls.location_mm, 3).tolist() if walls.location_mm else None}
    entry["strength"] = _strength_diagnostics(part, analysis, chosen, fab.min_safety_factor, result)
    part_dir = out_dir / cid
    if analysis.applicable:
        sf = entry["strength"]["safety_factor"]
        allowable = part.material.z_strength if entry["strength"]["strength_direction"] == "z" else part.material.tensile_strength
        plot = part_dir / f"{cid}_stress.png"
        plots.stress_plot(plot, cid, analysis, part.material, sf, allowable / 1e6)
        result.artifact(plot.relative_to(project.build_dir()), "plot_png", f"{cid} von Mises stress, worst load case")
        result.assume("fabrication", f"{cid}: voxel FE pitch {' x '.join(f'{h:.2f}' for h in analysis.pitch_mm)} mm, "
                                     f"{analysis.elements} 20-node hexes ({analysis.runtime_s:.1f} s)")
    placed = export.bed_placed(oriented, z_turn, bed)
    files = export.write_files(placed, part_dir, cid)
    for kind, path in files.items():
        result.artifact(Path(path).relative_to(project.build_dir()), kind, f"{cid} print file ({part.material.name}, mm, oriented)")
    infill = _fraction(part.options.get("infill"), 1.0)
    perimeters = int(part.options.get("perimeters", 3))
    estimate = export.print_estimate(float(mesh.volume), float(mesh.area), float(part.material.density), infill, perimeters)
    entry.update({"files": {k: str(Path(v).relative_to(project.root)) for k, v in files.items()}, "infill": infill, "perimeters": perimeters,
                  "volume_mm3": round(float(mesh.volume), 3), "print_mass_kg": round(estimate["mass_kg"], 6),
                  "solid_mass_kg": round(estimate["solid_mass_kg"], 6), "filament_length_m": round(estimate["filament_length_m"], 4),
                  "support_area_mm2": round(chosen.support_area_mm2, 3), "height_mm": round(chosen.height_mm, 3),
                  "footprint_mm": [round(v, 3) for v in chosen.footprint_mm], "catalog_mass_kg": part.ctype.mechanical.mass})
    if infill < 1.0 and analysis.applicable:
        result.add(Diagnostic.warning("print_infill_strength_estimated",
                                      f"{cid}: {infill:.0%} infill; the strength check assumes solid material (100 % infill TDS values)",
                                      subjects=[cid], data={"infill": infill}, hint="Print load-path parts solid or with more perimeters."))
    entry["_placed"] = placed
    return entry


# ----------------------------------------------------------------------------- stage


def run(project: Project) -> StageResult:
    started = time.time()
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    from r3_model.pulled import PullError, load_pulled
    from r3_model.pulled_view import apply_pulled
    try:
        apply_pulled(project, load_pulled(project.root))
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error)))
        return result
    fab = project.spec.fabrication
    out_dir = project.build_dir(STAGE)
    printer = project.catalog.printers.get(fab.printer)
    if printer is None:
        result.add(Diagnostic.error("printer_unknown", f"fabrication.printer {fab.printer!r} is not in printers.yaml",
                                    subjects=["fabrication"], data={"printer": fab.printer, "known": sorted(project.catalog.printers)},
                                    hint="Use one of the known printer ids."))
    printed = select_parts(project, result)
    if not printed:
        result.add(Diagnostic.info("not_applicable", "no fabricated part is FDM printed", subjects=["fabrication"],
                                   data={"printed_parts": 0}))
        result.duration_s = time.time() - started
        return result
    cases, default_cases = load_cases(project)
    if default_cases:
        result.assume("fabrication", "Load cases are R3 estimates (no fabrication.load_cases): " +
                      "; ".join(f"{c['id']}: {c['description']}" for c in cases))
    if project.assembly is None:
        result.add(Diagnostic.info("print_strength_not_applicable", "no assembly section: printed load paths cannot be derived",
                                   subjects=sorted(printed), data={"parts": len(printed)}))
    result.assume("fabrication", "Isotropic linear elasticity with TDS E and an estimated Poisson ratio; FDM anisotropy enters "
                                 "only as the Z (interlayer) strength where the major principal stress is within 45 deg of the "
                                 "build direction. Strengths are 100 % infill TDS values; von Mises vs ultimate tensile strength.")
    result.assume("fabrication", "Supports: joins whose far side reaches a wheel (fixed patch on the interface); loads: carried "
                                 "masses (CAD or catalog) x the load-case acceleration, spread evenly over the interface patch; "
                                 "derived fastener masses are not included.")
    cad_parts = _cad_parts(project)
    parts = []
    for cid, part in printed.items():
        parts.append(process_part(project, part, printer, cases, cad_parts, out_dir, result))
    fastener_diagnostics, fastener_records = checks.fastener_checks(project, printed, {})
    result.extend(fastener_diagnostics)
    hole_diagnostics, hole_records = checks.hole_clearance_checks(project, printed)
    result.extend(hole_diagnostics)
    placed: dict[str, tuple[str, Any, str, list[float]]] = {}
    for entry in parts:
        cid = entry["component"]
        entry["fasteners"] = fastener_records.get(cid, [])
        entry["holes"] = hole_records.get(cid, [])
        entry["checks"] = sorted({d.code for d in result.diagnostics if cid in d.subjects})
        mesh = entry.pop("_placed", None)
        if mesh is None:
            continue
        same = [c for c in printed if printed[c].ctype.id == printed[cid].ctype.id]
        if same[0] == cid:  # one panel per part type
            label = cid if len(same) == 1 else f"{cid} (+{len(same) - 1} alike, {printed[cid].ctype.id})"
            bed = [v * 1e3 for v in printer.build_volume[:2]] if printer else [float(mesh.bounds[1, 0]), float(mesh.bounds[1, 1])]
            placed[cid] = (label, mesh, printed[cid].material.color, bed)
    if placed:
        preview = out_dir / "preview.png"
        plots.preview(preview, list(placed.values()))
        result.artifact(preview.relative_to(project.build_dir()), "plot_png", "Printed parts in their print orientation")
    plan = {"printer": {"id": fab.printer, "name": printer.name if printer else None,
                        "build_volume_mm": [v * 1e3 for v in printer.build_volume] if printer else None},
            "load_cases": cases, "min_safety_factor": fab.min_safety_factor, "max_overhang_deg": fab.max_overhang_deg,
            "min_wall_mm": float(fab.min_wall) * 1e3, "parts": {entry["component"]: entry for entry in parts},
            "totals": {"parts": len(parts), "print_mass_kg": round(sum(e.get("print_mass_kg", 0.0) for e in parts), 6),
                       "filament_length_m": round(sum(e.get("filament_length_m", 0.0) for e in parts), 4)}}
    plan_path = out_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, default=_json_default))
    result.artifact(plan_path.relative_to(project.build_dir()), "json", "Print plan: material, orientation, checks, strength, files")
    strengths = [e["strength"]["safety_factor"] for e in parts if e.get("strength", {}).get("applicable")]
    result.metrics.update({"printed_parts": len(parts), "print_mass_kg": plan["totals"]["print_mass_kg"],
                           "filament_length_m": plan["totals"]["filament_length_m"],
                           "min_safety_factor": min(strengths) if strengths else None,
                           "support_area_mm2": round(sum(e.get("support_area_mm2", 0.0) for e in parts), 3)})
    result.duration_s = time.time() - started
    return result


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    raise TypeError(f"not JSON serializable: {type(value).__name__}")
