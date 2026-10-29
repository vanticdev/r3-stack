"""`cad` stage: build the robot in Onshape from robot.yaml and import its data.

Behavior:
* If ``build/cad/cad_model.json`` exists and was produced by the current
  generator digest, it is reused (offline CI, no Onshape access needed).
* Otherwise, when an Onshape transport is available (onshape-native bridge or
  API keys), a fresh document is built from scratch, verified, and pinned in
  ``r3.lock.yaml``. Failed attempts are deleted. ``R3_CAD_FORCE=1`` forces a rebuild.
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
from .sync import sync, write_cad_model


def _direct_builder():
    """The direct-CAD builder (standard Onshape features only). Returns
    (builder, digest_fn) or None while it is not installed."""
    try:
        from .direct import build_robot, input_digest
    except ImportError:
        return None
    return build_robot, input_digest


def run(project: Project) -> StageResult:
    result = StageResult(stage="cad", inputs=project.fingerprint())
    started = time.time()
    result.assume("mechanical", "Purchased parts are modeled as envelopes; their material density is set so CAD mass equals the catalog mass.")
    result.assume("mechanical", "Fabricated structure (plates, standoffs, arms, axles) mass is computed by Onshape from geometry x material density.")
    result.assume("mechanical", "Interference/contact is evaluated on exact B-rep in the modeled (straight-ahead) pose only; joint sweeps are checked by the wiring stage.")
    if project.spec.cad.provider == "none":
        result.add(Diagnostic.info("cad_disabled", "cad.provider is none"))
        return result
    direct = _direct_builder()
    if direct is None:
        result.add(Diagnostic.error("cad_builder_unavailable", "direct-CAD builder (r3_onshape.direct) is not installed",
                                    hint="FeatureScript generation was removed by policy; CAD is built with standard Onshape features."))
        return result
    builder, input_digest = direct
    digest = input_digest(project)
    existing = project.read_build_json("cad/cad_model.json")
    force = os.environ.get("R3_CAD_FORCE") == "1"
    if existing and existing.get("source", {}).get("generator_digest") == digest and not force:
        result.add(Diagnostic.info("cad_model_reused", "cad_model.json matches the current generator digest",
                                   data={"document": existing["source"].get("url"), "digest": digest}))
        if not (project.build_dir("cad") / "views" / "iso.png").exists():
            _views(project, default_transport(), existing.get("source", {}), result)
        _summarize(project, existing, result)
        result.duration_s = time.time() - started
        return result
    api = default_transport()
    if api is None:
        result.add(Diagnostic.warning("cad_geometry_estimated", "no Onshape transport (onshape-native bridge or API keys); downstream stages use catalog geometry",
                                      hint="Start the onshape-native companion or set ONSHAPE_ACCESS_KEY/ONSHAPE_SECRET_KEY."))
        return result
    previous = (project.lock.get("cad", {}) or {})
    attempt = previous.get("attempt", 0) + 1
    report = sync(project, api, result, builder, attempt_label=f"attempt-{attempt}")
    if result.ok and report.document:
        path = write_cad_model(project, report, api.name)
        result.artifact(path.relative_to(project.build_dir()), "json", "Per-part mass properties, bounding boxes, meshes, contacts")
        for name in sorted(report.parts):
            result.artifact(f"cad/meshes/{name}.stl", "stl")
        project.write_lock("cad", {"provider": "onshape", "document_id": report.document.did, "workspace_id": report.document.wid,
                                   "element_id": report.document.part_studio, "url": report.document.url(),
                                   "generator_digest": digest, "attempt": attempt, "revision": digest})
        _views(project, api, {"document_id": report.document.did, "workspace_id": report.document.wid,
                              "element_id": report.document.part_studio}, result)
        _summarize(project, json.loads(path.read_text()), result)
        old = previous.get("document_id")
        if old and old != report.document.did:
            # Keep exactly one live document per robot: the superseded build is trashed.
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


def _summarize(project: Project, model: dict, result: StageResult) -> None:
    parts = model.get("parts", {})
    total = sum(p["mass"] for p in parts.values())
    result.metrics.update({"parts": len(parts), "total_mass_kg": round(total, 4), "onshape_document": model["source"].get("url")})
    limit = project.spec.requirements.max_mass
    if limit and total > limit:
        result.add(Diagnostic.error("mass_budget_exceeded", f"CAD mass {total:.3f} kg exceeds {limit:.3f} kg",
                                    subjects=["robot"], data={"mass": total, "limit": limit}))
    for view in sorted((project.build_dir("cad") / "views").glob("*.png")):
        result.artifact(f"cad/views/{view.name}", "plot_png", f"Onshape shaded view ({view.stem}, server-rendered)")
    shot = project.root / "build" / "cad" / "onshape_isometric.png"
    if shot.exists():
        result.artifact("cad/onshape_isometric.png", "plot_png", "Onshape viewport capture")
