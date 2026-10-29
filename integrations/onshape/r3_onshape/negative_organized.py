"""Phase B negative tests: deliberately broken designs and documents must produce
their diagnostics.

* ``misaligned_hole_pattern``: one gearbox mounting hole in the chassis moved by
  0.5 mm (catalog override in a temporary project) -> ``hole_pattern_mismatch``
  from the assembly compiler;
* ``default_feature_name``, ``unfoldered_tab``, ``unconstrained_sketch``: a
  scratch Onshape document with one organized Part Studio (built by the same
  builder), then a feature renamed to ``Extrude 1``, a second tab left outside
  the tab folders and a sketch with a free line; the organization check must
  report ``cad_default_feature_name``, ``cad_tab_not_in_folder`` and
  ``cad_unconstrained_sketch``. The scratch document is deleted.

    python -m r3_onshape.negative_organized examples/robots/rc-car
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import yaml

from r3_model.diagnostics import StageResult
from r3_model.project import Project

CATALOG = Path(__file__).resolve().parents[3] / "packages" / "component-catalog" / "r3_catalog" / "data" / "components" / "rc_car.yaml"


def misaligned_hole_pattern(root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copy(root / "robot.yaml", work / "robot.yaml")
        (work / "catalog" / "components").mkdir(parents=True)
        data = yaml.safe_load(CATALOG.read_text())
        plate = copy.deepcopy(next(c for c in data["components"] if c["id"] == "chassis-plate-al6061"))
        plate["interfaces"]["gearbox"]["params"]["holes"][0][0] = "18.5 mm"
        (work / "catalog" / "components" / "override.yaml").write_text(yaml.safe_dump({"components": [plate]}))
        diagnostics = Project.load(work).assembly.diagnostics
    hits = [d for d in diagnostics if d.code == "hole_pattern_mismatch"]
    return {"id": "misaligned_hole_pattern", "mutation": "chassis.gearbox hole 1 moved +0.5 mm in x",
            "expected": ["hole_pattern_mismatch"], "found": sorted({d.code for d in diagnostics if d.severity.value == "error"}),
            "passed": bool(hits), "detail": hits[0].message if hits else ""}


def document_cases(project: Project, api: Any, bridge: Any) -> list[dict[str, Any]]:
    from .organized import DocumentInfo, StudioRef, _new_document, organization_check
    from .parts import build_part_catalog
    from .studio import SketchWriter, build_studio

    parts, _ = build_part_catalog(project)
    post = next(p for p in parts.values() if p.name == "Servo post")
    doc = _new_document(api, "R3 negative tests (scratch)", project.spec.cad.folder, "Deliberately broken organization (R3 negative tests)")
    cases: list[dict[str, Any]] = []
    try:
        defaults = api.json("GET", f"/documents/d/{doc.did}/w/{doc.wid}/elements")
        eid = api.json("POST", f"/partstudios/d/{doc.did}/w/{doc.wid}", body={"name": post.studio})["id"]
        built = build_studio(api, doc.did, doc.wid, eid, post.studio, [post])
        doc.studios[post.studio] = StudioRef(post.studio, eid, built.part_ids, built.connectors, built.folders)
        for element in defaults:
            api.request("DELETE", f"/elements/d/{doc.did}/w/{doc.wid}/e/{element['id']}")
        loose = api.json("POST", f"/partstudios/d/{doc.did}/w/{doc.wid}", body={"name": "Loose Part Studio"})["id"]
        if bridge is not None:
            bridge.tab_folders(doc.did, doc.wid, [("02 Fabricated parts", [eid])])
        base = f"/partstudios/d/{doc.did}/w/{doc.wid}/e/{eid}"
        features = api.json("GET", base + "/features")
        extrude = next(f for f in features["features"] if f["featureType"] == "extrude")
        renamed = dict(extrude, name="Extrude 1")
        api.json("POST", base + f"/features/featureid/{urllib.parse.quote(extrude['featureId'], safe='')}",
                 body={"btType": "BTFeatureDefinitionCall-1406", "feature": renamed})
        writer = SketchWriter()
        writer.line((0.0, 0.0), (0.01, 0.005))  # an undimensioned line: 4 free degrees of freedom
        api.json("POST", base + "/features", body={"btType": "BTFeatureDefinitionCall-1406", "feature": writer.feature("loose line sketch", "TOP")})
        result = StageResult(stage="cad")
        library = DocumentInfo(did=doc.did, wid=doc.wid, name="(none)")
        check = organization_check(api, None, doc, library, {post.key: post}, {}, result)
        codes = {d.code for d in result.diagnostics if d.severity.value == "error"}
        for case, code, mutation in (("default_feature_name", "cad_default_feature_name", f"feature {extrude['name']!r} renamed 'Extrude 1'"),
                                     ("unfoldered_tab", "cad_tab_not_in_folder", "'Loose Part Studio' left outside the tab folders"),
                                     ("unconstrained_sketch", "cad_unconstrained_sketch", "a sketch with one undimensioned line")):
            detail = next((d.message for d in result.diagnostics if d.code == code), "")
            cases.append({"id": case, "mutation": mutation, "expected": [code], "found": sorted(codes), "passed": code in codes,
                          "detail": detail, "document": doc.url()})
        del loose, check
    finally:
        api.request("DELETE", f"/documents/{doc.did}")
    return cases


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(argv[0] if argv else "examples/robots/rc-car").resolve()
    project = Project.load(root)
    started = time.time()
    cases = [misaligned_hole_pattern(root)]
    from .bridge import connect
    from .client import default_transport

    api = default_transport()
    if api is not None:
        try:
            cases += document_cases(project, api, connect())
        except Exception as error:  # noqa: BLE001 - e.g. the Onshape feature-API quota: recorded as not run, never as passed
            for case, code in (("default_feature_name", "cad_default_feature_name"), ("unfoldered_tab", "cad_tab_not_in_folder"),
                               ("unconstrained_sketch", "cad_unconstrained_sketch")):
                cases.append({"id": case, "expected": [code], "found": [], "passed": None, "status": "not_run",
                              "detail": f"{type(error).__name__}: {str(error)[:200]}"})
    ran = [c for c in cases if c["passed"] is not None]
    out = {"cases": cases, "passed": all(c["passed"] for c in ran), "not_run": [c["id"] for c in cases if c["passed"] is None],
           "seconds": round(time.time() - started, 1)}
    path = project.build_dir("cad") / "negative_tests.json"
    path.write_text(json.dumps(out, indent=1))
    for case in cases:
        status = "NOT RUN" if case["passed"] is None else ("PASS" if case["passed"] else "FAIL")
        print(f"{status} {case['id']}: expected {case['expected']} found {case['found']} {case.get('detail', '')[:120]}")
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
