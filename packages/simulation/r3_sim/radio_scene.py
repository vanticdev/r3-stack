"""Radio scene description for the RF propagation solver (Sionna RT).

Pure geometry and materials, independent of the solver, built from the spec:

* ground: an ITU-R P.2040 material (``medium_dry_ground`` by default, or
  ``radio_environment.ground_material``); a custom permittivity/conductivity
  pair only when *both* ``ground_permittivity`` and ``ground_conductivity`` are
  given explicitly;
* static obstacles: ``environment.obstacles`` boxes (center, size, material);
* the robot body: every component of the robot except the receiver and the
  antenna (its own radiator cannot shadow itself), as its CAD
  verified pulled STL mesh in the robot frame. Parts are merged per radio
  material so the solver can move the body with the robot at each trace;
* the antennas: transmitter (pilot handset) position, power, pattern and
  polarization; the robot antenna offset in the robot frame.

Material classes for the body are estimates: metals and PCB-dense parts
(computer, HAT, ESC, battery pouch foils, motor/servo cans) are perfect
conductors in practice at 2.4 GHz and use the ITU ``metal`` material; plastics
and tyres are a low-loss dielectric (eps_r = 3.0, tan(delta) ~ 0.01 ->
sigma = 2 pi f eps0 eps_r tan(delta) ~ 0.004 S/m).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.units import to_si

#: ITU-R P.2040 material names understood by Sionna RT (``ITURadioMaterial``).
ITU_MATERIALS = ("concrete", "brick", "plasterboard", "wood", "glass", "ceiling_board", "chipboard", "plywood", "marble",
                 "floorboard", "metal", "very_dry_ground", "medium_dry_ground", "wet_ground")
#: Terrain keyword -> ITU ground material (estimate; grass over soil is "medium dry ground").
TERRAIN_GROUND = {"flat-grass": "medium_dry_ground", "grass": "medium_dry_ground", "flat": "medium_dry_ground",
                  "dirt": "medium_dry_ground", "sand": "very_dry_ground", "desert": "very_dry_ground",
                  "wet": "wet_ground", "mud": "wet_ground", "asphalt": "concrete", "concrete": "concrete"}
#: Custom dielectric materials (not in ITU-R P.2040): (relative permittivity, conductivity S/m). Estimates.
CUSTOM_MATERIALS = {"plastic": (3.0, 0.004)}
_METAL_WORDS = ("alumin", "steel", "copper", "brass", "iron", "metal", "titanium", "zinc")
_PLASTIC_WORDS = ("petg", "pla", "abs", "nylon", "pa12", "rubber", "plastic", "polycarbonate", "pvc", "acrylic", "foam")
#: Component kinds that are electrically metal at 2.4 GHz when the catalog gives no material.
_METAL_KINDS = ("battery", "computer", "pcb", "esc", "servo", "motor", "sensor", "gearbox", "fuse", "radio", "board")
DEFAULT_OBSTACLE_MATERIAL = "concrete"


def material_for(material: str, kind: str = "") -> str:
    """Radio material class (ITU name or a ``CUSTOM_MATERIALS`` key) for a mechanical material string."""
    m = (material or "").strip().lower().replace("itu_", "").replace("itu-", "").replace(" ", "_")
    if m in ITU_MATERIALS or m in CUSTOM_MATERIALS:
        return m
    if any(word in m for word in _METAL_WORDS):
        return "metal"
    if any(word in m for word in _PLASTIC_WORDS):
        return "plastic"
    if not m:
        return "metal" if kind in _METAL_KINDS else "plastic"
    return "plastic"


@dataclass
class Box:
    name: str
    center: np.ndarray
    size: np.ndarray
    material: str
    material_estimated: bool = False


@dataclass
class BodyMesh:
    """robot body triangles of one radio material, robot frame (m)."""

    material: str
    vertices: np.ndarray
    faces: np.ndarray
    parts: list[str] = field(default_factory=list)


@dataclass
class RadioScene:
    frequency: float
    tx_position: np.ndarray
    tx_power_dbm: float
    tx_gain_dbi: float
    rx_gain_dbi: float
    tx_pattern: str = "hw_dipole"
    rx_pattern: str = "hw_dipole"
    polarization: str = "V"
    ground_material: str = "medium_dry_ground"
    ground_custom: tuple[float, float] | None = None  # (eps_r, sigma) when given explicitly
    ground_half_extent: float = 3000.0
    obstacles: list[Box] = field(default_factory=list)
    body: list[BodyMesh] = field(default_factory=list)
    antenna_offset: np.ndarray = field(default_factory=lambda: np.zeros(3))
    antenna_offset_source: str = "catalog"
    notes: list[str] = field(default_factory=list)

    def digest(self) -> str:
        payload = {
            "f": self.frequency, "tx": np.round(self.tx_position, 4).tolist(), "pat": [self.tx_pattern, self.rx_pattern],
            "pol": self.polarization, "ground": [self.ground_material, self.ground_custom, self.ground_half_extent],
            "obstacles": [[b.name, np.round(b.center, 4).tolist(), np.round(b.size, 4).tolist(), b.material] for b in self.obstacles],
            "body": [[m.material, int(len(m.vertices)), float(np.round(np.sum(m.vertices), 4))] for m in self.body],
            "antenna": np.round(self.antenna_offset, 4).tolist(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _polarization(value: Any) -> str:
    text = str(value or "V").strip().lower()
    return {"vertical": "V", "v": "V", "horizontal": "H", "h": "H", "cross": "cross", "vh": "VH"}.get(text, "V")


def scene_from_environment(environment: Any, receiver_params: dict[str, Any]) -> RadioScene:
    """Static scene (ground, obstacles, antennas) from ``simulation.environment``; no robot body."""
    tx = environment.radio_transmitter
    renv = dict(environment.radio_environment or {})
    position = np.asarray(tx.position, dtype=float) if tx else np.array([0.0, 0.0, 1.2])
    if tx is not None and tx.height is not None:
        position[2] = to_si(tx.height)
    ground = str(renv.get("ground_material") or TERRAIN_GROUND.get(str(getattr(environment, "terrain", "flat")), "medium_dry_ground"))
    ground = material_for(ground) if material_for(ground) in ITU_MATERIALS else "medium_dry_ground"
    custom = None
    notes: list[str] = []
    if "ground_permittivity" in renv and "ground_conductivity" in renv:
        custom = (float(renv["ground_permittivity"]), float(renv["ground_conductivity"]))
    elif "ground_permittivity" in renv:
        notes.append(f"radio_environment.ground_permittivity={renv['ground_permittivity']} ignored without "
                     f"ground_conductivity; the ITU-R P.2040 '{ground}' material is used instead.")
    obstacles = []
    for index, obstacle in enumerate(environment.obstacles or []):
        center = np.asarray([to_si(v) for v in obstacle.get("center", [0, 0, 0])], dtype=float)
        size = np.asarray([to_si(v) for v in obstacle.get("size", [0.3, 0.3, 0.3])], dtype=float)
        given = obstacle.get("material")
        obstacles.append(Box(str(obstacle.get("id", f"obstacle_{index}")), center, size,
                             material_for(str(given)) if given else DEFAULT_OBSTACLE_MATERIAL, material_estimated=not given))
    span = max([float(np.max(np.abs(b.center[:2] - position[:2]))) for b in obstacles] + [0.0])
    p = receiver_params
    return RadioScene(
        frequency=to_si(p.get("frequency", 2.44e9)), tx_position=position,
        tx_power_dbm=float(tx.power_dbm) if tx else 20.0, tx_gain_dbi=float(tx.antenna_gain_dbi) if tx else 2.0,
        rx_gain_dbi=float(p.get("antenna_gain_dbi", 2.0)),
        tx_pattern=str(renv.get("tx_antenna_pattern", "hw_dipole")), rx_pattern=str(p.get("antenna_pattern", "hw_dipole")),
        polarization=_polarization(renv.get("polarization", "V")), ground_material=ground, ground_custom=custom,
        ground_half_extent=max(3000.0, 2.0 * span + 500.0), obstacles=obstacles,
        antenna_offset=np.array([0.0, 0.0, to_si(p.get("antenna_height_above_mount", 0.1))]), notes=notes)


def _part_mesh(project: Any, cid: str, cad_parts: dict[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    """Pulled part-local STL transformed into the robot frame."""
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    from r3_model.pulled_view import world_mesh
    mesh = world_mesh(load_pulled(project.root), cid)
    return np.asarray(mesh.vertices), np.asarray(mesh.faces)


def robot_body(project: Any, exclude: tuple[str, ...] = ()) -> list[BodyMesh]:
    """robot body grouped by radio material (robot frame)."""
    from r3_model.pulled_view import cad_view
    cad_parts = cad_view(project)["parts"]
    groups: dict[str, BodyMesh] = {}
    for cid in sorted(cad_parts):
        if cid in exclude:
            continue
        comp = project.spec.components.get(cid)
        ctype = project.catalog.components.get(comp.type) if comp else None
        triangles = _part_mesh(project, cid, cad_parts)
        if triangles is None:
            continue
        material = material_for(cad_parts[cid].get("material"), ctype.kind if ctype else "structure")
        group = groups.setdefault(material, BodyMesh(material, np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)))
        vertices, faces = triangles
        group.faces = np.vstack([group.faces, faces + len(group.vertices)])
        group.vertices = np.vstack([group.vertices, vertices])
        group.parts.append(cid)
    return [groups[k] for k in sorted(groups)]


def antenna_component(project: Any) -> str | None:
    """The robot's receive antenna: CAD ``rf.antenna_component``, else the first ``antenna`` catalog kind."""
    rf = (project.read_build_json("cad/cad_model.json") or {}).get("rf") or {}
    if rf.get("antenna_component") in project.spec.components:
        return str(rf["antenna_component"])
    for cid, comp in project.spec.components.items():
        if comp.type in project.catalog.components and project.catalog.component(comp.type).kind == "antenna":
            return cid
    return None


