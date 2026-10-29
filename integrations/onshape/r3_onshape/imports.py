"""Vendor and KiCad 3D models into the Onshape assembly through Onshape's standard import.

This is what a person does in the Onshape UI: *Import* a STEP file (a
translation into a new Part Studio), *Insert* that Part Studio into the
assembly, and place the instance with its transform. No FeatureScript and no
custom features are involved; every request passes the direct-CAD policy.

Imported, from ``build/connectors/connectors.json`` (connectors stage):

* components with a detailed model (official Raspberry Pi 5 STEP, the KiCad
  stage's HAT ``board.step``) at their component pose;
* every harness connector half whose type has a KiCad library model (XT60,
  pin sockets, ...) at its mated pose. Datasheet proxies have no STEP and are
  reported as skipped (they stay catalog-envelope parts).

Headless: use the official API-key transport (``ONSHAPE_ACCESS_KEY`` /
``ONSHAPE_SECRET_KEY``)::

    python -m r3_onshape.imports <project> --document <did> --workspace <wid> --assembly <eid> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .client import Transport, default_transport


@dataclass
class ImportItem:
    name: str
    step: Path
    transform: np.ndarray  # 4x4: STEP model frame (m) -> robot frame (m)
    source: str
    license: str = ""
    url: str = ""


@dataclass
class ImportReport:
    imported: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def _frame(f: dict[str, Any]) -> np.ndarray:
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = f["x"], f["y"], f["z"], f["origin"]
    return m


def plan_imports(project: Any, connectors: dict[str, Any] | None = None) -> tuple[list[ImportItem], list[dict[str, str]]]:
    """Models to import and where they go (robot frame). Needs the connectors stage output."""
    from r3_connectors.frames import Frame
    from r3_connectors.models import ModelUnavailable, resolve_step

    if connectors is None:
        path = project.build_dir("connectors") / "connectors.json"
        if not path.exists():
            raise FileNotFoundError("build/connectors/connectors.json missing: run `python -m r3_connectors` first")
        connectors = json.loads(path.read_text())
    catalog = project.catalog
    items: list[ImportItem] = []
    skipped: list[dict[str, str]] = []
    for cid, comp in connectors["components"].items():
        ctype = catalog.component(comp["type"])
        if ctype.model is None:
            continue
        try:
            step = resolve_step(ctype.model, project)
        except (ModelUnavailable, FileNotFoundError) as error:
            skipped.append({"name": cid, "reason": str(error)})
            continue
        placement = Frame.from_placement(ctype.model.placement).matrix()
        items.append(ImportItem(f"{cid} ({ctype.name})", step, _frame(comp["frame"]) @ placement, ctype.model.source,
                                ctype.model.license, ctype.model.url))
    for ref, conn in connectors["connectors"].items():
        if conn["owner"] == "component" and conn.get("embedded") and connectors["components"].get(conn["component"], {}).get("glb"):
            continue  # inside the component's own model
        geometry = catalog.connector(conn["type"]).geometry if conn["type"] in catalog.connectors else None
        if geometry is None:
            continue
        if geometry.model.source != "kicad":
            skipped.append({"name": ref, "reason": f"{conn['type']} is a datasheet proxy (no STEP model)"})
            continue
        step = resolve_step(geometry.model)
        placement = Frame.from_placement(geometry.model.placement).matrix()
        items.append(ImportItem(f"{ref} ({conn['type']})", step, _frame(conn["frame"]) @ placement, "kicad",
                                geometry.model.license))
    return items, skipped


def _wait(api: Transport, translation_id: str, timeout: float, poll: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    while True:
        info = api.json("GET", f"/translations/{translation_id}")
        state = info.get("requestState")
        if state == "DONE":
            return info
        if state == "FAILED":
            raise RuntimeError(f"Onshape import failed: {info.get('failureReason')}")
        if time.time() > deadline:
            raise TimeoutError(f"Onshape import {translation_id} still {state} after {timeout:.0f} s")
        time.sleep(poll)


def import_item(api: Transport, did: str, wid: str, assembly: str, item: ImportItem,
                timeout: float = 900.0, poll: float = 2.0) -> dict[str, Any]:
    """Import one STEP as a Part Studio, insert it into ``assembly`` and place it."""
    translation = api.upload(f"/translations/d/{did}/w/{wid}", item.step, {
        "formatName": "STEP", "translate": True, "flattenAssemblies": True, "createComposite": False,
        "allowFaultyParts": True, "storeInDocument": False, "encodedFilename": item.step.name})
    info = _wait(api, translation["id"], timeout, poll)
    element = (info.get("resultElementIds") or [None])[0]
    if not element:
        raise RuntimeError(f"Onshape import of {item.step.name} produced no element")
    base = f"/assemblies/d/{did}/w/{wid}/e/{assembly}"
    before = {i["id"] for i in api.json("GET", base).get("rootAssembly", {}).get("instances", [])}
    api.json("POST", f"{base}/instances", body={"documentId": did, "elementId": element, "isWholePartStudio": True})
    after = api.json("GET", base).get("rootAssembly", {}).get("instances", [])
    new = [i for i in after if i["id"] not in before]
    if not new:
        raise RuntimeError(f"inserting {item.name} added no instance")
    # Placement: the same absolute transform a person sets with the Transform dialog (row-major 4x4, metres).
    api.json("POST", f"{base}/occurrencetransforms", body={
        "occurrences": [{"path": [new[0]["id"]]}], "transform": [float(v) for v in item.transform.ravel()],
        "isRelative": False})
    return {"name": item.name, "element": element, "instance": new[0]["id"], "step": item.step.name,
            "source": item.source, "license": item.license, "url": item.url}


def import_models(api: Transport, project: Any, did: str, wid: str, assembly: str, dry_run: bool = False) -> ImportReport:
    items, skipped = plan_imports(project)
    report = ImportReport(skipped=skipped)
    for item in items:
        if dry_run:
            report.imported.append({"name": item.name, "step": str(item.step), "source": item.source, "dry_run": True,
                                    "transform": [round(float(v), 7) for v in item.transform.ravel()]})
            continue
        report.imported.append(import_item(api, did, wid, assembly, item))
    return report


def main(argv: list[str] | None = None) -> int:
    from r3_model.project import Project

    parser = argparse.ArgumentParser(prog="r3_onshape.imports", description=__doc__.splitlines()[0])
    parser.add_argument("project")
    parser.add_argument("--document", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--assembly", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    project = Project.load(args.project)
    api = default_transport()
    if api is None and not args.dry_run:
        print("no Onshape transport: set ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY", file=sys.stderr)
        return 2
    report = import_models(api, project, args.document, args.workspace, args.assembly, args.dry_run)  # type: ignore[arg-type]
    out = project.build_dir("cad") / "model_imports.json"
    out.write_text(json.dumps({"imported": report.imported, "skipped": report.skipped}, indent=1))
    print(json.dumps({"imported": len(report.imported), "skipped": len(report.skipped), "report": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
