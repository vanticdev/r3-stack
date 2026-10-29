"""Build-up: a chosen candidate becomes a project catalog entry.

The entry is written to ``catalog/components/sourced_<id>.yaml``. Its electrical
and mechanical *contract* (connector names and pin maps the robot's harnesses,
direct mates and bindings use; mounting features its interfaces need) comes
from a **template**: the slot's current catalog part, or else the catalog part of
the same kind that provides every connector the robot references for the slot.
The candidate's sourced parameters then overwrite what they describe (envelope,
mass, supply range, protocol, band, RAM, hole pattern). Template values the
candidate does not confirm keep the template's provenance, downgraded to
``estimated`` when the template described a different part.

The file carries a top-level ``sourced:`` block (ignored by the catalog loader)
with the candidate, offers, 3D models, the requirement matches and
``fields``: provenance per catalog field path (``mechanical.size``,
``pins.5V.voltage_min``, ...).
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

import yaml

from ..schema import Catalog, ComponentType
from .candidate import Candidate, SourcedValue
from .quantities import normalize
from .requirements import ParamMatch, Requirement

CATALOG_PREFIX = "sourced"


def entry_id(candidate: Candidate) -> str:
    return f"{CATALOG_PREFIX}-{candidate.id}"[:90]


def file_name(candidate: Candidate) -> str:
    return f"{CATALOG_PREFIX}_{re.sub(r'[^a-z0-9]+', '_', candidate.id)}.yaml"


# ---------------------------------------------------------------- templates
def referenced_connectors(spec: Any, component_id: str) -> set[str]:
    """Connector names of ``component_id`` used by harness mates, direct mates or bindings."""
    used: set[str] = set()

    def note(ref: str | None) -> None:
        if ref and ref.split(".")[0] == component_id and len(ref.split(".")) >= 2:
            used.add(ref.split(".")[1])

    for harness in spec.electrical.harnesses:
        for connector in harness.connectors.values():
            note(connector.mates)
    for mate in spec.electrical.direct_mates:
        note(mate.a)
        note(mate.b)
    for binding in spec.software.bindings.values():
        for ref in [*binding.pins, *binding.expects]:
            note(ref)
    return used


def select_template(spec: Any, catalog: Catalog, components: list[str], kind: str) -> ComponentType | None:
    for cid in components:
        authored = spec.components[cid].type
        if authored and authored in catalog.components and not authored.startswith(f"{CATALOG_PREFIX}-"):
            return catalog.components[authored]
    needed = set().union(*(referenced_connectors(spec, cid) for cid in components)) if components else set()
    options = [c for c in catalog.components.values() if c.kind == kind and not c.id.startswith(f"{CATALOG_PREFIX}-")
               and needed <= set(c.connectors)]
    options.sort(key=lambda c: (-len(set(c.connectors) & needed), c.id))
    return options[0] if options else None


def same_product(template: ComponentType, candidate: Candidate) -> bool:
    """Does the template part describe the candidate's product (same part number or name)?"""
    label = normalize(template.part_number or template.name)
    text = normalize(f"{candidate.manufacturer} {candidate.name} {candidate.mpn}")
    return bool(label) and (label in text or normalize(candidate.name) == normalize(template.name))


# ------------------------------------------------------------------ helpers
def _status(value: SourcedValue) -> str:
    return value.status


def _prov(value: SourcedValue) -> dict[str, str]:
    return {"status": value.status, "source": value.source, **({"notes": value.note} if value.note else {})}


