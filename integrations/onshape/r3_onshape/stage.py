"""`cad` stage: build the robot in Onshape from robot.yaml and import verified data.

Behavior:
* If ``build/cad/cad_model.json`` exists and was produced by the current
  generator digest (plans + spec + catalog + generator/validator versions),
  it is reused (offline CI, no Onshape access needed).
* Otherwise, with an Onshape transport (headless session, onshape-native
  bridge or API keys), a fresh document is built from scratch with standard
  features, verified (fasteners, interfaces, joints, transmissions, retention,
  motion, assembly mates) and pinned in ``r3.lock.yaml``. Failed attempts are
  deleted; the superseded document is trashed. ``R3_CAD_FORCE=1`` rebuilds.
* Without a transport, downstream stages fall back to catalog geometry and a
  ``cad_geometry_estimated`` warning is emitted.
* Screenshots come from Onshape's REST shaded-view endpoints
  (``build/cad/views/{iso,front,top,right}.png``), never from a browser, so the
  stage runs headless with API keys. ``R3_CAD_VIEWS=0`` skips them.
"""

from __future__ import annotations

import json
import os
import time

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from .client import Transport, default_transport


def run(project: Project) -> StageResult:
    result = StageResult(stage="cad", inputs=project.fingerprint())
    started = time.time()
    from .purchased import diagnostics as purchased_diagnostics
    purchased_diagnostics(project, result)
    result.assume("mechanical", "Purchased parts resolve through the model bank and use real vendor geometry with datasheet mass overrides. "
                                "Missing exact models remain explicitly diagnosed catalog proxies; geometry validation is separate from sourcing.")
    result.assume("mechanical", "Fabricated parts get holes, bores, sockets and slots from their catalog interfaces (the joins they "
                                "serve); mass from geometry x material density. Missing-source fasteners are labelled standard-geometry proxies.")
    result.assume("mechanical", "Fit checks run on the Onshape Part Studio geometry moved by the solved assembly transforms, which "
                                "must equal the compiled poses.")
    if project.spec.cad.provider == "none":
        result.add(Diagnostic.info("cad_disabled", "cad.provider is none"))
        return result
    if project.spec.assembly is None:
        result.add(Diagnostic.error("cad_assembly_required", "the organized CAD build needs an assembly section (objects joined at interfaces)"))
        return result
    if not result.ok:
        result.duration_s = time.time() - started
        return result
    from .organized import input_digest, sync_organized, write_model

    digest = input_digest(project)
    existing = project.read_build_json("cad/cad_model.json")
    force = os.environ.get("R3_CAD_FORCE") == "1"
    if existing and existing.get("source", {}).get("generator_digest") == digest and not force:
        result.add(Diagnostic.info("cad_model_reused", "cad_model.json matches the current generator digest",
                                   data={"document": existing["source"].get("url"), "digest": digest}))
        _summarize(project, existing, result)
        result.duration_s = time.time() - started
        return result
    api = default_transport()
    if api is None:
        result.add(Diagnostic.warning("cad_geometry_estimated", "no Onshape transport (headless session, onshape-native bridge or API keys); "
                                      "downstream stages use catalog geometry",
                                      hint="Run `python -m r3_onshape.session login` once, or configure Onshape API keys."))
        return result
    previous = (project.lock.get("cad", {}) or {})
    attempt = previous.get("attempt", 0) + 1
    from .studio import StudioBuildError
    from .client import OnshapeError
    try:
        report = sync_organized(project, api, result, attempt_label=os.environ.get("R3_CAD_LABEL", ""))
    except StudioBuildError as error:
        code = error.feature if error.feature.startswith("model_") else "cad_purchased_import_failed"
        result.add(Diagnostic.error(code, str(error), subjects=[error.part], data={"feature": error.feature}))
        result.duration_s = time.time() - started
        return result
    except OnshapeError as error:
        result.add(Diagnostic.error("cad_api_quota" if error.status == 429 else "onshape_request_failed", str(error),
                                    subjects=["cad"], data={"status": error.status}))
        result.duration_s = time.time() - started
        return result
    if result.ok and report.get("robot") is not None:
        path = write_model(project, report, api.name, digest)
        model = json.loads(path.read_text())
        result.artifact(path.relative_to(project.build_dir()), "json", "Per-part mass properties, meshes, fit report, steering table, RF")
        for name, part in sorted(model["parts"].items()):
            result.artifact(f"cad/{part['mesh']}", "stl")
        for image in model.get("images", []):
            result.artifact(f"cad/{image}", "plot_png", "Onshape shaded view (headless REST) or solved-pose render")
        robot = report["robot"]
        project.write_lock("cad", {"provider": "onshape", "document_id": robot.did, "workspace_id": robot.wid,
                                   "element_id": model["source"]["element_id"], "assembly_id": model["source"]["assembly_id"],
                                   "url": model["source"]["url"], "microversion": model["source"]["microversion"],
                                   "generator_digest": digest, "validator_version": model["source"]["validator_version"],
                                   "mesh_digest": model["source"]["mesh_digest"], "attempt": attempt,
                                   "revision": model["source"]["microversion"] or digest,
                                   "library_document_id": model["source"]["library"]["document_id"],
                                   "library_version_id": model["source"]["library"]["version_id"]})
        _summarize(project, model, result)
        old = previous.get("document_id")
        if old and old != robot.did:
            # Keep exactly one live document per robot (plus the parts library): the superseded build is trashed.
            try:
                from .sync import delete_document
                delete_document(api, old)
                result.add(Diagnostic.info("cad_previous_document_trashed", "superseded Onshape document moved to trash",
                                           data={"document_id": old}))
            except Exception as error:  # noqa: BLE001 - report, do not fail a good build
                result.add(Diagnostic.warning("cad_previous_document_delete_failed", str(error), data={"document_id": old}))
    else:
        project.write_lock("cad", {**(project.lock.get("cad") or {}), "attempt": attempt})
    result.duration_s = time.time() - started
    return result


