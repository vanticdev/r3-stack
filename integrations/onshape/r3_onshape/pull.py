"""Pull pinned native CAD. R3 reads/checks; it never authors shapes or mates."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterator
from urllib.parse import parse_qs, quote, urlsplit

import numpy as np

from r3_model.pulled import PullError, SCHEMA, _frame
from .native import NativeCadTransport


def target(url: str, version_id: str = "") -> dict[str, str]:
    parsed = urlsplit(url)
    match = re.fullmatch(r"/documents/([^/]+)/([wvm])/([^/]+)/e/([^/]+)/?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "cad.onshape.com" or not match:
        raise PullError("cad_pull_target_invalid", "Use an exact Onshape assembly element URL")
    did, mode, revision, eid = match.groups()
    if mode == "m":
        raise PullError("cad_pull_version_required", "Only STL supports microversions. Select/create an Onshape version and pull /v, including STEP.")
    if mode == "v" and version_id and version_id != revision:
        raise PullError("cad_pull_target_invalid", "URL and requested version differ")
    vid = revision if mode == "v" else version_id
    if not vid:
        raise PullError("cad_pull_version_required", "Create/select a version through onshape-native, then supply its /v URL or --version. The documented bridge has no create-version local route; R3 will not call the public API.")
    configuration = parse_qs(parsed.query).get("configuration", [""])[0]
    result = {"document_id": did, "version_id": vid, "element_id": eid, "configuration": configuration}
    result["url"] = f"https://cad.onshape.com/documents/{did}/v/{vid}/e/{eid}" + ("?" + parsed.query if parsed.query else "")
    return result


def _cs(value: dict[str, Any]) -> list[list[float]]:
    origin = value["origin"]
    x, z = np.asarray(value["xAxis"], dtype=float), np.asarray(value["zAxis"], dtype=float)
    matrix = np.eye(4)
    matrix[:3, 0], matrix[:3, 1], matrix[:3, 2], matrix[:3, 3] = x, np.cross(z, x), z, origin
    return _frame(matrix, "native mate connector").tolist()


def _material(value: Any) -> tuple[str, float]:
    if not isinstance(value, dict):
        raise PullError("cad_pull_material_unavailable", "Native part has no material/density; assign it in Onshape and pull again")
    name = value.get("name") or value.get("displayName")
    density = value.get("density")
    properties = value.get("properties", [])
    if isinstance(properties, dict):
        density = properties.get("DENS", density)
    else:
        for item in properties:
            if item.get("name") == "DENS" or item.get("id") == "DENS":
                density = item.get("value")
    if isinstance(density, dict):
        units = density.get("units", "kg/m^3")
        if units not in ("kg/m^3", "kg/m³"):
            raise PullError("cad_pull_material_unavailable", f"Unsupported native density units {units}")
        density = density.get("value")
    if not name or density is None or not np.isfinite(float(density)) or float(density) <= 0:
        raise PullError("cad_pull_material_unavailable", "Native material must supply a name and positive density in kg/m³")
    return str(name), float(density)


def mass_properties(path: Path, density: float) -> dict[str, Any]:
    """Open CASCADE volume integrals in STEP millimetres, converted to SI."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.TransferRoots():
        raise PullError("cad_pull_geometry_invalid", f"Cannot read pulled STEP {path.name}")
    shape = reader.OneShape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise PullError("cad_pull_geometry_invalid", f"Invalid STEP solid {path.name}")
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    volume = props.Mass() * 1e-9
    if not np.isfinite(volume) or volume <= 0:
        raise PullError("cad_pull_geometry_invalid", f"STEP has no positive solid volume: {path.name}")
    inertia = props.MatrixOfInertia()
    return {"mass": volume * density, "com": [v * 1e-3 for v in props.CentreOfMass().Coord()],
            "inertia": [inertia.Value(i, j) * 1e-15 * density for i in range(1, 4) for j in range(1, 4)],
            "volume": volume, "density": density}


