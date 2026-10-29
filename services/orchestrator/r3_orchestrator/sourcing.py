"""Sourcing operations shared by the CLI (``r3 source``), MCP and HTTP API.

* :func:`list_slots`: every ``source:`` slot with its requirement, pin and last search.
* :func:`search_slot`: run the providers, rank with digital-twin checks, store
  ``build/sourcing/<slot>/candidates.json``. Never changes the project.
* :func:`choose`: build the chosen candidate's catalog entry
  (``catalog/components/sourced_<id>.yaml``), pin it in ``r3.lock.yaml``
  (``sourcing.<slot>``) and re-check the robot. Re-sourcing a pinned slot is
  this same explicit command.
* :func:`check_pins`: model-stage diagnostics for sourced slots (unpinned,
  missing or edited catalog file, a part that violates its requirements).

Twin checks substitute the candidate's built catalog entry into the robot and
run the model stage's checker (connectors, bindings, pin roles, rail and logic
voltages, I2C, envelope interference, mass budget), the assembly compiler when
the spec has an ``assembly`` section, and an interface-feature comparison
against the slot's template part. Only findings that the current part does not
already produce count against a candidate.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from r3_catalog.sourcing import (Candidate, HttpClient, Ranked, Requirement, TwinCheck, build_entry, catalog_params, entry_id,
                                 file_name, rank, render_file, search, select_template, substituted_catalog)
from r3_catalog.sourcing.build import digest as text_digest
from r3_catalog.sourcing.providers import PROVIDERS, make_providers
from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

BUILD = "sourcing"
CANDIDATES_FILE = "candidates.json"
ELECTRICAL = {"connector", "binding", "power", "uart", "pwm", "input", "logic", "i2c", "wire", "harness", "unknown", "catalog"}
Progress = Callable[[str, dict[str, Any]], None]


class SourcingError(KeyError):
    """Usage errors (unknown slot, no stored search, bad candidate index)."""


# ------------------------------------------------------------------ slots
def requirement_for(project: Project, slot: str) -> Requirement:
    slots = project.spec.source_slots()
    if slot not in slots:
        # Accept a member component id of a grouped slot.
        owner = next((key for key, members in slots.items() if slot in members), None)
        if owner is None:
            raise SourcingError(f"no sourcing slot {slot!r}; slots: {', '.join(slots) or 'none (add source: to a component)'}")
        slot = owner
    members = slots[slot]
    return Requirement.from_source(slot, project.spec.components[members[0]].source, members)


def _slot_dir(project: Project, slot: str) -> Path:
    return project.root / "build" / BUILD / slot


def stored_search(project: Project, slot: str) -> dict[str, Any] | None:
    path = _slot_dir(project, slot) / CANDIDATES_FILE
    return json.loads(path.read_text()) if path.exists() else None


def _authored_types(root: Path) -> dict[str, str]:
    raw = yaml.safe_load((root / "robot.yaml").read_text()) or {}
    return {cid: (comp or {}).get("type", "") for cid, comp in (raw.get("components") or {}).items()}


def list_slots(project: Project) -> dict[str, Any]:
    pins = project.lock.get("sourcing") or {}
    authored = _authored_types(project.root)
    out = []
    for slot, members in project.spec.source_slots().items():
        requirement = requirement_for(project, slot)
        stored = stored_search(project, slot)
        pin = pins.get(slot)
        out.append({
            "slot": slot, "components": members, "kind": requirement.kind, "requires": requirement.raw,
            "prefer": requirement.prefer, "quantity": requirement.quantity,
            "budget": requirement.budget.to_json() if requirement.budget else None, "providers": requirement.providers,
            "authored_type": authored.get(members[0], ""), "resolved_type": project.spec.components[members[0]].type,
            "pinned": pin, "requirement_sha256": requirement.digest(),
            "pin_stale": bool(pin) and pin.get("requirement_sha256") not in (None, requirement.digest()),
            "last_search": None if stored is None else {
                "searched_at": stored["searched_at"], "candidates": len(stored["ranked"]),
                "eligible": sum(1 for r in stored["ranked"] if r["tier"] == "eligible"),
                "requirement_sha256": stored["requirement_sha256"], "reports": stored["reports"]},
        })
    providers = [cls(http=HttpClient(offline=True)).describe() for cls in PROVIDERS.values()]
    return {"ok": True, "project": str(project.root), "slots": out, "providers": providers}


# ------------------------------------------------------------- twin checks
def _key(d: Diagnostic) -> tuple[str, tuple[str, ...]]:
    return d.code, tuple(sorted(d.subjects))


def _category(code: str) -> str:
    if code == "component_interference":
        return "mechanical"
    if code.startswith("mass"):
        return "mass"
    if code.startswith(("join", "assembly", "hole", "fit", "interface", "joint")):
        return "assembly"
    return "electrical" if code.split("_")[0] in ELECTRICAL else "model"


def _twin_diagnostics(project: Project, spec: Any, catalog: Any) -> tuple[dict[tuple[str, tuple[str, ...]], Diagnostic], dict[str, Any]]:
    from r3_model.assembly import compile_assembly

    from .model_stage import ModelChecker

    temp = Project(root=project.root, spec=spec, lock=project.lock)
    temp._catalog = catalog
    # Envelope checks only: build/cad and build/wiring describe the current part, not the candidate.
    temp.read_build_json = lambda relative: None  # type: ignore[method-assign]
    result = StageResult(stage=BUILD)
    checker = ModelChecker(temp, result)
    checker.check_connectors()
    checker.check_bindings()
    checker.check_roles()
    checker.check_voltages()
    checker.check_i2c()
    checker.check_interference(checker.solids())
    mass = checker.check_mass()
    if spec.assembly is not None:
        for diagnostic in compile_assembly(spec, catalog).diagnostics:
            result.add(diagnostic)
    found = {}
    for diagnostic in result.diagnostics:
        if diagnostic.severity.value != "info":
            found.setdefault(_key(diagnostic), diagnostic)
    return found, mass


def make_checker(project: Project, requirement: Requirement, template: Any) -> Callable[[Candidate], list[TwinCheck]]:
    baseline, base_mass = _twin_diagnostics(project, project.spec, project.catalog) if template is not None else ({}, None)
    interfaces = [i for i in project.spec.mechanical.interfaces if set(i.parts) & set(requirement.components)]

    def check(candidate: Candidate) -> list[TwinCheck]:
        try:
            entry, meta = build_entry(candidate, requirement, template)
        except ValueError as error:
            return [TwinCheck("build", "sourcing_build_failed", "error", f"catalog entry does not validate: {error}")]
        spec = project.spec.model_copy(deep=True)
        for cid in requirement.components:
            spec.components[cid].type = entry["id"]
        found, mass = _twin_diagnostics(project, spec, substituted_catalog(project.catalog, entry))
        checks = [TwinCheck(_category(d.code), d.code, d.severity.value, d.message, d.subjects, d.data)
                  for key, d in found.items() if key not in baseline]
        delta = mass["total"] - base_mass["total"] if base_mass else None
        checks.append(TwinCheck("mass", "sourcing_mass_budget", "info",
                                f"robot mass {mass['total']:.3f} kg" + (f" ({delta * 1e3:+.0f} g vs the current part)" if delta is not None else "")
                                + (f", limit {mass['limit']:.3f} kg" if mass["limit"] else ""),
                                requirement.components, {"total": mass["total"], "limit": mass["limit"], "delta": delta}))
        if template is not None and interfaces:
            kept = {f["id"] for f in entry["mechanical"].get("features", [])}
            removed = [f for f in template.mechanical.features if f.id not in kept and f.kind != "face"]
            for interface in interfaces:
                if removed and interface.kind in ("revolute_fit", "press_fit", "drive_flat", "spline", "ball_joint"):
                    checks.append(TwinCheck(
                        "interface", "sourcing_interface_feature_changed", "warning",
                        f"interface {interface.id} ({interface.kind} {interface.parts[0]}/{interface.parts[1]}) used the template's "
                        f"{', '.join(f'{f.id} ({f.kind} {f.diameter * 1e3:.2f} mm)' for f in removed)}; the sourced part has "
                        f"{', '.join(sorted(kept)) or 'no features'}; an adapter or a redesigned mating part is needed",
                        [interface.id, *interface.parts], {"removed": [f.id for f in removed], "features": sorted(kept)}))
        for note in meta["notes"]:
            if "dropped" in note or "removed" in note:
                checks.append(TwinCheck("mechanical", "sourcing_template_feature_dropped", "info", note, requirement.components))
        return checks

    return check


# ------------------------------------------------------------------ search
def search_slot(project: Project, slot: str, provider_ids: list[str] | None = None, http: HttpClient | None = None,
                progress: Progress | None = None, providers: list[Any] | None = None) -> dict[str, Any]:
    requirement = requirement_for(project, slot)
    emit = progress or (lambda event, payload: None)
    emit("search_started", {"slot": requirement.slot, "requires": requirement.raw, "components": requirement.components})
    if providers is None:
        providers = make_providers(provider_ids or requirement.providers or None, http)
    found = search(requirement, providers=providers, provider_ids=provider_ids, progress=emit)
    template = select_template(project.spec, project.catalog, requirement.components, requirement.kind)
    # Check the slot's current part against its own requirements (a fixed `type` may already violate them).
    current = None
    authored = project.spec.components[requirement.components[0]].type
    if authored in project.catalog.components:
        matches = requirement.evaluate(catalog_params(project.catalog.components[authored]), project.catalog.components[authored].kind)
        current = {"type": authored, "matches": [m.to_json() for m in matches],
                   "satisfied": all(m.status == "pass" for m in matches)}
    emit("ranking_started", {"candidates": len(found.candidates), "template": template.id if template else None})
    incumbent = (template.part_number or template.name) if template is not None and template.id == authored else ""
    ranked = rank(requirement, found.candidates, make_checker(project, requirement, template), incumbent=incumbent)
    for entry in ranked[:50]:
        emit("candidate", {"rank": entry.rank, "tier": entry.tier, "score": round(entry.score, 2), "id": entry.candidate.id,
                           "name": entry.candidate.name, "provider": entry.candidate.provider, "unit_price": entry.unit_price,
                           "currency": entry.currency, "twin_errors": entry.twin_errors, "twin_warnings": entry.twin_warnings})
    payload = {
        "ok": True, "slot": requirement.slot, "components": requirement.components, "kind": requirement.kind,
        "requires": requirement.raw, "prefer": requirement.prefer, "quantity": requirement.quantity,
        "requirement_sha256": requirement.digest(), "searched_at": time.time(), "template": template.id if template else None,
        "current": current, "reports": [r.model_dump(mode="json") for r in found.reports],
        "counts": {tier: sum(1 for r in ranked if r.tier == tier) for tier in ("eligible", "unverified", "rejected")},
        "ranked": [r.to_json() for r in ranked],
    }
    folder = _slot_dir(project, requirement.slot)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CANDIDATES_FILE).write_text(json.dumps(payload, indent=1, default=str))
    for provider in providers:
        if getattr(provider, "last_output", ""):
            (folder / f"{provider.id}_output.json").write_text(provider.last_output)
    emit("search_finished", {"slot": requirement.slot, "counts": payload["counts"],
                             "best": _summary(ranked[0]) if ranked and ranked[0].tier != "rejected" else None})
    return payload


def _summary(entry: Ranked) -> dict[str, Any]:
    return {"rank": entry.rank, "id": entry.candidate.id, "name": entry.candidate.name, "tier": entry.tier,
            "score": round(entry.score, 2), "unit_price": entry.unit_price, "currency": entry.currency}


# ------------------------------------------------------------------ choose
def _pick(stored: dict[str, Any], choice: str | int) -> dict[str, Any]:
    ranked = stored["ranked"]
    if isinstance(choice, int) or str(choice).isdigit():
        index = int(choice)
        if not 1 <= index <= len(ranked):
            raise SourcingError(f"--choose {index}: the last search has {len(ranked)} candidates (1-based ranks)")
        return ranked[index - 1]
    for entry in ranked:
        if entry["candidate"]["id"] == choice:
            return entry
    raise SourcingError(f"candidate {choice!r} is not in the last search; ids: {', '.join(r['candidate']['id'] for r in ranked[:10])}")


def choose(root: str | Path, slot: str, choice: str | int, allow_rejected: bool = False) -> dict[str, Any]:
    """Pin a candidate of the last stored search (rank number or candidate id)."""
    project = Project.load(root)
    requirement = requirement_for(project, slot)
    stored = stored_search(project, requirement.slot)
    if stored is None:
        raise SourcingError(f"no stored search for {requirement.slot!r}; run `r3 source {requirement.slot}` first")
    if stored["requirement_sha256"] != requirement.digest():
        return {"ok": False, "code": "sourcing_search_stale", "slot": requirement.slot,
                "message": "robot.yaml's requirements changed since the last search; search again before choosing"}
    picked = _pick(stored, choice)
    if picked["tier"] == "rejected" and not allow_rejected:
        return {"ok": False, "code": "sourcing_candidate_rejected", "slot": requirement.slot, "reasons": picked["reasons"],
                "message": f"candidate {picked['candidate']['id']} fails the hard requirements: {'; '.join(picked['reasons'])}"}
    candidate = Candidate.model_validate(picked["candidate"])
    template = select_template(project.spec, project.catalog, requirement.components, requirement.kind)
    from r3_catalog.sourcing import ParamMatch

    matches = [ParamMatch(**{k: m[k] for k in ("param", "required", "status", "detail", "value", "provenance", "source")})
               for m in picked["matches"]]
    entry, meta = build_entry(candidate, requirement, template, matches)
    meta["rank"], meta["score"], meta["tier"] = picked["rank"], picked["score"], picked["tier"]
    meta["twin"] = picked["twin"]
    text = render_file(entry, meta)
    catalog_dir = project.root / "catalog" / "components"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    path = catalog_dir / file_name(candidate)
    path.write_text(text)
    offer = candidate.best_offer(requirement.quantity)
    pin = {
        "catalog_id": entry["id"], "catalog_file": str(path.relative_to(project.root)), "digest": text_digest(text),
        "provider": candidate.provider, "candidate_id": candidate.id, "name": candidate.name, "manufacturer": candidate.manufacturer,
        "mpn": candidate.mpn, "vendor": offer.vendor if offer else candidate.manufacturer, "url": candidate.primary_url,
        "price": ({"amount": offer.unit_price(requirement.quantity), "currency": offer.currency, "quantity": requirement.quantity}
                  if offer and offer.unit_price(requirement.quantity) is not None else None),
        "retrieved_at": candidate.retrieved_at, "requirement_sha256": requirement.digest(), "components": requirement.components,
        "cad": meta["cad"]["status"],
    }
    lock = yaml.safe_load((project.root / "r3.lock.yaml").read_text()) if (project.root / "r3.lock.yaml").exists() else {}
    lock = lock or {}
    previous = (lock.get("sourcing") or {}).get(requirement.slot)
    lock.setdefault("sourcing", {})[requirement.slot] = pin
    (project.root / "r3.lock.yaml").write_text(yaml.safe_dump(lock, sort_keys=True))
    if previous and previous.get("catalog_file") and previous["catalog_file"] != pin["catalog_file"]:
        still_used = any(p.get("catalog_file") == previous["catalog_file"] for p in lock["sourcing"].values())
        old = project.root / previous["catalog_file"]
        if not still_used and old.exists():
            old.unlink()
    # Re-check the robot with the pinned part, exactly as the model stage will.
    reloaded = Project.load(project.root)
    result = StageResult(stage=BUILD)
    result.extend(check_pins(reloaded))
    return {"ok": True, "slot": requirement.slot, "components": requirement.components, "pinned": pin, "previous": previous,
            "catalog_file": pin["catalog_file"], "entry": entry, "twin": picked["twin"], "tier": picked["tier"],
            "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics]}


def unpin(root: str | Path, slot: str) -> dict[str, Any]:
    project = Project.load(root)
    requirement = requirement_for(project, slot)
    lock = yaml.safe_load((project.root / "r3.lock.yaml").read_text()) or {}
    pin = (lock.get("sourcing") or {}).pop(requirement.slot, None)
    if not lock.get("sourcing"):
        lock.pop("sourcing", None)
    (project.root / "r3.lock.yaml").write_text(yaml.safe_dump(lock, sort_keys=True))
    return {"ok": True, "slot": requirement.slot, "removed": pin}


# --------------------------------------------------------- model-stage checks
def check_pins(project: Project) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    pins = project.lock.get("sourcing") or {}
    for slot, members in project.spec.source_slots().items():
        requirement = requirement_for(project, slot)
        pin = pins.get(slot)
        type_id = project.spec.components[members[0]].type
        if pin:
            path = project.root / str(pin.get("catalog_file", ""))
            if not path.is_file():
                diagnostics.append(Diagnostic.error(
                    "sourcing_catalog_missing", f"slot {slot} is pinned to {pin.get('catalog_id')} but {pin.get('catalog_file')} is missing",
                    subjects=members, hint=f"Re-run `r3 source {slot} --choose N` to rebuild the catalog entry."))
                continue
            if text_digest(path.read_text()) != pin.get("digest"):
                diagnostics.append(Diagnostic.error(
                    "sourcing_digest_mismatch", f"{pin['catalog_file']} changed since it was pinned (digest {pin.get('digest')})",
                    subjects=members, data={"pinned": pin.get("digest"), "actual": text_digest(path.read_text())},
                    hint="Sourced entries are reproducible build inputs: re-source the slot, or copy the file to a hand-maintained "
                         "catalog entry and set the component's `type`."))
            if pin.get("requirement_sha256") and pin["requirement_sha256"] != requirement.digest():
                diagnostics.append(Diagnostic.warning(
                    "sourcing_pin_stale", f"slot {slot}'s requirements changed after {pin.get('name', pin['catalog_id'])} was pinned",
                    subjects=members, hint=f"Run `r3 source {slot}` and choose again (re-sourcing is explicit)."))
        elif not type_id:
            continue  # component_unsourced comes from the graph
        if type_id not in project.catalog.components:
            continue
        component = project.catalog.components[type_id]
        params = catalog_params(component)
        sourced = _sourced_params(project, pin)
        params.update(sourced)
        for match in requirement.evaluate(params, component.kind):
            if match.status == "fail":
                diagnostics.append(Diagnostic.error(
                    "source_requirement_unmet", f"{type_id} (slot {slot}) violates {match.param} {match.required}: {match.detail}",
                    subjects=members, data={"param": match.param, "required": match.required, "value": match.value},
                    hint=f"Run `r3 source {slot}` to find a part that meets the requirement, or relax source.requires."))
            elif match.status == "unknown" and pin:
                diagnostics.append(Diagnostic.warning(
                    "source_requirement_unverified", f"{type_id} (slot {slot}): no sourced value for {match.param} {match.required}",
                    subjects=members, data={"param": match.param}))
        estimated = sorted(name for name, value in sourced.items() if value.status == "estimated")
        if pin and estimated:
            diagnostics.append(Diagnostic.info(
                "sourcing_estimated_parameters", f"{pin['name']}: {len(estimated)} parameter(s) are estimates: {', '.join(estimated)}",
                subjects=members, data={"estimated": estimated}))
    return diagnostics


def _sourced_params(project: Project, pin: dict[str, Any] | None) -> dict[str, Any]:
    from r3_catalog.sourcing import SourcedValue

    if not pin:
        return {}
    path = project.root / str(pin.get("catalog_file", ""))
    if not path.is_file():
        return {}
    meta = (yaml.safe_load(path.read_text()) or {}).get("sourced") or {}
    return {name: SourcedValue.model_validate(value) for name, value in (meta.get("params") or {}).items()}


# --------------------------------------------------------------- BOM pricing
def price_bom(project: Project, http: HttpClient | None = None, provider_ids: list[str] | None = None) -> dict[str, Any]:
    """Price and stock-check the PCB BOM's MPNs (``build/pcb/*-bom.csv`` MPN column) through the distributor APIs."""
    mpns: dict[str, int] = {}
    files = sorted((project.root / "build" / "pcb").glob("*-bom.csv")) if (project.root / "build" / "pcb").exists() else []
    for path in files:
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                mpn = (row.get("MPN") or row.get("mpn") or "").strip()
                if mpn and mpn != "~":
                    mpns[mpn] = mpns.get(mpn, 0) + int(float(row.get("Qty") or row.get("Quantity") or 1))
    providers = [p for p in make_providers(provider_ids or ["nexar", "digikey", "mouser"], http) if hasattr(p, "price_mpn")]
    available, skipped = [], []
    for provider in providers:
        try:
            provider.check_available()
            available.append(provider)
        except Exception as error:  # noqa: BLE001 - ProviderUnavailable: reported per provider
            skipped.append({"provider": provider.id, "code": getattr(error, "code", "unavailable"), "message": str(error)})
    rows = []
    for mpn, quantity in sorted(mpns.items()):
        offers = []
        for provider in available:
            try:
                for candidate in provider.price_mpn(mpn):
                    offers += [{"provider": provider.id, **o.model_dump(mode="json"), "unit_price_at_qty": o.unit_price(quantity)}
                               for o in candidate.offers]
            except Exception as error:  # noqa: BLE001 - network/API failures are per-row findings
                offers.append({"provider": provider.id, "error": f"{type(error).__name__}: {error}"})
        priced = [o for o in offers if o.get("unit_price_at_qty") is not None and (o.get("stock") or 0) >= quantity]
        best = min(priced, key=lambda o: o["unit_price_at_qty"]) if priced else None
        rows.append({"mpn": mpn, "quantity": quantity, "offers": offers, "best": best})
    return {"ok": True, "bom_files": [str(p.relative_to(project.root)) for p in files], "lines": rows, "skipped": skipped,
            "message": "" if mpns else "no MPN column in build/pcb/*-bom.csv (run the pcb stage with MPN fields)"}
