"""Connector geometry: pin maps against KiCad footprints, mated pairs, wire ends on pins, GLB determinism."""

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from r3_catalog import load_catalog
from r3_connectors.frames import Frame
from r3_connectors.kicad import kicad_available, library_footprints
from r3_connectors.models import connector_scene, export_glb
from r3_connectors.pins import component_connector_frame, geometry_of, instance_frame
from r3_model.project import Project

ROOT = Path(__file__).resolve().parents[1]
RC_CAR = ROOT / "examples" / "robots" / "rc-car"
needs_kicad = pytest.mark.skipif(not kicad_available(), reason="KiCad (pcbnew) not installed")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_every_connector_type_has_geometry(catalog):
    missing = [t for t, c in catalog.connectors.items() if c.geometry is None and t != "integral"]
    assert not missing
    for type_id, ctype in catalog.connectors.items():
        g = ctype.geometry
        if g is None:
            continue
        assert sorted(g.pins, key=int) == [str(n) for n in range(1, ctype.pin_count + 1)], type_id
        assert g.model.source in ("kicad", "proxy"), type_id
        if g.model.source == "proxy":
            assert g.model.provenance.status == "estimated" and "proxy" in g.model.provenance.source
        else:
            assert g.model.license and g.model.path.startswith("${KICAD10_3DMODEL_DIR}/")
        for pin in g.pins.values():
            assert abs(pin.position[2]) < 1e-12  # contacts sit on the mating face
            if pin.wire_entry is not None:  # cable halves: conductors leave behind the face, away from the mate
                assert pin.wire_entry[2] < 0 and np.dot(pin.wire_axis, [0, 0, 1]) < -0.99


@needs_kicad
def test_pin_maps_match_kicad_footprint_pads(catalog):
    """Every board-side pad in the catalog equals the KiCad footprint pad (pcbnew), through footprint_placement."""
    types = {t: c.geometry for t, c in catalog.connectors.items() if c.geometry is not None and c.geometry.footprint}
    footprints = library_footprints([g.footprint for g in types.values()])
    assert {"header-2x20-2.54", "socket-2x20-2.54", "header-1x3-2.54", "jst-xh-3-header", "jst-xh-3-header-ra",
            "jst-gh-4-header", "xt30pw-male", "xt60-male", "xt60-female", "ufl"} <= set(types)
    for type_id, g in types.items():
        fp = footprints[g.footprint]
        placement = Frame.from_placement(g.footprint_placement)
        pads = {p["number"]: p["position"] for p in fp["pads"] if p["number"].isdigit()}
        for number, pin in g.pins.items():
            assert pin.pad is not None, (type_id, number)
            # KiCad 2D footprint (mm, y down) -> footprint 3D frame -> connector frame.
            expected = placement.point(np.array([pads[number][0], -pads[number][1], 0.0]) * 1e-3)
            assert np.allclose(expected, pin.pad, atol=1e-7), (type_id, number, expected, pin.pad)
        # Pitch and row spacing straight from the pads: contacts keep the footprint's pitch along x.
        if g.pin_shape != "coax":
            xs = sorted({round(p.pad[0], 7) for p in g.pins.values()})
            contacts = sorted({round(p.position[0], 7) for p in g.pins.values()})
            assert np.allclose(xs, contacts, atol=1e-7), type_id


def test_header_pin_grid(catalog):
    """2x20 GPIO header: 2.54 mm grid, pin 1 at one end of the odd row, pin 2 beside it."""
    g = catalog.connector("header-2x20-2.54").geometry
    p = {n: np.asarray(v.position) for n, v in g.pins.items()}
    assert np.allclose(p["1"], [-0.02413, -0.00127, 0], atol=1e-9)
    assert np.allclose(p["2"] - p["1"], [0, 0.00254, 0], atol=1e-9)
    assert np.allclose(p["3"] - p["1"], [0.00254, 0, 0], atol=1e-9)
    assert np.allclose(p["40"], [0.02413, 0.00127, 0], atol=1e-9)
    assert g.pins["1"].tip == pytest.approx(0.006)


