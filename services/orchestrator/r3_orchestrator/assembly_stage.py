"""Validate native pulled assembly poses and exact STEP interference."""

from __future__ import annotations

import itertools
import json
import math
import time
from typing import Any

import numpy as np
from scipy.spatial import HalfspaceIntersection, QhullError

from r3_model.assembly import AssemblyResult, invert
from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project
from r3_model.units import to_si

from .geometry import TOUCH_TOLERANCE, Solid, _chebyshev_center, make_solid, outline_solid, overlap

STAGE = "assembly"
# Kinds where one part legitimately enters the other's envelope (shaft in a hub bore).
INSERTION_KINDS = {"bore", "ball_socket", "spline"}
INSERTION_TOLERANCE = 1e-4  # m: a 48-gon shaft prism inside a bore of the same nominal size
CONTACT_TOLERANCE = 1e-5  # m: a seat origin this close to its envelope surface is on it
EXACT_VOLUME_TOL = 5e-11  # m^3 (0.05 mm^3): mesh-boolean slivers of coincident faces stay below this














def check_exact_interference(project: Project, result: StageResult) -> list[dict[str, Any]]:
    """STEP-derived solids at pulled poses, with the unchanged volume tolerance."""
    from r3_onshape.step_geometry import load_geometry
    from r3_onshape.fit import FitChecker
    from r3_model.spec_files import validate_pulled_spec as load_pulled

    pulled = load_pulled(project.root)
    model = load_geometry(project.root)
    checker = FitChecker(project, model, {n: p.material or "" for n, p in pulled.parts.items()}, result)
    report = checker.run()
    project._exact_solids = {name: part.manifold() for name, part in model.parts.items()}
    result.metrics["exact_solids"] = len(model.parts)
    return [{"a": a, "b": b, "volume": volume, "allowed": report.allowed.get((a, b)),
             "center": ((np.maximum(model[a].lo, model[b].lo) + np.minimum(model[a].hi, model[b].hi)) / 2).tolist(),
             "extent": (np.minimum(model[a].hi, model[b].hi) - np.maximum(model[a].lo, model[b].lo)).tolist()}
            for (a, b), volume in report.intersections.items() if volume > EXACT_VOLUME_TOL]
















def run(project: Project) -> StageResult:
    from r3_model.pulled import PullError
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    from r3_model.pulled_view import apply_pulled

    start = time.time()
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    try:
        assembly = apply_pulled(project, load_pulled(project.root))
        result.extend(assembly.diagnostics)
        try:
            interference = check_exact_interference(project, result)
        except PullError as error:
            if error.code != 'cad_analytic_geometry_pending':
                raise
            interference = []
            result.add(Diagnostic.warning('cad_geometry_estimated', str(error)))
            result.metrics['interference_verified'] = False
        # Catalog-only cap dimensions cannot establish a physical cap fit.
        for join in (project.spec.assembly.joins if project.spec.assembly else []):
            for end in (join.a, join.b):
                obj, _, name = end.partition(".")
                comp = project.spec.components.get(obj)
                ctype = project.catalog.components.get(comp.type) if comp else None
                iface = ctype.interfaces.get(name) if ctype else None
                if iface and iface.params.get("cap_for"):
                    result.add(Diagnostic.error("cap_geometry_unverified", f"{obj}: cap clearance/PCB keep-out check needs a cap-to-pulled-face association; catalog dimensions cannot certify it", subjects=[obj]))
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error)))
        result.duration_s = time.time() - start
        return result
    payload = assembly.to_json()
    payload.update(interference=interference, interference_method="pending" if result.metrics.get('interference_verified') is False else "pulled_step",
                   inputs=project.fingerprint(), diagnostics=[d.model_dump(mode="json") for d in result.diagnostics])
    (project.build_dir("model") / "assembly.json").write_text(json.dumps(payload, indent=2, default=float))
    result.artifact("model/assembly.json", "json", "Pulled poses, mate closure residuals and geometry qualification status")
    result.metrics.update(objects=len(assembly.poses), joins=len(assembly.joins), loop_closures=len(assembly.loops))
    result.assume("mechanical", "Onshape solved poses are authoritative; fastened mate frames are checked for closure. " +
                  ("Analytic fit qualification remains pending for this mesh prototype." if result.metrics.get('interference_verified') is False
                   else "Fit checks use pulled STEP faces and solids."))
    result.duration_s = time.time() - start
    return result
