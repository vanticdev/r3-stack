"""Run inside KiCad's bundled Python (the ``pcbnew`` module); no GUI is created.

    board_build.py <plan.json> <report.json>

Builds the board the way pcbnew's editor does, through its own engine:
File -> Board Setup (stackup, rules, net classes, predefined sizes), Edge.Cuts
outline, Tools -> Update PCB from Schematic (footprints from the official
libraries by the netlist's footprint field, linked to their symbols by KIID path,
pad nets from the netlist), placement, deliberately routed power tracks and vias,
copper zones, Edit -> Fill All Zones, Save.

plan.json is prepared by ``r3_kicad.pcb`` from KiCad's exported netlist and the
board's design intent; coordinates are board millimetres relative to ``origin``.
"""
import json
import math
import sys

import pcbnew

MM = pcbnew.FromMM


def pt(origin, xy):
    return pcbnew.VECTOR2I(MM(origin[0] + xy[0]), MM(origin[1] + xy[1]))


def setup_board(board, plan):
    bds = board.GetDesignSettings()
    board.SetCopperLayerCount(2)
    bds.SetBoardThickness(MM(plan["thickness"]))
    rules = plan["rules"]
    bds.m_MinClearance = MM(rules["min_clearance"])
    bds.m_TrackMinWidth = MM(rules["min_track_width"])
    bds.m_ViasMinSize = MM(rules["min_via_diameter"])
    bds.m_MinThroughDrill = MM(rules["min_via_drill"])
    bds.m_ViasMinAnnularWidth = MM(rules["min_annular_ring"])
    bds.m_CopperEdgeClearance = MM(rules["copper_edge_clearance"])
    bds.m_HoleClearance = MM(rules["hole_clearance"])
    bds.m_HoleToHoleMin = MM(rules["hole_to_hole"])
    widths = bds.m_TrackWidthList
    widths.clear()
    widths.append(0)
    for w in plan["track_widths"]:
        widths.append(MM(w))
    vias = bds.m_ViasDimensionsList
    vias.clear()
    vias.append(pcbnew.VIA_DIMENSION(0, 0))
    for d, drill in plan["via_sizes"]:
        vias.append(pcbnew.VIA_DIMENSION(MM(d), MM(drill)))
    settings = bds.m_NetSettings
    # Board Setup > Net Classes is replaced, not merged: stale classes or pattern assignments left in the project by
    # an earlier design would otherwise win (the first matching pattern assigns the class).
    settings.ClearNetclassPatternAssignments()
    settings.ClearNetclasses()
    for nc in plan["net_classes"]:
        cls = settings.GetDefaultNetclass() if nc["name"] == "Default" else pcbnew.NETCLASS(nc["name"])
        cls.SetTrackWidth(MM(nc["track_width"]))
        cls.SetClearance(MM(nc["clearance"]))
        cls.SetViaDiameter(MM(nc["via_diameter"]))
        cls.SetViaDrill(MM(nc["via_drill"]))
        if nc["name"] != "Default":
            settings.SetNetclass(nc["name"], cls)
            for net in nc["nets"]:
                settings.SetNetclassPatternAssignment(net, nc["name"])
    title = board.GetTitleBlock()
    title.SetTitle(plan["title"])
    title.SetRevision("A")
    title.SetCompany("R3")
    title.SetDate(plan.get("date", ""))


def outline(board, plan):
    """Rounded-rectangle Edge.Cuts: four lines and four quarter arcs."""
    o, (w, h), r = plan["origin"], plan["size"], plan["corner_radius"]

    def add(shape):
        shape.SetLayer(pcbnew.Edge_Cuts)
        shape.SetWidth(MM(0.1))
        board.Add(shape)

    for (x0, y0), (x1, y1) in (((r, 0), (w - r, 0)), ((w, r), (w, h - r)), ((w - r, h), (r, h)), ((0, h - r), (0, r))):
        line = pcbnew.PCB_SHAPE(board)
        line.SetShape(pcbnew.SHAPE_T_SEGMENT)
        line.SetStart(pt(o, (x0, y0)))
        line.SetEnd(pt(o, (x1, y1)))
        add(line)
    k = r * (1 - math.sqrt(0.5))
    for start, mid, end in (((0, r), (k, k), (r, 0)), ((w - r, 0), (w - k, k), (w, r)),
                            ((w, h - r), (w - k, h - k), (w - r, h)), ((r, h), (k, h - k), (0, h - r))):
        arc = pcbnew.PCB_SHAPE(board)
        arc.SetShape(pcbnew.SHAPE_T_ARC)
        arc.SetArcGeometry(pt(o, start), pt(o, mid), pt(o, end))
        add(arc)