def test_catalog_mated_pairs_align(catalog):
    """Each half placed on its partner's mated frame: every contact meets the same-numbered contact."""
    checked = 0
    for type_id, ctype in catalog.connectors.items():
        a = ctype.geometry
        for partner in ctype.mates_with:
            b = geometry_of(catalog, partner)
            if a is None or b is None:
                continue
            frame_a = Frame.from_axes([0.01, -0.02, 0.03], [0, 1, 0], [1, 0, 0])
            frame_b = frame_a.mated()
            assert set(a.pins) == set(b.pins), (type_id, partner)
            for number in a.pins:
                pa, pb = frame_a.point(a.pins[number].position), frame_b.point(b.pins[number].position)
                assert np.linalg.norm(pa - pb) < 1e-9, (type_id, partner, number)
                assert np.dot(frame_a.vector(a.pins[number].axis), frame_b.vector(b.pins[number].axis)) < -0.999
            checked += 1
    assert checked >= 16


@pytest.fixture(scope="module")
def project():
    return Project.load(RC_CAR)


def test_component_connectors_mate_in_place(project):
    """The HAT socket sits on the Pi header: same x/y, faces 0.22 mm apart, pin 1 on pin 1."""
    j8 = component_connector_frame(project, "pi.J8")
    socket = component_connector_frame(project, "hat.J_GPIO")
    catalog = project.catalog
    header, sock = catalog.connector("header-2x20-2.54").geometry, catalog.connector("socket-2x20-2.54").geometry
    d = socket.origin - j8.origin
    assert np.linalg.norm(d - (d @ j8.z) * j8.z) < 1e-6
    assert 0.0 <= d @ j8.z < 5e-4
    assert np.dot(j8.z, socket.z) < -0.9999 and np.dot(j8.x, socket.x) > 0.9999
    for number in header.pins:
        delta = socket.point(sock.pins[number].position) - j8.point(header.pins[number].position)
        assert np.linalg.norm(delta - (delta @ j8.z) * j8.z) < 1e-6, number


@needs_kicad
def test_hat_connector_poses_match_kicad_board(project):
    from r3_connectors.board import board_connectors

    ctype = project.catalog.component("r3-carrier-hat")
    refs = {n: i.source.split()[0].split(":")[1] for n, i in ctype.connectors.items() if i.source.startswith("kicad:")}
    placed = board_connectors(RC_CAR / "pcb" / "carrier-hat" / "carrier-hat.kicad_pcb", ctype, project.catalog, refs)
    assert set(placed) == set(ctype.connectors)
    for name, b in placed.items():
        frame = instance_frame(ctype.connectors[name])
        assert np.linalg.norm(frame.origin - b.frame.origin) < 1e-6, name
        assert np.allclose(frame.z, b.frame.z, atol=1e-6) and np.allclose(frame.x, b.frame.x, atol=1e-6), name
        assert b.pad_error < 1e-6 and b.pad_residual < 1e-6, name


@pytest.fixture(scope="module")
def routed(project):
    from r3_wiring.stage import route_and_check

    return route_and_check(project)


def test_every_wire_ends_on_its_pins(project, routed):
    """Independent of the router: each wire endpoint lies within 0.1 mm of the conductor exit of its pin."""
    routing, routes = routed["routing"], routed["routes"]
    catalog = project.catalog
    checked = 0
    for harness in project.spec.electrical.harnesses:
        for wire in harness.wires:
            data = routes["wires"][wire.id]
            path = np.asarray(data["path"])
            for index, ref in enumerate((wire.from_, wire.to)):
                point = path[0] if index == 0 else path[-1]
                end = data["ends"][index]
                if ref in harness.splices:
                    assert end is None
                    assert np.linalg.norm(point - np.asarray(harness.splices[ref].position)) < 0.02
                    continue
                name, pin = ref.split(".")
                conn = harness.connectors[name]
                assert end["connector"] == f"{harness.id}.{name}" and end["pin"] == pin
                if conn.type == "integral":
                    frame = component_connector_frame(project, conn.mates)
                    # Pigtail roots: on the component's connector face, in a row along its x axis.
                    local = frame.inverse().point(point)
                    assert abs(local[2]) < 1e-9 and abs(local[1]) < 1e-9
                    continue
                if conn.mates and conn.mates in project.graph.endpoints and \
                        project.graph.endpoints[conn.mates].owner_kind == "component":
                    frame = component_connector_frame(project, conn.mates).mated()
                else:
                    frame = routing.nodes[harness.id][name].frame
                expected = frame.point(catalog.connector(conn.type).geometry.pins[pin].wire_entry)
                assert np.linalg.norm(point - expected) <= 1e-4, (wire.id, ref)
                checked += 1
    assert checked >= 25  # 30 wires, both ends: crimp/cup ends here, pigtails and splices above