class _Builder:
    def __init__(self, candidate: Candidate, template: ComponentType | None, kind: str) -> None:
        self.candidate = candidate
        self.template = template
        self.kind = kind
        self.fields: dict[str, dict[str, str]] = {}
        self.notes: list[str] = []
        if template is not None:
            self.entry = template.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            self.entry.setdefault("mechanical", {}).setdefault("size", list(template.mechanical.size))
            self.same_product = same_product(template, candidate)
            for path in ("mechanical", "behavior"):
                if path in self.entry:
                    self.fields[path] = ({"status": "derived", "source": f"template:{template.id}",
                                          "notes": "the template describes this same product"} if self.same_product else
                                         {"status": "estimated", "source": f"template:{template.id}",
                                          "notes": "inherited from the slot's template part; not confirmed for the sourced part"})
            if not self.same_product:
                self._downgrade_template_provenance()
        else:
            self.entry = {"mechanical": {"shape": "box", "size": [0.02, 0.02, 0.01], "mass": 0.01}}
            self.fields["mechanical"] = {"status": "estimated", "source": "r3 default", "notes": "no template; placeholder envelope"}
            self.notes.append(f"no catalog template of kind {kind!r}: no pins or connectors; the robot cannot wire it until edited")
        self.param = {name: value for name, value in candidate.params.items()}

    def _downgrade_template_provenance(self) -> None:
        """Template values describe another product: they are estimates for this one."""
        template_id = self.template.id if self.template else ""

        def downgrade(block: dict[str, Any] | None) -> dict[str, Any]:
            block = block or {"status": "estimated"}
            original = f"{block.get('status', 'estimated')}: {block.get('source', '')}".strip(": ")
            return {"status": "estimated", "source": f"template:{template_id}", "notes": f"inherited ({original})"}

        mech = self.entry["mechanical"]
        mech["provenance"] = downgrade(mech.get("provenance"))
        for feature in mech.get("features", []):
            feature["provenance"] = downgrade(feature.get("provenance"))
        if "behavior" in self.entry:
            self.entry["behavior"]["provenance"] = downgrade(self.entry["behavior"].get("provenance"))

    def set(self, path: str, value: Any, sourced: SourcedValue) -> None:
        node = self.entry
        keys = path.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
        self.fields[path] = _prov(sourced)

    def get(self, name: str) -> SourcedValue | None:
        return self.param.get(name)

    # ------------------------------------------------------------ per kind
    def common(self) -> None:
        c = self.candidate
        self.entry.update({"id": entry_id(c), "kind": self.kind, "name": c.name, "manufacturer": c.manufacturer, "part_number": c.mpn,
                           "description": (c.description or c.name) + f" (sourced by r3 from {c.provider})"})
        statuses = [p.status for p in c.params.values()]
        overall = "datasheet" if statuses and statuses.count("datasheet") >= len(statuses) / 2 else "vendor" if "vendor" in statuses else "estimated"
        self.entry["provenance"] = {"status": overall, "source": c.primary_url, "notes": f"r3 source: {c.provider}, candidate {c.id}"}
        mass = self.get("mass")
        if mass and isinstance(mass.value, (int, float)):
            # mechanical.provenance is what the mass budget reports as the mass source.
            self.set("mechanical.mass", float(mass.value), mass)
            self.entry["mechanical"]["provenance"] = _prov(mass)

    def wheel(self) -> None:
        diameter, width = self.get("diameter"), self.get("width")
        mech = self.entry["mechanical"]
        if diameter and width and isinstance(diameter.value, (int, float)) and isinstance(width.value, (int, float)):
            d, w = float(diameter.value), float(width.value)
            mech.update({"shape": "cylinder", "axis": mech.get("axis", "y")})
            self.set("mechanical.size", [d, w], diameter if diameter.status == "estimated" else width)
            axis = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[mech["axis"]]
            mech["solids"] = [{"kind": "cylinder", "name": "tire", "position": [-a * w / 2 for a in axis], "axis": axis,
                               "diameter": d, "length": w}]
            self.fields["mechanical.solids"] = {"status": "derived", "source": "diameter x width"}
            features = []
            for feature in mech.get("features", []):
                kept = dict(feature)
                if feature["kind"] == "face":
                    # Seating faces scale with the wheel width (template offsets kept proportionally).
                    old_w = float(self.template.mechanical.size[1]) if self.template else w
                    kept["position"] = [p * w / old_w for p in feature["position"]]
                if feature.get("diameter", 0) >= d * 0.95:
                    self.notes.append(f"template feature {feature['id']} ({feature['diameter'] * 1e3:.1f} mm) does not fit a {d * 1e3:.1f} mm wheel; dropped")
                    continue
                features.append(kept)
            mech["features"] = features
            if "behavior" in self.entry:
                self.set("behavior.params.radius", d / 2, diameter)
                self.set("behavior.params.width", w, width)
        hub = self.get("hub")
        if hub and isinstance(hub.value, dict):
            size, shape = float(hub.value["size"]), normalize(hub.value["shape"])
            hub_features = [f for f in mech.get("features", []) if f["id"].startswith("hub_") and f["kind"] in ("bore", "d_bore", "socket")
                            and not f["id"].startswith("hub_recess")]
            template_kind = {"bore": "bore", "d_bore": "d-bore", "socket": "hex"}
            same = [f for f in hub_features if template_kind[f["kind"]] == shape and abs(f["diameter"] - size) <= size * 0.02]
            if not same:
                anchor = hub_features[0] if hub_features else {"position": [0, -float(mech["size"][1]) / 2 + 0.014, 0], "axis": [0, 1, 0],
                                                               "depth": 0.01}
                mech["features"] = [f for f in mech.get("features", []) if f not in hub_features]
                kind = {"hex": "socket", "bore": "bore", "d-bore": "d_bore"}.get(shape, "socket")
                feature_id = {"socket": "hub_hex", "bore": "hub_bore", "d_bore": "hub_dbore"}[kind]
                mech["features"].append({"id": feature_id, "kind": kind, "position": anchor["position"], "axis": anchor["axis"],
                                         "diameter": size, "depth": anchor.get("depth", 0.01), "provenance": _prov(hub)})
                self.fields[f"mechanical.features.{feature_id}"] = _prov(hub)
                if hub_features:
                    self.notes.append(f"hub {size * 1e3:g} mm {shape} replaces the template's "
                                      f"{', '.join(f['id'] for f in hub_features)}: parts mating at the hub need an adapter or a new interface")

    def radio(self) -> None:
        supply = self.get("supply")
        if supply is not None:
            for name, pin in self.entry.get("pins", {}).items():
                if pin.get("role") == "power_in":
                    span = supply.value if isinstance(supply.value, list) else [supply.value, supply.value]
                    self.set(f"pins.{name}.voltage_min", float(span[0]), supply)
                    self.set(f"pins.{name}.voltage_max", float(span[1]), supply)
                    self.set(f"pins.{name}.voltage_nominal", (float(span[0]) + float(span[1])) / 2, supply)
        protocol = self.get("protocol")
        if protocol is not None:
            names = protocol.value if isinstance(protocol.value, list) else [protocol.value]
            software = [s for s in self.entry.get("software", []) if s.get("bus") == "uart"]
            if software:
                software[0]["protocol"] = str(names[0])
                self.fields["software.uart.protocol"] = _prov(protocol)
        band = self.get("band")
        behavior = self.entry.get("behavior")
        if band is not None and behavior is not None:
            span = band.value if isinstance(band.value, list) else [band.value, band.value]
            self.set("behavior.params.frequency", round((float(span[0]) + float(span[1])) / 2, -6), band)
        power = self.get("telemetry_power")
        if power is not None and behavior is not None:
            self.set("behavior.params.telemetry_power_dbm", float(power.value), power)
        self._box_size()

    def computer(self) -> None:
        mech = self.entry["mechanical"]
        length, width = self.get("length"), self.get("width")
        if length and width:
            size = list(mech.get("size", [0.0, 0.0, 0.0]))
            size[0], size[1] = float(length.value), float(width.value)
            self.set("mechanical.size", size, length if length.status == "estimated" else width)
        holes = self.get("mount_holes")
        if holes and isinstance(holes.value, list) and self.template is not None and self.template.mechanical.mount_holes:
            xs = [h[0] for h in self.template.mechanical.mount_holes]
            ys = [h[1] for h in self.template.mechanical.mount_holes]
            pitch = [max(xs) - min(xs), max(ys) - min(ys)]
            if all(abs(a - float(b)) < 1e-4 for a, b in zip(pitch, holes.value)):
                self.fields["mechanical.mount_holes"] = _prov(holes)
                self.fields["mechanical.features"] = {**_prov(holes), "notes": "template hole features match the drawing's pattern"}
            else:
                self.notes.append(f"hole pattern {holes.value} differs from the template's {pitch}; template holes removed")
                mech["mount_holes"], mech["features"] = [], [f for f in mech.get("features", []) if f["kind"] != "clearance_hole"]
        supply = self.get("supply")
        current = self.get("supply_current")
        for name, pin in self.entry.get("pins", {}).items():
            if pin.get("role") == "power_in":
                if supply is not None and isinstance(supply.value, (int, float)):
                    self.set(f"pins.{name}.voltage_nominal", float(supply.value), supply)
                if current is not None and isinstance(current.value, (int, float)):
                    self.set(f"pins.{name}.current_max", float(current.value), current)
        ram = self.get("ram")
        if ram is not None:
            self.fields["name"] = _prov(ram)

    def _box_size(self) -> None:
        dims = [self.get(n) for n in ("length", "width", "height")]
        if all(d is not None and isinstance(d.value, (int, float)) for d in dims):
            mech = self.entry["mechanical"]
            mech["shape"] = "box"
            self.set("mechanical.size", [float(d.value) for d in dims], dims[0])