def place(fp, placement, origin, report):
    """Position, side and orientation; with anchors, the quarter turn that lands the listed pads on target."""
    if placement["side"] == "B" and fp.GetLayer() != pcbnew.B_Cu:
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
    anchors = placement.get("anchors") or []
    candidates = [placement["rotation"]] + ([0.0, 90.0, 180.0, 270.0] if anchors else [])
    best = None
    for rotation in candidates:
        fp.SetOrientationDegrees(rotation)
        fp.SetPosition(pt(origin, (placement["x"], placement["y"])))
        if not anchors:
            return
        pads = {p.GetNumber(): p for p in fp.Pads()}
        missing = [n for n, _xy in anchors if n not in pads]
        if missing:
            report["errors"].append("%s: anchor pads missing %s" % (fp.GetReference(), missing))
            return
        first, (tx, ty) = anchors[0]
        p0 = pads[first].GetPosition()
        shift = pt(origin, (tx, ty)) - p0
        fp.SetPosition(fp.GetPosition() + shift)
        error = sum(math.hypot(pcbnew.ToMM(pads[n].GetPosition().x) - origin[0] - x,
                               pcbnew.ToMM(pads[n].GetPosition().y) - origin[1] - y) for n, (x, y) in anchors)
        if best is None or error < best[0]:
            best = (error, rotation, fp.GetPosition())
    fp.SetOrientationDegrees(best[1])
    fp.SetPosition(best[2])
    report["anchored"][fp.GetReference()] = {"rotation": best[1], "error_mm": round(best[0], 4)}
    if best[0] > 0.01:
        report["errors"].append("%s: pad anchors cannot be satisfied (total error %.4f mm)" %
                                (fp.GetReference(), best[0]))


def add_footprints(board, plan, nets, report):
    origin = plan["origin"]
    for comp in plan["components"]:
        lib, name = comp["footprint"].split(":", 1)
        fp = pcbnew.FootprintLoad(plan["footprint_dir"] + "/" + lib + ".pretty", name)
        if fp is None:
            report["errors"].append("footprint not found: " + comp["footprint"])
            continue
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(comp["ref"])
        # Silkscreen references at the board minimum text height (0.8 mm) so dense 0603 rows stay legible.
        ref_text = fp.Reference()
        ref_text.SetTextSize(pcbnew.VECTOR2I(MM(0.8), MM(0.8)))
        ref_text.SetTextThickness(MM(0.12))
        fp.SetValue(comp["value"])
        fp.SetPath(pcbnew.KIID_PATH("/" + comp["uuid"]))
        fp.SetSheetname("/")
        fp.SetSheetfile(plan["sheet_file"])
        for key, value in comp.get("fields", {}).items():
            fp.SetField(key, value)
            fp.GetField(key).SetVisible(False)
        attrs = fp.GetAttributes()
        if not comp.get("in_bom", True):
            attrs |= pcbnew.FP_EXCLUDE_FROM_BOM
        fp.SetAttributes(attrs)
        board.Add(fp)
        pad_nets = comp.get("pads", {})
        numbers = set()
        for pad in fp.Pads():
            number = pad.GetNumber()
            numbers.add(number)
            if number in pad_nets:
                pad.SetNet(nets[pad_nets[number]])
        missing = sorted(set(pad_nets) - numbers)
        if missing:
            report["errors"].append("%s: netlist pins without a pad %s" % (comp["ref"], missing))
        if comp.get("placement"):
            place(fp, comp["placement"], origin, report)
        report["footprints"] += 1


