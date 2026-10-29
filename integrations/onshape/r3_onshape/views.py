"""Headless CAD images from Onshape's REST shaded-view endpoints.

Onshape renders the model server-side (``getPartStudioShadedViews`` /
``getAssemblyShadedViews``) and returns base64 images, so screenshots need no
browser and no local GL. Works with every transport, including API keys on a
server. Read-only GETs; nothing here touches features (see :mod:`.policy`).
"""

from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .client import Transport


def _iso_matrix() -> str:
    """Row-major 3x4 view matrix of Onshape's isometric view (viewer at +x, -y, +z).

    Rows are the view axes in model coordinates: right = (x+y)/sqrt(2),
    toward-viewer d = (x-y+z)/sqrt(3), up = d x right. Onshape's own example for
    the isometric view, ``0.612,0.612,0,0,-0.354,0.354,0.707,0,0.707,-0.707,0.707,0``,
    is the same rotation with approximate, unnormalized rows.
    """
    r2, r3, r6 = math.sqrt(2.0), math.sqrt(3.0), math.sqrt(6.0)
    rows = ((1 / r2, 1 / r2, 0.0), (-1 / r6, 1 / r6, 2 / r6), (1 / r3, -1 / r3, 1 / r3))
    return ",".join(f"{v:.6f}" for row in rows for v in (*row, 0.0))


# Named views are Onshape's own; "iso" is the explicit matrix above.
VIEWS: dict[str, str] = {"iso": _iso_matrix(), "front": "front", "top": "top", "right": "right"}


@dataclass(frozen=True)
class ElementRef:
    did: str
    wvm: str  # "w", "v" or "m"
    wvmid: str
    eid: str
    kind: str = "partstudio"  # or "assembly"

    @property
    def path(self) -> str:
        collection = "assemblies" if self.kind == "assembly" else "partstudios"
        return f"/{collection}/d/{self.did}/{self.wvm}/{self.wvmid}/e/{self.eid}/shadedviews"


def _images(payload: Any) -> list[bytes]:
    """Decode ``BTShadedViewsInfo.images`` (base64 strings, possibly nested one level)."""
    raw = payload.get("images", []) if isinstance(payload, dict) else []
    flat: list[str] = []
    for item in raw:
        flat.extend(item if isinstance(item, list) else [item])
    return [base64.b64decode(item) for item in flat if item]


def _to_png(data: bytes) -> bytes:
    if data.startswith(b"\x89PNG"):
        return data
    from PIL import Image

    out = io.BytesIO()
    Image.open(io.BytesIO(data)).save(out, format="PNG")
    return out.getvalue()


def shaded_view(api: Transport, element: ElementRef, view: str, width: int = 1280, height: int = 960,
                edges: bool = True) -> bytes:
    """One server-rendered PNG of ``element`` from ``view`` (a key of :data:`VIEWS`,
    an Onshape named view, or a 12-number view matrix)."""
    query: dict[str, Any] = {
        "viewMatrix": VIEWS.get(view, view),
        "outputWidth": width,
        "outputHeight": height,
        "pixelSize": 0,  # 0 = fit the model to the image
        "edges": "show" if edges else "hide",
        "useAntiAliasing": True,
    }
    if element.kind == "partstudio":
        query["showAllParts"] = True
    payload = api.json("GET", element.path, query=query)
    images = _images(payload)
    if not images:
        raise ValueError(f"shaded view {view!r} of {element.kind} {element.eid} returned no image")
    return _to_png(images[0])


def render_views(api: Transport, element: ElementRef, out_dir: Path, views: Iterable[str] = ("iso", "front", "top", "right"),
                 prefix: str = "", width: int = 1280, height: int = 960) -> list[Path]:
    """Write ``<out_dir>/<prefix><view>.png`` for each view and return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for view in views:
        path = out_dir / f"{prefix}{view}.png"
        path.write_bytes(shaded_view(api, element, view, width, height))
        paths.append(path)
    return paths


def document_elements(api: Transport, did: str, wid: str) -> list[dict[str, Any]]:
    return api.json("GET", f"/documents/d/{did}/w/{wid}/elements") or []


def _assembly_has_instances(api: Transport, did: str, wid: str, eid: str) -> bool:
    definition = api.json("GET", f"/assemblies/d/{did}/w/{wid}/e/{eid}", query={"includeMateFeatures": False}) or {}
    return bool((definition.get("rootAssembly") or {}).get("instances"))


def render_document_views(api: Transport, did: str, wid: str, part_studio: str, out_dir: Path) -> list[Path]:
    """Views of the robot's Part Studio, plus ``assembly_<view>.png`` for the first
    non-empty Assembly in the document (mated, as-built placement). New documents
    contain an empty default Assembly, which is skipped."""
    paths = render_views(api, ElementRef(did, "w", wid, part_studio), out_dir)
    for element in document_elements(api, did, wid):
        if element.get("elementType") == "ASSEMBLY" and _assembly_has_instances(api, did, wid, element["id"]):
            paths += render_views(api, ElementRef(did, "w", wid, element["id"], kind="assembly"), out_dir, prefix="assembly_")
            break
    return paths
