#!/usr/bin/env python3
"""Copy one hand-routed key to every other key: pcb/tile.kicad_pcb, in place.

Every key cluster on the board is identical (tilegeom places them all the
same way), so a key routed by hand is a template for all 40.  This finds the
routed key, copies its tracks to every other key -- shifted by the key
spacing, nets renamed pad by pad (HE7 -> HE12, ...) -- and checks that each
copy starts and ends on pads of the right net.

KiCad must not have unsaved changes: save first, run this, then File > Revert.

    ./kpython.sh route_keys.py
"""

import os
import subprocess
import sys

import pcbnew

import tilegeom as g
import tile_circuit as tc

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "pcb", "tile.kicad_pcb")
REACH = 6.0                 # mm: a template track lies within this of its key centre


def roles(i):
    """The footprints of key i, by role, so key 7's C14 maps to key 12's C24."""
    he, cv, co = [p.ref for p in tc.PARTS.values() if p.block == "keys"][3 * i: 3 * i + 3]
    return {"he": he, "cv": cv, "co": co}


def main():
    b = pcbnew.LoadBoard(PCB)
    fps = {fp.GetReference(): fp for fp in b.GetFootprints()}
    keys = g.keys()
    centre = [fps["SW%d" % (i + 1)].GetPosition() for i in range(len(keys))]

    def near(t, c):
        r = pcbnew.FromMM(REACH)
        return (t.GetStart() - c).EuclideanNorm() < r and (t.GetEnd() - c).EuclideanNorm() < r

    tracks = [t for t in b.GetTracks() if t.GetClass() == "PCB_TRACK"]
    routed = [i for i in range(len(keys)) if any(near(t, centre[i]) for t in tracks)]
    if len(routed) != 1:
        sys.exit("expected exactly one hand-routed key, found %d: %s"
                 % (len(routed), [roles(i)["he"] for i in routed]))
    t0 = routed[0]
    template = [t for t in tracks if near(t, centre[t0])]
    print("template: key %s, %d track segments" % (roles(t0)["he"], len(template)))

    def pad_net_map(i):
        m = {}
        for role, ref in roles(t0).items():
            src, dst = fps[ref], fps[roles(i)[role]]
            dpads = {p.GetNumber(): p.GetNetname() for p in dst.Pads()}
            for p in src.Pads():
                m.setdefault(p.GetNetname(), dpads[p.GetNumber()])
        return m

    def pad_at(pt):
        for fp in b.GetFootprints():
            for p in fp.Pads():
                if p.HitTest(pt):
                    return p
        return None

    added = 0
    for i in range(len(keys)):
        if i == t0:
            continue
        nets = pad_net_map(i)
        shift = centre[i] - centre[t0]
        for t in template:
            n = pcbnew.PCB_TRACK(b)
            n.SetStart(t.GetStart() + shift)
            n.SetEnd(t.GetEnd() + shift)
            n.SetWidth(t.GetWidth())
            n.SetLayer(t.GetLayer())
            n.SetNet(b.FindNet(nets[t.GetNetname()]))
            b.Add(n)
            added += 1
            for end in (n.GetStart(), n.GetEnd()):
                p = pad_at(end)
                assert p is None or p.GetNetname() == n.GetNetname(), \
                    "key %d: track %s ends on pad of %s" % (i + 1, n.GetNetname(), p.GetNetname())

    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    b.BuildConnectivity()
    print("added %d copied segments; unrouted now %d"
          % (added, b.GetConnectivity().GetUnconnectedCount(True)))
    rpt = os.path.join(HERE, "pcb", "tile.drc.json")
    if os.path.exists(rpt):
        os.remove(rpt)
    subprocess.run([os.path.join(HERE, "kcli.sh"), "pcb", "drc", "--schematic-parity", "--severity-all",
                    "--format", "json", "-o", rpt, PCB], capture_output=True)
    import json
    d = json.load(open(rpt))
    errs = [v for v in d["violations"] if v["severity"] == "error"]
    print("DRC: %d errors (excluding unrouted)" % len(errs))
    for v in errs[:10]:
        print("   ", v["type"], v["description"])


if __name__ == "__main__":
    main()