def test_floating_pairs_share_mating_face(routed, project):
    routing = routed["routing"]
    catalog = project.catalog
    assert routing.floating
    for mate in routing.floating:
        ha, ca = mate.a.split(".")
        hb, cb = mate.b.split(".")
        na, nb = routing.nodes[ha][ca], routing.nodes[hb][cb]
        assert np.allclose(na.frame.origin, nb.frame.origin) and np.dot(na.frame.z, nb.frame.z) < -0.9999
        for number in catalog.connector(na.connector_type).geometry.pins:
            pa = na.frame.point(catalog.connector(na.connector_type).geometry.pins[number].position)
            pb = nb.frame.point(catalog.connector(nb.connector_type).geometry.pins[number].position)
            assert np.linalg.norm(pa - pb) < 1e-9


def test_fanout_core_is_shared_by_bundle(routed):
    """Wires of one branch share their bundle core exactly (MuJoCo cables group them)."""
    from r3_wiring.mujoco_cables import core_path

    wires = routed["routes"]["wires"]
    a, b = core_path(wires["W_RX_5V"]), core_path(wires["W_RX_GND"])
    assert a.shape == b.shape and np.allclose(a, b)
    assert not np.allclose(wires["W_RX_5V"]["path"][0], wires["W_RX_GND"]["path"][0])


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("type_id", ["header-2x20-2.54", "jst-gh-4-header", "xt60-female", "servo-3-female"])
def test_glb_conversion_is_deterministic(catalog, tmp_path, type_id):
    if catalog.connector(type_id).geometry.model.source == "kicad" and not kicad_available():
        pytest.skip("KiCad 3D library not installed")
    first = export_glb(connector_scene(catalog, type_id), tmp_path / "a.glb")
    second = export_glb(connector_scene(catalog, type_id), tmp_path / "b.glb")
    assert _digest(first) == _digest(second)
    # A fresh interpreter (no in-process caches, new random seeds) produces the same bytes.
    code = ("import sys; from pathlib import Path; from r3_catalog import load_catalog; "
            "from r3_connectors.models import connector_scene, export_glb; "
            f"export_glb(connector_scene(load_catalog(), {type_id!r}), Path(sys.argv[1]))")
    env = {**os.environ, "PYTHONHASHSEED": "12345", "PYTHONPATH": os.pathsep.join(sys.path)}
    subprocess.run([sys.executable, "-c", code, str(tmp_path / "c.glb")], check=True, env=env, cwd=ROOT)
    assert _digest(tmp_path / "c.glb") == _digest(first)
    import trimesh

    bounds = trimesh.load(first).bounds
    envelope = catalog.connector(type_id).geometry.envelope
    assert np.all(bounds[1] - bounds[0] <= np.asarray(envelope) + 2e-4)


def test_routes_json_records_pin_ends(routed):
    for wire_id, wire in routed["routes"]["wires"].items():
        path = np.asarray(wire["path"])
        head, tail = wire["fanout"]
        for index, end in enumerate(wire["ends"]):
            if end is None:
                continue
            point = path[0] if index == 0 else path[-1]
            assert np.linalg.norm(point - np.asarray(end["position"])) < 1e-6, wire_id
            assert math.isclose(np.linalg.norm(end["direction"]), 1.0, abs_tol=1e-5)
        assert head + tail < len(path)
    json.dumps(routed["routes"])
