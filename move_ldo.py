#!/usr/bin/env python3
"""Move the 3.3 V regulator group (U3, C100-C103, FB1) 2.5 mm up, off SW5's peg hole.

Its own +3V3 / +3.3VA tracks move with it.  The +3V7 tracks from L1 to U3 and
C100 are deleted, since their old ends would land on C100's GND pad: redraw
those two links by hand.

KiCad must not have unsaved changes: save first, run this, then File > Revert.

    ./kpython.sh move_ldo.py [board]      (default pcb/tile.kicad_pcb)
"""

import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "pcb", "tile.kicad_pcb")
GROUP = ["U3", "C100", "C101", "C102", "C103", "FB1"]
SHIFT = pcbnew.VECTOR2I(0, pcbnew.FromMM(-2.5))
L1_PAD2_X = 31.5            # mm: +3V7 tracks left of here run L1 -> U3 / C100


def main():
    b = pcbnew.LoadBoard(PCB)
    mm = pcbnew.ToMM
    fps = [f for f in b.GetFootprints() if f.GetReference() in GROUP]
    assert len(fps) == len(GROUP)
    u3 = next(f for f in fps if f.GetReference() == "U3")
    assert abs(mm(u3.GetPosition().y) - 33.76) < 0.05, "U3 has moved since this script was written"
    pads = {(p.GetPosition().x, p.GetPosition().y) for f in fps for p in f.Pads()}

    moved = deleted = 0
    for t in list(b.GetTracks()):
        if t.GetClass() != "PCB_TRACK":
            continue
        net = t.GetNetname()
        xs = (mm(t.GetStart().x), mm(t.GetEnd().x))
        ys = (mm(t.GetStart().y), mm(t.GetEnd().y))
        if net == "+3V7" and max(xs) <= L1_PAD2_X + 0.01 and min(xs) >= 27.0 and 31.0 <= min(ys) and max(ys) <= 37.0:
            b.Remove(t)
            deleted += 1
        elif net in ("+3V3", "+3.3VA") and 21.5 <= min(xs) and max(xs) <= 31.5 and 27.5 <= min(ys) and max(ys) <= 38.0:
            t.Move(SHIFT)
            moved += 1
    for f in fps:
        f.Move(SHIFT)
    print("moved %d parts and %d tracks; deleted %d +3V7 tracks" % (len(fps), moved, deleted))
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)


if __name__ == "__main__":
    main()