def _part_records(snapshot: dict[str, Any]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    records = snapshot["definition"].get("parts", [])
    return {(p["documentId"], p["elementId"], p["partId"], p.get("fullConfiguration") or p.get("configuration") or ""): p for p in records}


def describe(snapshot: dict[str, Any], pin: dict[str, str], selectors: dict[str, list[str]]) -> dict[str, Any]:
    """Normalize complete native assembly data; never infer assembly placement."""
    definition = snapshot["definition"]
    rows = {tuple(r["occurrence_path"]): r for r in snapshot["rows"]
            if r.get("kind") == "occurrence" and str(r.get("instance_type", "")).lower() == "part" and not r.get("suppressed")}
    if not selectors:
        for path, row in rows.items():
            name = row.get("name")
            if not name or name in selectors:
                raise PullError("cad_pull_selection_required", "Assembly part names must be unique or explicitly mapped to occurrence paths")
            selectors[name] = list(path)
    if len({tuple(path) for path in selectors.values()}) != len(selectors):
        raise PullError("cad_pull_selection_invalid", "An occurrence cannot identify multiple local objects")
    by_path = {tuple(path): name for name, path in selectors.items()}
    # Omitting native parts would hide interference and leave the local robot incomplete.
    if set(by_path) != set(rows):
        raise PullError("cad_pull_selection_invalid", "Description and native assembly must contain exactly the same unsuppressed part occurrences")
    records = _part_records(snapshot)
    parts, poses = {}, {}
    for name, occurrence in selectors.items():
        if not isinstance(name, str) or not name:
            raise PullError("cad_pull_selection_invalid", "Part identities must be nonempty strings")
        filename = quote(name, safe="-_.")
        if filename in (".", ".."):
            filename = filename.replace(".", "%2E")
        row = rows[tuple(occurrence)]
        ref = row["reference"]
        key = (ref["documentId"], ref["elementId"], ref["partId"], ref.get("fullConfiguration") or ref.get("configuration") or "")
        record = records.get(key)
        if record is None:
            raise PullError("cad_pull_incomplete", f"{name}: no native part definition")
        vid = ref.get("documentVersion") or (pin["version_id"] if ref["documentId"] == pin["document_id"] else None)
        if not vid:
            raise PullError("cad_pull_version_required", f"{name}: external reference is only a microversion; pin that source to an Onshape version for STEP")
        name_material, density = _material(record.get("material"))
        connectors = {}
        for item in record.get("mateConnectors", []):
            connector_id = item.get("name") or item.get("featureId") or item.get("id")
            if not connector_id or connector_id in connectors:
                raise PullError("cad_pull_incomplete", f"{name}: missing or duplicate native connector identity")
            connectors[connector_id] = _cs(item.get("mateConnectorCS") or item.get("coordinateSystem") or item)
        parts[name] = {"name": name, "stl": f"parts/{filename}.stl", "step": f"parts/{filename}.step", "sha256": {},
                       "material": name_material, "density": density, "mate_connectors": connectors,
                       "purchased": False, "onshape": {"document_id": key[0], "version_id": vid, "element_id": key[1],
                           "part_id": key[2], "occurrence_path": occurrence, "configuration": key[3]}}
        poses[name] = _frame(np.asarray(row["transform"]).reshape(4, 4), f"pose {name}").tolist()
    mates = []
    assembly_poses = {tuple(r["occurrence_path"]): np.asarray(r["transform"]).reshape(4, 4)
                      for r in snapshot["rows"] if r.get("kind") == "occurrence" and r.get("transform") is not None}
    # Native definitions retain full nested paths. Reject unsupported relation
    # types rather than silently discarding a kinematic constraint.
    assemblies = [(definition["rootAssembly"], [])]
    subdefs = {(a.get("documentId"), a.get("elementId"), a.get("documentMicroversion") or a.get("documentVersion")): a
               for a in definition.get("subAssemblies", [])}
    while assemblies:
        assembly, parent = assemblies.pop()
        for instance in assembly.get("instances", []):
            if instance.get("suppressed") or str(instance.get("type", "")).lower() != "assembly":
                continue
            key = (instance.get("documentId"), instance.get("elementId"), instance.get("documentMicroversion") or instance.get("documentVersion"))
            if key not in subdefs or len(parent) > 100:
                raise PullError("cad_pull_incomplete", "Missing or cyclic native subassembly")
            assemblies.append((subdefs[key], [*parent, instance["id"]]))
        for feature in assembly.get("features", []):
            if feature.get("suppressed"):
                continue
            data = feature.get("featureData") or {}
            kind = str(data.get("mateType", "")).lower()
            if not kind:
                if str(feature.get("featureType", "")).lower() in ("mateconnector", "mate_connector"):
                    continue
                raise PullError("cad_pull_constraint_unsupported", f"Native feature {feature.get('id')} has no readable mate definition; cannot reproduce its constraint")
            ends = []
            entities = data.get("matedEntities") or []
            if len(entities) != 2:
                raise PullError("cad_pull_constraint_unsupported", f"Mate {feature['id']} must have two resolved entities")
            mate_id = "/".join([*parent, feature["id"]])
            world_frames = []
            for side, entity in zip(("a", "b"), entities):
                occurrence = entity.get("matedOccurrence") or entity.get("occurrence") or []
                path = occurrence.get("path", []) if isinstance(occurrence, dict) else occurrence
                full = tuple([*parent, *path])
                if full not in by_path:
                    raise PullError("cad_pull_incomplete", f"Mate {mate_id}: unresolved occurrence {full}")
                part = by_path[full]
                # matedCS is solved in assembly coordinates; pull its exact frame
                # into the part frame, rather than deriving it from catalog interfaces.
                world = (assembly_poses[tuple(parent)] if parent else np.eye(4)) @ np.asarray(_cs(entity["matedCS"]))
                local = np.linalg.inv(poses[part]) @ world
                connector_id = entity.get("mateConnectorId") or f"{mate_id}:{side}"
                previous = parts[part]["mate_connectors"].get(connector_id)
                if previous is not None and not np.allclose(previous, local, atol=1e-7):
                    raise PullError("cad_pull_connector_mismatch", f"{part}.{connector_id}: native connector and mate frames differ")
                parts[part]["mate_connectors"][connector_id] = local.tolist()
                ends.append([part, connector_id])
                world_frames.append(world)
            limits = data.get("mateLimits") or {}
            fields = ("limitZMin", "limitZMax") if kind == "slider" else ("limitAxialZMin", "limitAxialZMax")
            pair = [limits.get(key) for key in fields]
            if limits and any(value is None for value in pair):
                raise PullError("cad_pull_constraint_unsupported", f"Mate {mate_id}: limits are not representable by one axis/range")
            mates.append({"id": mate_id, "kind": kind, "a": ends[0], "b": ends[1],
                          "axis": world_frames[0][:3, 2].tolist(), "limits": pair if limits else None,
                          "native": feature})
    return {"schema": SCHEMA, **pin, "parts": parts, "poses": poses, "mates": mates,
            "microversion": snapshot.get("microversion", "")}


@contextmanager
def pull_lock(root: Path) -> Iterator[None]:
    folder = root / "cad"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".pull.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PullError("cad_pull_busy", "A CAD pull is already active") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def download(transport: Any, manifest: dict[str, Any], directory: Path, capture_url: str) -> None:
    import trimesh

    (directory / "parts").mkdir()
    for name, part in manifest["parts"].items():
        ref = part["onshape"]
        for kind in ("stl", "step"):
            path = directory / part[kind]
            path.write_bytes(getattr(transport, kind)(ref["document_id"], ref["version_id"], ref["element_id"], ref["part_id"], ref["configuration"]))
            part["sha256"][kind] = hashlib.sha256(path.read_bytes()).hexdigest()
        mesh = trimesh.load(directory / part["stl"], force="mesh", process=True)
        if not len(mesh.faces) or not mesh.is_watertight or not np.isfinite(mesh.vertices).all() or mesh.volume <= 0:
            raise PullError("cad_pull_geometry_invalid", f"{name}: STL is not a finite, closed positive-volume solid")
        part.update(mass_properties(directory / part["step"], part["density"]))
    image, evidence = transport.capture(capture_url)
    revision = evidence.get("microversion")
    if not revision or revision != manifest["microversion"]:
        raise PullError("cad_pull_view_stale", "Viewport revision differs from the pinned native assembly; open the pinned view before pull")
    (directory / "viewport.png").write_bytes(image)
    capture = {key: value for key, value in evidence.items() if key not in ("path", "artifact")}
    manifest["images"] = [{"path": "viewport.png", "sha256": hashlib.sha256(image).hexdigest(), "capture": capture}]
    (directory / "pull.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    for path in directory.rglob("*"):
        if path.is_file():
            path.chmod(0o444)


def pull(project: Any, url: str = "", version_id: str = "", transport: Any = None) -> dict[str, Any]:
    """Explicit refresh. Validation stages never contact or modify native CAD."""
    try:
        with pull_lock(project.root):
            return _pull(project, url, version_id, transport)
    except (ValueError, FileNotFoundError, ImportError, RuntimeError) as error:
        fallback = "cad_native_unavailable" if isinstance(error, FileNotFoundError) else "cad_pull_failed"
        return {"ok": False, "code": getattr(error, "code", fallback), "message": str(error)}


def _pull(project: Any, url: str, version_id: str, transport: Any) -> dict[str, Any]:
    cad = project.spec.cad.model_dump()
    url = url or cad.get("source", "") or cad.get("url", "")
    pin = target(url, version_id or cad.get("version_id") or "")
    transport = transport or NativeCadTransport()
    # Spec-schema integration refreshes these selectors from the split part refs.
    selectors = {}
    for name, descriptor in project.spec.components.items():
        ref = getattr(descriptor, "onshape", None)
        path = getattr(ref, "occurrence_path", None)
        if path:
            selectors[name] = list(path)
    manifest = describe(transport.assembly(pin["url"]), pin, selectors)
    for name, part in manifest["parts"].items():
        component = project.spec.components.get(name)
        selection = getattr(component, "onshape", None)
        if selection and selection.configuration and selection.configuration != part["onshape"]["configuration"]:
            raise PullError("cad_pull_selection_invalid", f"{name}: configured native occurrence differs from the requested selector; configure the assembly in Onshape")
        catalog = project.catalog.components.get(component.type) if component else None
        part["purchased"] = bool(catalog and not catalog.mechanical.fabricated)
    folder = project.root / "cad"
    staging = Path(tempfile.mkdtemp(prefix=".pull-", dir=folder))
    backup = folder / ".pulled-previous"
    destination = folder / "pulled"
    try:
        download(transport, manifest, staging, pin["url"])
        checksum = hashlib.sha256((staging / "pull.json").read_bytes()).hexdigest()
        from r3_model.description import refresh_pulled
        previous_lock = (project.root / "r3.lock.yaml").read_bytes() if (project.root / "r3.lock.yaml").exists() else None
        previous_pin = project.lock.get("cad_pull")
        if backup.exists():
            raise PullError("cad_pull_recovery_required", "Previous pull backup remains at cad/.pulled-previous; inspect it before replacing CAD")
        if destination.exists():
            destination.rename(backup)
        try:
            staging.rename(destination)
            project.write_lock("cad_pull", {"document_id": pin["document_id"], "version_id": pin["version_id"],
                               "manifest_sha256": checksum, "revision": pin["version_id"]})
            refresh_pulled(project.root, manifest)
        except Exception:
            # Restore the last complete local snapshot on description/schema failures.
            # Native CAD is never touched by rollback.
            if destination.exists():
                shutil.rmtree(destination)
            if backup.exists():
                backup.rename(destination)
            lock_path = project.root / "r3.lock.yaml"
            if previous_lock is not None:
                lock_path.write_bytes(previous_lock)
            else:
                lock_path.unlink(missing_ok=True)
            if previous_pin is None:
                project.lock.pop("cad_pull", None)
            else:
                project.lock["cad_pull"] = previous_pin
            # refresh_pulled owns its atomic spec transaction. Rewriting an
            # earlier snapshot here would discard concurrent authored edits.
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"ok": True, "document_id": pin["document_id"], "version_id": pin["version_id"],
            "manifest_sha256": checksum, "parts": len(manifest["parts"]), "mates": len(manifest["mates"]),
            "manifest": "cad/pulled/pull.json"}
