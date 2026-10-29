"""Direct-CAD policy: R3 never authors or evaluates FeatureScript.

CAD is built only from Onshape's standard, user-editable features (sketches,
extrudes, revolves, holes, fillets, patterns, mate connectors, assemblies and
mates), exactly as a person would build it in the Onshape UI. Every request
leaving R3 passes :func:`check_request`; forbidden requests raise
:class:`FeatureScriptForbidden` before anything is sent.

Forbidden:
* Feature Studio elements (create, read, update) and their feature specs.
* FeatureScript evaluation endpoints (``.../featurescript``).
* Custom features: any feature with a non-empty ``namespace`` (that is how a
  Feature Studio feature is referenced) or a ``featureType`` outside the
  standard allowlist below.
* Creating a ``featurestudio`` element through the document element APIs.
"""

from __future__ import annotations

import json
import re
from typing import Any


class FeatureScriptForbidden(PermissionError):
    pass


# Standard Part Studio / Assembly feature types R3 may create (Onshape built-ins).
STANDARD_FEATURE_TYPES = frozenset({
    # sketching and reference geometry
    "newSketch", "cPlane", "mateConnector", "helix", "point", "fitSpline",
    # solid modeling
    "extrude", "revolve", "sweep", "loft", "thicken", "rib", "shell", "draft",
    "fillet", "chamfer", "hole", "booleanBodies", "splitPart", "transform",
    "linearPattern", "circularPattern", "mirror", "deleteBodies", "moveFace",
    "replaceFace", "deleteFace", "offsetSurface", "modifyFillet", "wrap",
    "partStudioNumber", "assignVariable",
    # assembly features
    "mate", "fastenMate", "revoluteMate", "sliderMate", "cylindricalMate", "planarMate",
    "ballMate", "pinSlotMate", "parallelMate", "tangentMate", "mateGroup", "gearRelation",
    "rackRelation", "screwRelation", "linearRelation", "replicate",
})

_FORBIDDEN_PATHS = (
    re.compile(r"/featurestudios(/|$)"),
    re.compile(r"/featurescript(\?|$)"),
    re.compile(r"/featurespecs(\?|$)"),
)


def _walk_features(node: Any):
    if isinstance(node, dict):
        bt = str(node.get("btType", ""))
        if bt.startswith(("BTMFeature", "BTMSketch", "BTMMate", "BTMAssemblyFeature")) or "featureType" in node:
            yield node
        for value in node.values():
            yield from _walk_features(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_features(item)


def check_request(method: str, path: str, body: Any = None) -> None:
    """Raise FeatureScriptForbidden if the request would author or run FeatureScript."""
    for pattern in _FORBIDDEN_PATHS:
        if pattern.search(path):
            raise FeatureScriptForbidden(f"{method} {path}: Feature Studios and FeatureScript evaluation are disabled (direct-CAD policy)")
    if body is None:
        return
    text = json.dumps(body) if not isinstance(body, str) else body
    if "BTFeatureScriptEvalCall" in text or "FeatureScript " in text:
        raise FeatureScriptForbidden(f"{method} {path}: request body contains FeatureScript")
    if isinstance(body, dict) and str(body.get("elementType", "")).upper() == "FEATURESTUDIO":
        raise FeatureScriptForbidden(f"{method} {path}: creating Feature Studio elements is disabled")
    for feature in _walk_features(body):
        namespace = feature.get("namespace")
        if namespace:
            raise FeatureScriptForbidden(f"{method} {path}: custom feature namespace {namespace!r} is not allowed")
        feature_type = feature.get("featureType")
        if feature_type and feature_type not in STANDARD_FEATURE_TYPES:
            raise FeatureScriptForbidden(f"{method} {path}: feature type {feature_type!r} is not a standard Onshape feature")
