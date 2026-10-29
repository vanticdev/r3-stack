"""CAD for sourced parts, the way a person does it in Onshape.

For every pinned sourcing slot (``r3.lock.yaml`` ``sourcing.<slot>``):

* **vendor model:** download the vendor's STEP (zip archives are unpacked) and
  import it into the parts library document with Onshape's standard import
  (``POST /translations/d/{did}/w/{wid}``, multipart, the same translation the
  *Import* dialog runs; poll ``GET /translations/{id}``). Then measure the
  imported part (``boundingboxes``): its extents must match the catalog
  envelope (any axis order within 1.5 mm), which also locates the R3 component
  frame (bottom centre of the envelope) in the STEP frame. Finally add standard
  ``mateConnector`` features at the part's mounting features and connectors,
  placed on the Part Studio origin and offset into position (no FeatureScript).
* **no model:** the part is a parametric **proxy**: the direct-CAD builder
  already models the catalog envelope with standard features; the slot is
  reported as ``proxy``.

The parts library is ``R3_ONSHAPE_PARTS_LIBRARY`` (a document URL) or the
project's CAD document from ``r3.lock.yaml``. Headless only through the
official API-key transport; without it every slot is skipped with
``sourced_cad_transport_missing``.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from . import features as fx
from .client import API, ApiKeyTransport, Transport, _url, default_transport
from .policy import check_request

ORIGIN_VERTEX = "IB"  # deterministic id of the Part Studio origin point
FRAME_TOLERANCE = 1.5e-3
_DOC_URL = re.compile(r"/documents/([0-9a-f]{24})/w/([0-9a-f]{24})")


def library_target(project: Any) -> tuple[str, str] | None:
    import os

    url = os.environ.get("R3_ONSHAPE_PARTS_LIBRARY", "")
    match = _DOC_URL.search(url)
    if match:
        return match.group(1), match.group(2)
    cad = project.lock.get("cad") or {}
    if cad.get("document_id") and cad.get("workspace_id"):
        return str(cad["document_id"]), str(cad["workspace_id"])
    return None


def step_from_download(path: Path) -> Path:
    """The STEP file itself, unpacking a zip archive (largest .step/.stp member)."""
    if not zipfile.is_zipfile(path):
        return path
    with zipfile.ZipFile(path) as archive:
        members = [m for m in archive.infolist() if m.filename.lower().endswith((".step", ".stp")) and ".." not in m.filename]
        if not members:
            raise ValueError(f"{path.name} contains no .step/.stp file")
        member = max(members, key=lambda m: m.file_size)
        target = path.parent / Path(member.filename).name
        target.write_bytes(archive.read(member))
        return target


def multipart(fields: dict[str, str], file_field: str, file_name: str, data: bytes) -> tuple[bytes, str]:
    boundary = f"r3{secrets.token_hex(12)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
                  f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def upload(transport: Transport, path: str, fields: dict[str, str], file_name: str, data: bytes) -> dict[str, Any]:
    """Multipart POST through a signed HTTP transport (``upload_multipart`` on test doubles)."""
    check_request("POST", path, fields)
    if hasattr(transport, "upload_multipart"):
        return transport.upload_multipart(path, fields, file_name, data)  # type: ignore[attr-defined]
    if not hasattr(transport, "_auth_headers"):
        raise RuntimeError(f"{transport.name} cannot upload files; use the API-key transport")
    body, content_type = multipart(fields, "file", file_name, data)
    url = _url(transport.base, path, None)  # type: ignore[attr-defined]
    headers = {"Accept": "application/json", "Content-Type": content_type,
               **transport._auth_headers("POST", url, content_type)}  # type: ignore[attr-defined]
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.loads(response.read() or b"{}")


def translate_step(transport: Transport, did: str, wid: str, step: Path, poll_s: float = 2.0, timeout_s: float = 900.0) -> dict[str, Any]:
    fields = {"formatName": "STEP", "translate": "true", "flattenAssemblies": "true", "createComposite": "false",
              "yAxisIsUp": "false", "storeInDocument": "false", "encodedFilename": step.name, "allowFaultyParts": "true"}
    request = upload(transport, f"{API}/translations/d/{did}/w/{wid}", fields, step.name, step.read_bytes())
    started = time.time()
    while request.get("requestState") not in ("DONE", "FAILED"):
        if time.time() - started > timeout_s:
            raise TimeoutError(f"translation {request.get('id')} not done after {timeout_s:.0f} s")
        time.sleep(poll_s)
        request = transport.json("GET", f"/translations/{request['id']}")
    if request["requestState"] == "FAILED":
        raise RuntimeError(f"Onshape could not import {step.name}: {request.get('failureReason', 'unknown reason')}")
    return request


def frame_offset(box: dict[str, Any], envelope: list[float]) -> tuple[np.ndarray | None, str]:
    """STEP-frame position of the R3 component frame (bottom centre of the envelope), if the extents match."""
    low = np.array([box["lowX"], box["lowY"], box["lowZ"]], dtype=float)
    high = np.array([box["highX"], box["highY"], box["highZ"]], dtype=float)
    extents = high - low
    expected = np.array(envelope[:3] if len(envelope) >= 3 else [envelope[0], envelope[0], envelope[1]], dtype=float)
    if np.allclose(extents, expected, atol=FRAME_TOLERANCE):
        centre = (low + high) / 2
        return np.array([centre[0], centre[1], low[2]]), "extents match the catalog envelope (same axes)"
    if np.allclose(np.sort(extents), np.sort(expected), atol=FRAME_TOLERANCE):
        return None, f"extents {np.round(extents * 1e3, 1).tolist()} mm match the envelope only in another axis order"
    return None, f"extents {np.round(extents * 1e3, 1).tolist()} mm differ from the envelope {np.round(expected * 1e3, 1).tolist()} mm"


def mate_points(entry: dict[str, Any]) -> list[tuple[str, list[float], list[float]]]:
    """(name, position, z axis) in the component frame: mounting holes/bores and connectors."""
    points = []
    for feature in entry.get("mechanical", {}).get("features", []):
        if feature["kind"] in ("clearance_hole", "tapped_hole", "bore", "d_bore", "socket", "shaft", "face"):
            points.append((feature["id"], list(feature.get("position", [0, 0, 0])), list(feature.get("axis", [0, 0, -1]))))
    for name, connector in entry.get("connectors", {}).items():
        points.append((name, list(connector.get("position", [0, 0, 0])), list(connector.get("direction", [0, 0, 1]))))
    return points


def part_studio_mate_connector(name: str, offset: np.ndarray, axis: list[float]) -> dict[str, Any] | None:
    """A standard mate connector on the Part Studio origin, translated to ``offset``; z along +-z only."""
    direction = np.asarray(axis, dtype=float)
    if abs(abs(direction[2]) - 1.0) > 1e-6:
        return None
    params = [fx.enum("originType", "OriginCreationType", "ON_ENTITY"), fx.qlist("originQuery", [fx.det(ORIGIN_VERTEX)]),
              fx.boolean("transform", True), fx.qty("translationX", fx.mm(offset[0])), fx.qty("translationY", fx.mm(offset[1])),
              fx.qty("translationZ", fx.mm(offset[2]))]
    if direction[2] < 0:
        params.append(fx.boolean("flipPrimary", True))
    return {"btType": "BTMMateConnector-66", "featureType": "mateConnector", "name": f"R3 {name}", "namespace": "", "parameters": params}


def import_sourced(project: Any, slots: list[str] | None = None, transport: Transport | None = None, http: Any = None) -> dict[str, Any]:
    from r3_catalog.sourcing import HttpClient

    pins = project.lock.get("sourcing") or {}
    selected = {slot: pin for slot, pin in pins.items() if slots is None or slot in slots or set(slots) & set(pin.get("components", []))}
    results: list[dict[str, Any]] = []
    transport = transport if transport is not None else default_transport()
    headless = transport is not None and (isinstance(transport, ApiKeyTransport) or hasattr(transport, "upload_multipart"))
    target = library_target(project)
    http = http or HttpClient()
    for slot, pin in sorted(selected.items()):
        path = project.root / str(pin.get("catalog_file", ""))
        if not path.is_file():
            results.append({"slot": slot, "status": "failed", "code": "sourcing_catalog_missing", "message": f"{path} missing"})
            continue
        document = yaml.safe_load(path.read_text())
        entry, meta = document["components"][0], document.get("sourced") or {}
        step = next((m for m in meta.get("models", []) if m.get("format") in ("step", "step_zip")), None)
        if step is None:
            results.append({"slot": slot, "status": "proxy", "code": "sourced_cad_proxy",
                            "message": "no vendor model: the direct-CAD builder models the catalog envelope as a parametric proxy"})
            continue
        if not headless:
            results.append({"slot": slot, "status": "skipped", "code": "sourced_cad_transport_missing", "model": step["url"],
                            "message": "vendor STEP import needs the Onshape API-key transport (ONSHAPE_ACCESS_KEY/SECRET_KEY)"})
            continue
        if target is None:
            results.append({"slot": slot, "status": "skipped", "code": "sourced_cad_library_missing", "model": step["url"],
                            "message": "set R3_ONSHAPE_PARTS_LIBRARY to an Onshape document URL (or sync the project's CAD first)"})
            continue
        results.append(_import_one(project, slot, entry, step, transport, target, http))
    return {"ok": not any(r["status"] == "failed" for r in results), "slots": results,
            "library": None if target is None else {"document_id": target[0], "workspace_id": target[1]}}


def _import_one(project: Any, slot: str, entry: dict[str, Any], step: dict[str, Any], transport: Transport,
                target: tuple[str, str], http: Any) -> dict[str, Any]:
    did, wid = target
    folder = project.root / "build" / "sourcing" / slot / "model"
    try:
        download = http.download(step["url"], folder / Path(step["url"].split("?")[0]).name.replace("%20", "_"))
        step_file = step_from_download(download)
        translation = translate_step(transport, did, wid, step_file)
    except Exception as error:  # noqa: BLE001 - network, archive and Onshape failures are per-slot results
        return {"slot": slot, "status": "failed", "code": "sourced_cad_import_failed", "message": f"{type(error).__name__}: {error}"}
    elements = translation.get("resultElementIds") or []
    out: dict[str, Any] = {"slot": slot, "status": "imported", "model": step["url"], "step": str(step_file.relative_to(project.root)),
                           "translation_id": translation.get("id"), "element_ids": elements, "mate_connectors": []}
    if not elements:
        return {**out, "code": "sourced_cad_no_part_studio", "message": "translation produced no Part Studio"}
    eid = elements[0]
    box = transport.json("GET", f"/partstudios/d/{did}/w/{wid}/e/{eid}/boundingboxes")
    offset, verdict = frame_offset(box, list(entry["mechanical"]["size"]))
    out["frame_check"] = verdict
    if offset is None:
        return {**out, "code": "sourced_cad_frame_unverified",
                "message": f"{verdict}: mate connectors not added (orient the model, then re-run)"}
    for name, position, axis in mate_points(entry):
        feature = part_studio_mate_connector(name, offset + np.asarray(position, dtype=float), axis)
        if feature is None:
            out["mate_connectors"].append({"name": name, "status": "skipped", "reason": "axis not along z"})
            continue
        path = f"/partstudios/d/{did}/w/{wid}/e/{eid}/features"
        check_request("POST", path, {"feature": feature})
        transport.json("POST", path, body={"feature": feature})
        out["mate_connectors"].append({"name": name, "status": "added"})
    out["code"] = "sourced_cad_imported"
    out["message"] = f"imported into element {eid}; {sum(1 for m in out['mate_connectors'] if m['status'] == 'added')} mate connectors"
    return out
