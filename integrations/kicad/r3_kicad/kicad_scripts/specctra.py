"""Run inside KiCad's bundled Python (the pcbnew scripting API).

    specctra.py export <board.kicad_pcb> <out.dsn>
    specctra.py import <board.kicad_pcb> <in.ses> [--fill]

The same operations as pcbnew's File -> Export -> Specctra DSN and
File -> Import -> Specctra Session, followed by Edit -> Fill All Zones.
"""
import sys

import pcbnew


def main(argv):
    action, board_path, other = argv[1], argv[2], argv[3]
    board = pcbnew.LoadBoard(board_path)
    if action == "export":
        ok = pcbnew.ExportSpecctraDSN(board, other)
        print("exported" if ok else "export failed", other)
        return 0 if ok else 1
    if action == "import":
        ok = pcbnew.ImportSpecctraSES(board, other)
        if not ok:
            print("import failed", other)
            return 1
        if "--fill" in argv:
            filler = pcbnew.ZONE_FILLER(board)
            filler.Fill(board.Zones())
        pcbnew.SaveBoard(board_path, board)
        print("imported", other)
        return 0
    if action == "unroute":
        # Edit -> Delete the autorouted (unlocked) tracks and vias of the listed nets, as a person unroutes
        # a net before autorouting it again; hand-routed locked copper stays.
        nets = set(other.split(","))
        removed = [t for t in board.GetTracks() if t.GetNetname() in nets and not t.IsLocked()]
        for item in removed:
            board.Remove(item)
        pcbnew.SaveBoard(board_path, board)
        print("unrouted", len(removed), "items of", len(nets), "nets")
        return 0
    print("unknown action", action)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