def resolve(board, origin, point):
    if isinstance(point, str):
        ref, number = point.split(".", 1)
        fp = board.FindFootprintByReference(ref)
        pads = [p for p in fp.Pads() if p.GetNumber() == number]
        return pads[0].GetPosition()
    return pt(origin, point)


def add_copper(board, plan, nets, report):
    origin = plan["origin"]
    for track in plan["tracks"]:
        invalid = False
        for point in track["points"]:
            if isinstance(point, str):
                ref, number = point.split(".", 1)
                fp = board.FindFootprintByReference(ref)
                pads = [p for p in fp.Pads() if p.GetNumber() == number] if fp else []
                if not pads or any(p.GetNetname() != track["net"] for p in pads):
                    report["errors"].append("%s: track endpoint %s has a missing or different pad net" % (track["net"], point))
                    invalid = True
        if invalid:
            continue
        layer = board.GetLayerID(track["layer"])
        points = [resolve(board, origin, p) for p in track["points"]]
        for a, b in zip(points, points[1:]):
            seg = pcbnew.PCB_TRACK(board)
            seg.SetStart(a)
            seg.SetEnd(b)
            seg.SetWidth(MM(track["width"]))
            seg.SetLayer(layer)
            seg.SetNet(nets[track["net"]])
            seg.SetLocked(True)              # deliberate copper: Freerouting keeps it as fixed wiring
            board.Add(seg)
            report["tracks"] += 1
    for v in plan["vias"]:
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(pt(origin, (v["x"], v["y"])))
        via.SetWidth(MM(v["diameter"]))
        via.SetDrill(MM(v["drill"]))
        via.SetNet(nets[v["net"]])
        via.SetLocked(True)
        board.Add(via)
        report["vias"] += 1
    add_zones(board, plan["zones"], nets, origin, report)


def add_zones(board, zones, nets, origin, report):
    for spec in zones:
        zone = pcbnew.ZONE(board)
        zone.SetLayer(board.GetLayerID(spec["layer"]))
        zone.SetNet(nets[spec["net"]])
        zone.SetZoneName(spec["name"])
        zone.SetAssignedPriority(spec["priority"])
        zone.SetLocalClearance(MM(spec["clearance"]))
        zone.SetMinThickness(MM(spec["min_thickness"]))
        zone.SetThermalReliefGap(MM(spec["thermal_gap"]))
        zone.SetThermalReliefSpokeWidth(MM(spec["thermal_spoke"]))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL if spec["solid"] else pcbnew.ZONE_CONNECTION_THERMAL)
        zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in spec["polygon"]:
            p = pt(origin, (x, y))
            outline.Append(p.x, p.y)
        board.Add(zone)
        report["zones"] += 1


def add_keepouts(board, keepouts, origin, report):
    """Mounting envelopes as rule areas owned by the hole footprint (DRC exempts the owner itself):
    components outside the screw head / standoff envelope plus print clearance, copper outside the
    metal contact circle. Board Setup -> keepout rule areas, as a person draws them in pcbnew."""
    for spec in keepouts:
        fp = board.FindFootprintByReference(spec["ref"])
        cx, cy = spec["x"], spec["y"]
        for rule in spec["rules"]:
            zone = pcbnew.ZONE(fp)
            zone.SetIsRuleArea(True)
            zone.SetZoneName("%s %s" % (spec["ref"], rule["name"]))
            layers = pcbnew.LSET()
            layers.AddLayer(board.GetLayerID(rule["layer"]))
            zone.SetLayerSet(layers)
            copper = rule["kind"] == "copper"
            zone.SetDoNotAllowTracks(copper)
            zone.SetDoNotAllowVias(copper)
            zone.SetDoNotAllowPads(False)          # other footprints are kept out by the envelope area
            zone.SetDoNotAllowZoneFills(copper)
            zone.SetDoNotAllowFootprints(not copper)
            outline = zone.Outline()
            outline.NewOutline()
            for i in range(48):
                a = 2 * math.pi * i / 48
                p = pt(origin, (cx + rule["radius"] * math.cos(a), cy + rule["radius"] * math.sin(a)))
                outline.Append(p.x, p.y)
            fp.Add(zone)
            report["keepouts"] += 1