def antenna_offset(project: Any, receiver_id: str | None, height_above_mount: float) -> tuple[np.ndarray, str]:
    """An explicit pulled feed connector is required for an antenna phase centre."""
    from r3_model.pulled import PullError
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    pulled = load_pulled(project.root)
    antenna = antenna_component(project) or receiver_id
    part = pulled.parts.get(antenna)
    feed = part.mate_connectors.get("feed") if part else None
    if feed is None:
        raise PullError("cad_antenna_feed_missing", f"{antenna or 'antenna'}: add a feed mate connector in Onshape and pull it; catalog envelopes cannot locate the phase centre")
    return (pulled.poses[antenna] @ feed)[:3, 3], "pulled"


def scene_for_project(project: Any, receiver_id: str | None, receiver_params: dict[str, Any], include_body: bool = True) -> RadioScene:
    scene = scene_from_environment(project.spec.simulation.environment, receiver_params)
    if receiver_id is None and antenna_component(project) is None:
        scene.antenna_offset = np.zeros(3)
        scene.antenna_offset_source = "not_applicable"
        return scene
    scene.antenna_offset, scene.antenna_offset_source = antenna_offset(
        project, receiver_id, to_si(receiver_params.get("antenna_height_above_mount", 0.1)))
    if include_body:
        antenna = antenna_component(project)
        scene.body = robot_body(project, exclude=tuple(c for c in (receiver_id, antenna) if c))
    return scene