def _views(project: Project, api: Transport | None, source: dict, result: StageResult) -> None:
    """Server-rendered PNGs of the CAD (no browser); failures only warn."""
    if api is None or os.environ.get("R3_CAD_VIEWS") == "0":
        return
    ids = (source.get("document_id"), source.get("workspace_id"), source.get("element_id"))
    if not all(ids):
        return
    from .views import render_document_views

    try:
        paths = render_document_views(api, *ids, project.build_dir("cad") / "views")
    except Exception as error:  # noqa: BLE001 - images are evidence, not engineering data
        result.add(Diagnostic.warning("cad_views_failed", f"Onshape shaded views failed: {error}", subjects=["cad"],
                                      data={"status": getattr(error, "status", None)}))
        return
    result.metrics["cad_views"] = len(paths)


def _count(value: object) -> int:
    return value if isinstance(value, int) else len(value or [])  # type: ignore[arg-type]


def _summarize(project: Project, model: dict, result: StageResult) -> None:
    parts = model.get("parts", {})
    total = sum(p["mass"] for p in parts.values())
    fit = model.get("fit", {})
    steering = (model.get("kinematics") or {}).get("steering") or {}
    result.metrics.update({"parts": len(parts), "total_mass_kg": round(total, 4), "onshape_document": model["source"].get("url"),
                           "verified_fasteners": len(fit.get("verified_fasteners", [])),
                           "verified_interfaces": len(fit.get("verified_interfaces", [])),
                           "ackermann_pct_full_lock": steering.get("ackermann_pct_full_lock"),
                           "assembly_mates": _count((model.get("assembly") or {}).get("mates", []))})
    limit = project.spec.requirements.max_mass
    if limit and total > limit:
        result.add(Diagnostic.error("mass_budget_exceeded", f"CAD mass {total:.3f} kg exceeds {limit:.3f} kg",
                                    subjects=["robot"], data={"mass": total, "limit": limit}))
    for view in sorted((project.build_dir("cad") / "views").glob("*.png")):
        result.artifact(f"cad/views/{view.name}", "plot_png", f"Onshape shaded view ({view.stem}, server-rendered)")
    shot = project.root / "build" / "cad" / "onshape_isometric.png"
    if shot.exists():
        result.artifact("cad/onshape_isometric.png", "plot_png", "Onshape viewport capture")