def stitch_ground(board, net_name, spacing, diameter, drill, report):
    """Stitching vias over open ground, the way a person drops them after the pours: a via wherever both
    layers' ground fills have room for it, at least ``spacing`` from every other ground via (candidates on a
    1 mm grid, so small pour fragments get tied to the other layer too)."""
    zones = [z for z in board.Zones() if not z.GetIsRuleArea() and z.GetNetname() == net_name]
    layers = sorted({z.GetLayer() for z in zones})
    if len(layers) < 2:
        return
    net = board.FindNet(net_name)
    edge = board.GetBoardEdgesBoundingBox()
    r = MM(diameter / 2 + 0.25)
    probes = [(0, 0)] + [(r * math.cos(a * math.pi / 4), r * math.sin(a * math.pi / 4)) for a in range(8)]
    keep = MM(diameter / 2 + 0.6)          # stay off every pad and hole (solid pours fill around same-net pads)
    obstacles = []
    for pad in board.GetPads():
        box = pad.GetBoundingBox()
        box.Inflate(keep)
        obstacles.append(box)
    placed = []
    for item in board.GetTracks():
        if item.GetClass() == "PCB_VIA":
            box = item.GetBoundingBox()
            box.Inflate(keep)
            obstacles.append(box)
            if item.GetNetname() == net_name:
                placed.append((item.GetPosition().x, item.GetPosition().y))
    spacing_iu = MM(spacing)

    def filled(x, y):
        return all(any(z.HitTestFilledArea(layer, pcbnew.VECTOR2I(int(x + dx), int(y + dy)), 0) for z in zones
                       if z.GetLayer() == layer) for layer in layers for dx, dy in probes)

    step = MM(1.0)
    y = edge.GetY() + step
    while y < edge.GetBottom():
        x = edge.GetX() + step
        while x < edge.GetRight():
            here = pcbnew.VECTOR2I(int(x), int(y))
            if (all(math.hypot(x - px, y - py) >= spacing_iu for px, py in placed)
                    and not any(box.Contains(here) for box in obstacles) and filled(x, y)):
                via = pcbnew.PCB_VIA(board)
                via.SetPosition(here)
                via.SetWidth(MM(diameter))
                via.SetDrill(MM(drill))
                via.SetNet(net)
                board.Add(via)
                placed.append((x, y))
                report["stitching_vias"] += 1
            x += step
        y += step


