"""Run inside KiCad's bundled Python: read a board back through pcbnew (read-only).

    board_dump.py <board.kicad_pcb> <out.json>

Writes footprints, pads (absolute positions and nets), tracks, vias and filled
zone areas, in millimetres relative to the top-left corner of the Edge.Cuts
outline. ``r3_kicad.board_read`` turns it into typed records for board.json.
"""
import json
import sys

import pcbnew


def mm(v):
    return round(pcbnew.ToMM(v), 4)


def main(argv):
    board = pcbnew.LoadBoard(argv[1])
    # Outline extents from the Edge.Cuts geometry (the edges bounding box also includes the line width).
    xs, ys = [], []
    for shape in board.GetDrawings():
        if shape.GetLayer() == pcbnew.Edge_Cuts and hasattr(shape, "GetStart"):
            for p in (shape.GetStart(), shape.GetEnd()):
                xs.append(p.x)
                ys.append(p.y)
    x0, y0 = min(xs), min(ys)
    width, height = max(xs) - x0, max(ys) - y0

    def loc(p):
        return [mm(p.x - x0), mm(p.y - y0)]

    out = {"size": [mm(width), mm(height)], "origin_page": [mm(x0), mm(y0)],
           "footprints": [], "pads": [], "tracks": [], "vias": [], "zones": []}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        out["footprints"].append({"ref": ref, "lib_id": fp.GetFPIDAsString(), "position": loc(fp.GetPosition()),
                                  "rotation": fp.GetOrientationDegrees(),
                                  "layer": board.GetLayerName(fp.GetLayer())})
        for pad in fp.Pads():
            box = pad.GetBoundingBox()
            out["pads"].append({"ref": ref, "number": pad.GetNumber(), "net": pad.GetNetname(),
                                "position": loc(pad.GetPosition()),
                                "layers": [board.GetLayerName(l) for l in pad.GetLayerSet().CuStack()],
                                "bbox": loc(box.GetOrigin()) + loc(box.GetEnd())})
    for item in board.GetTracks():
        if item.GetClass() == "PCB_VIA":
            out["vias"].append({"net": item.GetNetname(), "position": loc(item.GetPosition()),
                                "diameter": mm(item.GetWidth(pcbnew.F_Cu)), "drill": mm(item.GetDrillValue()),
                                "locked": item.IsLocked()})
        elif item.GetClass() == "PCB_TRACK":
            out["tracks"].append({"net": item.GetNetname(), "layer": board.GetLayerName(item.GetLayer()),
                                  "width": mm(item.GetWidth()), "start": loc(item.GetStart()),
                                  "end": loc(item.GetEnd()), "locked": item.IsLocked()})
    for zone in board.Zones():
        for layer in zone.GetLayerSet().CuStack():
            poly = zone.GetFilledPolysList(layer)
            area = poly.Area() if poly is not None else 0.0
            outlines = []
            if poly is not None and zone.GetNetname() != "/GND":  # power pours: the filled polygons themselves
                for i in range(poly.OutlineCount()):
                    rings = [poly.Outline(i)] + [poly.Hole(i, h) for h in range(poly.HoleCount(i))]
                    outlines.append([[loc(ring.CPoint(k)) for k in range(ring.PointCount())] for ring in rings])
            out["zones"].append({"net": zone.GetNetname(), "layer": board.GetLayerName(layer),
                                 "name": zone.GetZoneName(), "area_mm2": round(area / 1e12, 4), "polygons": outlines})
    json.dump(out, open(argv[2], "w"), indent=1)
    print("dumped", argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
