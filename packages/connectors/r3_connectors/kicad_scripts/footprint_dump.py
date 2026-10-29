"""Run inside KiCad's bundled Python: read footprints through pcbnew (read-only).

    footprint_dump.py library <footprints dir> <out.json> <Lib:Footprint> ...
    footprint_dump.py board <board.kicad_pcb> <out.json> <Ref> ...

``library`` loads official library footprints with ``pcbnew.FootprintLoad``;
``board`` reads placed footprints from a board. Both report, in millimetres,
the footprint origin and orientation, every pad (number, position, size, drill,
shape, copper layers) and the 3D model references (path, offset, rotation,
scale) exactly as KiCad stores them. Positions use KiCad's board axes
(x right, y down); board positions are relative to the Edge.Cuts top-left corner.
"""
import json
import sys

import pcbnew


def mm(v):
    return round(pcbnew.ToMM(v), 5)


def vec(v):
    return [round(float(c), 6) for c in (v.x, v.y, v.z)]


SHAPES = {getattr(pcbnew, n): n.replace("PAD_SHAPE_", "").lower() for n in dir(pcbnew) if n.startswith("PAD_SHAPE_")}


def describe(fp, board, origin):
    def loc(p):
        return [mm(p.x - origin[0]), mm(p.y - origin[1])]

    layer = board.GetLayerName(fp.GetLayer()) if board is not None else "F.Cu"
    pads = []
    for pad in fp.Pads():
        size = pad.GetSize(pcbnew.F_Cu) if hasattr(pad, "GetSize") else pad.GetSize()
        drill = pad.GetDrillSize()
        pads.append({"number": pad.GetNumber(), "position": loc(pad.GetPosition()),
                     "size": [mm(size.x), mm(size.y)], "drill": [mm(drill.x), mm(drill.y)],
                     "shape": SHAPES.get(pad.GetShape(pcbnew.F_Cu) if pad.GetShape.__code__.co_argcount > 1 else pad.GetShape(), "?")
                     if hasattr(pad.GetShape, "__code__") else str(pad.GetShape(pcbnew.F_Cu)),
                     "through_hole": pad.HasHole(), "net": pad.GetNetname() if board is not None else "",
                     "layers": [n for n in ((board or pcbnew.BOARD()).GetLayerName(l) for l in pad.GetLayerSet().CuStack()) if n in ("F.Cu", "B.Cu")]})
    models = [{"filename": m.m_Filename, "offset": vec(m.m_Offset), "rotation": vec(m.m_Rotation), "scale": vec(m.m_Scale),
               "show": bool(m.m_Show)} for m in fp.Models()]
    box = fp.GetCourtyard(pcbnew.F_CrtYd).BBox() if fp.GetCourtyard(pcbnew.F_CrtYd).OutlineCount() else None
    return {"lib_id": fp.GetFPIDAsString(), "reference": fp.GetReference(), "position": loc(fp.GetPosition()),
            "rotation": fp.GetOrientationDegrees(), "layer": layer, "flipped": fp.IsFlipped(),
            "pads": pads, "models": models,
            "courtyard": None if box is None else [loc(box.GetOrigin()), loc(box.GetEnd())]}


def main(argv):
    mode, source, out_path, names = argv[1], argv[2], argv[3], argv[4:]
    out = {"mode": mode, "source": source, "footprints": {}}
    if mode == "library":
        for name in names:
            lib, _, fp_name = name.partition(":")
            fp = pcbnew.FootprintLoad(f"{source}/{lib}.pretty", fp_name)
            if fp is None:
                out["footprints"][name] = None
                continue
            out["footprints"][name] = describe(fp, None, (0, 0))
    else:
        board = pcbnew.LoadBoard(source)
        xs, ys = [], []
        for shape in board.GetDrawings():
            if shape.GetLayer() == pcbnew.Edge_Cuts and hasattr(shape, "GetStart"):
                for p in (shape.GetStart(), shape.GetEnd()):
                    xs.append(p.x)
                    ys.append(p.y)
        origin = (min(xs), min(ys))
        out["origin_page"] = [mm(origin[0]), mm(origin[1])]
        out["size"] = [mm(max(xs) - origin[0]), mm(max(ys) - origin[1])]
        out["thickness"] = mm(board.GetDesignSettings().GetBoardThickness())
        for fp in board.GetFootprints():
            if not names or fp.GetReference() in names:
                out["footprints"][fp.GetReference()] = describe(fp, board, origin)
    json.dump(out, open(out_path, "w"), indent=1)
    print("dumped", len(out["footprints"]), "footprints")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