def build_entry(candidate: Candidate, requirement: Requirement, template: ComponentType | None,
                matches: list[ParamMatch] | None = None, slot: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """(catalog entry, ``sourced`` metadata). The entry validates as a :class:`ComponentType`."""
    builder = _Builder(candidate, template, requirement.kind)
    builder.common()
    getattr(builder, requirement.kind, lambda: builder._box_size())()
    entry = builder.entry
    ComponentType.model_validate(entry)  # fail early on an inconsistent build
    step = next((m for m in candidate.models if m.format in ("step", "step_zip")), None)
    meta = {
        "slot": slot or requirement.slot, "components": requirement.components, "candidate_id": candidate.id,
        "candidate_digest": candidate.digest(), "provider": candidate.provider, "retrieved_at": candidate.retrieved_at,
        "requirement": requirement.raw, "requirement_sha256": requirement.digest(), "template": template.id if template else None,
        "urls": candidate.urls, "offers": [o.model_dump(mode="json") for o in candidate.offers],
        "models": [m.model_dump(mode="json") for m in candidate.models],
        "cad": {"status": "vendor_step" if step else "proxy", "model": step.url if step else None,
                "notes": "import with `r3 source --import-cad`" if step else "parametric proxy from the catalog envelope (no vendor model)"},
        "params": {k: v.model_dump(mode="json") for k, v in candidate.params.items()},
        "matches": [m.to_json() for m in matches or []],
        "fields": builder.fields, "notes": builder.notes + candidate.notes,
    }
    return entry, meta


class _Dumper(yaml.SafeDumper):
    """Flow style for short scalar lists (vectors, ranges), block style for the rest."""


def _represent_list(dumper: yaml.SafeDumper, data: list[Any]) -> yaml.Node:
    flow = len(data) <= 8 and all(isinstance(v, (int, float, str, bool)) or v is None for v in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


def _represent_float(dumper: yaml.SafeDumper, value: float) -> yaml.Node:
    return dumper.represent_float(float(f"{value:.10g}"))


_Dumper.add_representer(list, _represent_list)
_Dumper.add_representer(tuple, _represent_list)
_Dumper.add_representer(float, _represent_float)


def render_file(entry: dict[str, Any], meta: dict[str, Any]) -> str:
    header = (f"# Generated by `r3 source` from {meta['provider']} (candidate {meta['candidate_id']}).\n"
              "# Provenance per field is under sourced.fields; re-source with `r3 source <component> --choose N`.\n")
    body = yaml.dump({"components": [entry], "sourced": meta}, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)
    return header + body


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def substituted_catalog(catalog: Catalog, entry: dict[str, Any]) -> Catalog:
    """A shallow copy of ``catalog`` with ``entry`` added (for twin checks before choosing)."""
    clone = catalog.model_copy()
    clone.components = {**catalog.components, entry["id"]: ComponentType.model_validate(copy.deepcopy(entry))}
    return clone
