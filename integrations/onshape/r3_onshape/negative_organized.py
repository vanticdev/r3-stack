"""Negative assembly and document-organization probes derived from the current project."""

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

def misaligned_hole_pattern(root: Path) -> dict[str, Any]:
    """Move a hole on one side of an actual declared multi-hole join."""
    project = Project.load(root)
    selected = None
    if project.spec.assembly:
        for join in project.spec.assembly.joins:
            cid, _, iface = join.a.partition(".")
            component = project.spec.components[cid]
            entry = project.catalog.component(component.type)
            interface = entry.interfaces.get(iface)
            if interface and interface.kind == "hole_pattern" and len(interface.params.get("holes", [])) > 1:
                selected = (entry, iface)
                break
    if selected is None:
        return {"id": "misaligned_hole_pattern", "skipped": "no declared multi-hole join", "passed": True}
    entry, iface = selected
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copy(root / "robot.yaml", work / "robot.yaml")
        if (root / "catalog").is_dir():
            shutil.copytree(root / "catalog", work / "catalog")
        directory = work / "catalog/components"
        directory.mkdir(parents=True, exist_ok=True)
        data = entry.model_dump(mode="json")
        from r3_model.units import to_si
        data["interfaces"][iface]["params"]["holes"][0][0] = to_si(interface.params["holes"][0][0]) + .0005
        (directory / "zz_negative_override.yaml").write_text(yaml.safe_dump({"components": [data]}))
        diagnostics = Project.load(work).assembly.diagnostics
    hits = [d for d in diagnostics if d.code == "hole_pattern_mismatch"]
    return {"id": "misaligned_hole_pattern", "mutation": f"{entry.id}.{iface} hole shifted +0.5 mm",
            "expected": ["hole_pattern_mismatch"], "found": sorted({d.code for d in diagnostics if d.severity.value == "error"}),
            "passed": bool(hits), "detail": hits[0].message if hits else ""}


def document_cases(project: Project, api: Any, bridge: Any) -> list[dict[str, Any]]:
    from .organized import DocumentInfo, StudioRef, _new_document, organization_check
    from .parts import build_part_catalog
    from .studio import SketchWriter, build_studio
    from .journal import RequestJournal, save_json
    from .organized import input_digest
    from .quota import raise_if_quota

    parts, _ = build_part_catalog(project)
    post = next(p for p in parts.values() if p.name == "Servo post")
    state_path = project.build_dir("cad") / "negative_build_state.json"
    digest = input_digest(project)
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["digest"] != digest:
            raise ValueError("negative-test inputs changed; preserve the scratch document and clear negative_build_state explicitly")
        doc = DocumentInfo(did=state["did"], wid=state["wid"], name=state["name"])
    else:
        doc = _new_document(api, "R3 negative tests (scratch)", project.spec.cad.folder, "Deliberately broken organization")
        save_json(state_path, {"digest": digest, "did": doc.did, "wid": doc.wid, "name": doc.name})
    live_api = api
    api = RequestJournal(api, project.build_dir("cad") / f"negative-{doc.did}.journal.json", digest)
    quota_blocked = False
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
    except Exception as error:
        # Coerce quota errors from the editor adapter as well as the REST transport.
        if getattr(error, "status", None) == 429:
            quota_blocked = True
            raise
        try:
            raise_if_quota(error)
        except Exception:
            quota_blocked = True
            raise
        raise
    finally:
        if not quota_blocked:
            live_api.request("DELETE", f"/documents/{doc.did}")
            state_path.unlink(missing_ok=True)
    return cases


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.project).resolve()
    project = Project.load(root)
    if args.dry_run:
        from .dry_run import negative_plan
        print(json.dumps(negative_plan(project), indent=2))
        return 0
    started = time.time()
    cases = [misaligned_hole_pattern(root)]
    from .bridge import connect
    from .client import default_transport

    quota_result = StageResult(stage="cad")
    try:
        api = default_transport()
        if api is None:
            raise RuntimeError("no Onshape transport configured")
        cases += document_cases(project, api, connect())
    except Exception as error:  # noqa: BLE001 - e.g. the Onshape feature-API quota: recorded as not run, never as passed
        if getattr(error, "status", None) == 429:
            from .quota import quota_diagnostic
            quota_diagnostic(error, quota_result)
        for case, code in (("default_feature_name", "cad_default_feature_name"), ("unfoldered_tab", "cad_tab_not_in_folder"),
                           ("unconstrained_sketch", "cad_unconstrained_sketch")):
            cases.append({"id": case, "expected": [code], "found": [], "passed": None, "status": "not_run",
                          "detail": f"{type(error).__name__}: {str(error)[:200]}"})
    out = {"cases": cases, "passed": all(c["passed"] is True for c in cases), "not_run": [c["id"] for c in cases if c["passed"] is None],
           "diagnostics": [d.model_dump(mode="json") for d in quota_result.diagnostics],
           "seconds": round(time.time() - started, 1)}
    path = project.build_dir("cad") / "negative_tests.json"
    path.write_text(json.dumps(out, indent=1))
    for case in cases:
        status = "NOT RUN" if case["passed"] is None else ("PASS" if case["passed"] else "FAIL")
        print(f"{status} {case['id']}: expected {case['expected']} found {case['found']} {case.get('detail', '')[:120]}")
    return 0 if out["passed"] and not out["not_run"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