def _ground_clusters(board, net_name):
    """Connected groups of a net's copper: fill fragments, vias, pads and tracks (geometric connectivity:
    shared track endpoints, endpoints on pads/vias, items inside a fill fragment on their layer)."""
    frags = []
    for zone in board.Zones():
        if zone.GetIsRuleArea() or zone.GetNetname() != net_name:
            continue
        layer = zone.GetLayer()
        polys = zone.GetFilledPolysList(layer)
        frags += [(layer, polys, i) for i in range(polys.OutlineCount())]
    items = []                                   # (layers, points, object)
    for t in board.GetTracks():
        if t.GetNetname() != net_name:
            continue
        if t.GetClass() == "PCB_VIA":
            items.append(((pcbnew.F_Cu, pcbnew.B_Cu), [t.GetPosition()], t))
        else:
            items.append(((t.GetLayer(),), [t.GetStart(), t.GetEnd()], t))
    for p in board.GetPads():
        if p.GetNetname() == net_name:
            items.append((tuple(l for l in (pcbnew.F_Cu, pcbnew.B_Cu) if p.IsOnLayer(l)), [p.GetPosition()], p))
    n = len(frags)
    parent = list(range(n + len(items)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for j, (layers, points, _obj) in enumerate(items):
        for i, (layer, polys, idx) in enumerate(frags):
            if layer in layers and any(polys.Contains(pt, idx) for pt in points):
                parent[find(n + j)] = find(i)
    for j, (layers_a, points_a, _a) in enumerate(items):
        for k, (layers_b, _points_b, obj_b) in enumerate(items):
            if k <= j or not set(layers_a) & set(layers_b):
                continue
            if any(obj_b.HitTest(pt) for pt in points_a) or any(_a.HitTest(pt) for pt in _points_b):
                parent[find(n + j)] = find(n + k)
    groups = {}
    for i in range(len(parent)):
        groups.setdefault(find(i), []).append(i)
    clusters = [[frags[i] for i in g if i < n] for g in groups.values()]
    sizes = [len(g) for g in groups.values()]
    return clusters, sizes


def repair_ground(board, net_name, diameter, drill, report):
    """A pour fragment that is not connected to the main ground (cut off by tracks on both layers) gets a via
    where it overlaps the main ground's fill on the other layer, as a person fixes an isolated pour piece."""
    net = board.FindNet(net_name)
    keep = MM(diameter / 2 + 0.25)
    probes = [(0, 0)] + [(keep * math.cos(a * math.pi / 4), keep * math.sin(a * math.pi / 4)) for a in range(8)]
    blockers = []
    for pad in board.GetPads():
        box = pad.GetBoundingBox()
        box.Inflate(MM(diameter / 2 + 0.3))
        blockers.append(box)
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            box = t.GetBoundingBox()
            box.Inflate(MM(diameter / 2 + 0.3))
            blockers.append(box)
    for _attempt in range(10):
        clusters, sizes = _ground_clusters(board, net_name)
        if len(clusters) <= 1:
            return
        main = clusters[sizes.index(max(sizes))]
        fixed = False
        for cluster in clusters:
            if cluster is main or not cluster:
                continue
            for layer, polys, idx in cluster:
                others = [(l, p, i) for l, p, i in main if l != layer]
                box = polys.Outline(idx).BBox()
                y = box.GetY()
                while y <= box.GetBottom() and not fixed:
                    x = box.GetX()
                    while x <= box.GetRight() and not fixed:
                        here = pcbnew.VECTOR2I(int(x), int(y))
                        pts = [pcbnew.VECTOR2I(int(x + dx), int(y + dy)) for dx, dy in probes]
                        if (not any(b.Contains(here) for b in blockers)
                                and all(polys.Contains(p, idx) for p in pts)
                                and any(all(op.Contains(p, oi) for p in pts) for _l, op, oi in others)):
                            via = pcbnew.PCB_VIA(board)
                            via.SetPosition(here)
                            via.SetWidth(MM(diameter))
                            via.SetDrill(MM(drill))
                            via.SetNet(net)
                            board.Add(via)
                            report["island_vias"] += 1
                            fixed = True
                        x += MM(0.25)
                    y += MM(0.25)
                if fixed:
                    break
            if fixed:
                break
        if not fixed:
            report["untied_islands"] += len(clusters) - 1
            return
        fill(board)


def fill(board):
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())


def main(argv):
    plan = json.load(open(argv[1]))
    report = {"footprints": 0, "tracks": 0, "vias": 0, "zones": 0, "keepouts": 0, "stitching_vias": 0, "island_vias": 0,
              "untied_islands": 0, "anchored": {},
              "errors": []}
    if plan.get("mode") == "zones":
        # Post-route step: add the ground pours to the routed board and refill everything.
        board = pcbnew.LoadBoard(plan["board"])
        nets = {n.GetNetname(): n for n in board.GetNetsByName().values()}
        add_zones(board, plan["zones"], nets, plan["origin"], report)
        stitch = plan.get("stitch")
        if stitch:
            fill(board)
            stitch_ground(board, stitch["net"], stitch["spacing"], stitch["diameter"], stitch["drill"], report)
            fill(board)
            repair_ground(board, stitch["net"], stitch["diameter"], stitch["drill"], report)
    else:
        board = pcbnew.NewBoard(plan["board"])
        setup_board(board, plan)
        outline(board, plan)
        nets = {}
        for name in plan["nets"]:
            net = pcbnew.NETINFO_ITEM(board, name)
            board.Add(net)
            nets[name] = net
        add_footprints(board, plan, nets, report)
        add_keepouts(board, plan.get("keepouts", []), plan["origin"], report)
        add_copper(board, plan, nets, report)
    fill(board)
    pcbnew.SaveBoard(plan["board"], board)
    json.dump(report, open(argv[2], "w"), indent=2)
    print("board saved", plan["board"], report["footprints"], "footprints", len(report["errors"]), "errors")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
