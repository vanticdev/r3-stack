"""CAD stage validates the immutable pulled copy; never generates native CAD."""
from __future__ import annotations

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project
from r3_model.pulled import PullError, load_pulled


def run(project: Project) -> StageResult:
    result = StageResult(stage="cad", inputs=project.fingerprint())
    try:
        model = load_pulled(project.root)
        # The split-spec loader owns the exact comparison with derived fields.
        from r3_model.description import check_pulled
        check_pulled(project.root, model)
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error), hint="Fix the model in Onshape, then run r3 cad pull. Never edit pulled files or derived values."))
        return result
    missing_mass = [name for name, part in model.parts.items()
                    if part.mass is None or part.com is None or part.inertia is None or part.material is None]
    if missing_mass:
        result.add(Diagnostic.error("cad_pull_mass_unavailable", "Pulled parts lack geometry/material mass properties",
                                    subjects=missing_mass, data={"parts": len(missing_mass)}))
        return result
    result.metrics.update(parts=len(model.parts), assembly_mates=len(model.mates),
                          total_mass_kg=sum(part.mass or 0 for part in model.parts.values()),
                          onshape_document=f"https://cad.onshape.com/documents/{model.document_id}/v/{model.version_id}")
    limit = project.spec.requirements.max_mass
    if limit is not None and result.metrics["total_mass_kg"] > limit:
        result.add(Diagnostic.error("mass_budget_exceeded", "Pulled CAD mass exceeds the robot mass budget",
                                   subjects=["robot"], data={"mass": result.metrics["total_mass_kg"], "limit": limit}))
    return result
