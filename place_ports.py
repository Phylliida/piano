#!/usr/bin/env python3
"""Move the four port headers to tilegeom.port_layout(), in place: pcb/tile.kicad_pcb.

The headers' spots live in tilegeom.PORT_SPOTS, so the case's wall slots and
the board can't disagree.  This moves each header there (keeping its
rotation, after checking it opens toward its slot), puts its two 220R
resistors just behind it, and moves the post keep-out areas to
tilegeom.posts().  Tracks that ran to the old spots are left for you to
reroute.  Then DRC with schematic parity runs, in a fresh process.

KiCad must not have unsaved changes: save first, run this, then File > Revert.

    ./kpython.sh place_ports.py [board]      (default pcb/tile.kicad_pcb)
"""

import json
import math
import os
import subprocess
import sys

import pcbnew

import tilegeom as g
import tile_circuit as tc

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "pcb", "tile.kicad_pcb")
MM = pcbnew.FromMM
mm = pcbnew.ToMM


def drc(path):
    rpt = path + ".drc.json"
    if os.path.exists(rpt):
        os.remove(rpt)
    subprocess.run([os.path.join(HERE, "kcli.sh"), "pcb", "drc", "--schematic-parity", "--severity-all",
                    "--format", "json", "-o", rpt, path], capture_output=True)
    d = json.load(open(rpt))
    os.remove(rpt)
    errs = sorted("%s: %s" % (v["type"], " / ".join(i["description"] for i in v["items"]))
                  for v in d["violations"] if v["severity"] == "error")
    return errs, len(d.get("schematic_parity", [])), len(d["unconnected_items"])


def check():
    """DRC in a fresh process (KiCad's Python can crash forking after edits)."""
    before = json.load(open(PCB + ".before.json"))
    os.remove(PCB + ".before.json")
    after = drc(PCB)
    new = sorted(set(after[0]) - set(before[0]))
    print("DRC errors: %d before, %d after, %d new; parity issues %d; unrouted %d -> %d"
          % (len(before[0]), len(after[0]), len(new), after[1], before[2], after[2]))
    for e in new:
        print("   NEW", e)
    sys.exit(1 if after[1] else 0)


def courtyard(fp):
    fp.BuildCourtyardCaches()
    c = fp.GetCourtyard(pcbnew.B_CrtYd).BBox()
    return mm(c.GetLeft()), mm(c.GetTop()), mm(c.GetRight()), mm(c.GetBottom())


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def hits_circle(r, cx, cy, rad):
    nx, ny = min(max(cx, r[0]), r[2]), min(max(cy, r[1]), r[3])
    return math.hypot(nx - cx, ny - cy) < rad


def main():
    json.dump(drc(PCB), open(PCB + ".before.json", "w"))
    b = pcbnew.LoadBoard(PCB)
    fps = {f.GetReference(): f for f in b.GetFootprints()}
    pegs = [g.to_kicad(x, y) for x, y in g.peg_holes()]
    posts = [g.to_kicad(x, y) for x, y in g.posts()]

    # 1. Headers.
    moving, resistors = [], []
    for side, lay in g.port_layout().items():
        j = fps[tc.PORT[side]]
        fx, fy = lay["facing"][0], -lay["facing"][1]          # tile y is up, KiCad's is down
        sig = [p.GetPosition() for p in j.Pads() if p.GetNumber() != "MP"]
        mp = [p.GetPosition() for p in j.Pads() if p.GetNumber() == "MP"]
        ox = sum(mm(p.x) for p in mp) / 2 - sum(mm(p.x) for p in sig) / 6
        oy = sum(mm(p.y) for p in mp) / 2 - sum(mm(p.y) for p in sig) / 6
        assert ox * fx + oy * fy > 0, "%s opens the wrong way: rotate it to face %s" % (j.GetReference(), side)
        x, y = g.to_kicad(*lay["header"])
        j.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
        moving.append(j.GetReference())
        pads = {p.GetNumber(): p for p in j.Pads()}
        for pin in ("3", "4"):
            net = pads[pin].GetNetname()
            ref = next(r for r, p in tc.PARTS.items() if p.block == "ports" and p.pins.get("2") == net)
            px, py = mm(pads[pin].GetPosition().x), mm(pads[pin].GetPosition().y)
            resistors.append((ref, (px - fx * 2.6, py - fy * 2.6), (fx, fy)))
    # 2. Each header's resistors: behind its pads 3 (A) and 4 (B), pad 2 toward it.
    for ref, want, f in resistors:
        moving.append(ref)
        place_near(b, fps[ref], want, f, pegs, posts)

    # 3. Post keep-outs follow tilegeom.posts().
    areas = [z for z in b.Zones() if z.GetIsRuleArea()]
    centres = [((mm(z.GetBoundingBox().GetLeft()) + mm(z.GetBoundingBox().GetRight())) / 2,
                (mm(z.GetBoundingBox().GetTop()) + mm(z.GetBoundingBox().GetBottom())) / 2) for z in areas]
    stale = [k for k, c in enumerate(centres) if min(math.dist(c, p) for p in posts) > 0.01]
    vacant = [p for p in posts if min(math.dist(c, p) for c in centres) > 0.01]
    assert len(stale) == len(vacant), (stale, vacant)
    for k, p in zip(stale, vacant):
        c = centres[k]
        areas[k].Move(pcbnew.VECTOR2I(MM(p[0] - c[0]), MM(p[1] - c[1])))
        print("post keep-out moved (%.2f, %.2f) -> (%.2f, %.2f)" % (c + p))

    for ref in moving:
        f = fps[ref]
        print("%-4s at (%.2f, %.2f)" % (ref, mm(f.GetPosition().x), mm(f.GetPosition().y)))
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), PCB, "--check"])


def place_near(b, fp, want, f, pegs, posts):
    """Put fp at the free spot nearest `want`, its axis along f, pad 2 leading."""
    fixed = [courtyard(o) for o in b.GetFootprints()
             if o.IsFlipped() and o.GetReference() != fp.GetReference()]
    for k in range(0, 200):
        r, a = 0.25 * math.sqrt(k), k * 2.39996          # sunflower spiral around `want`
        x, y = want[0] + r * math.cos(a), want[1] + r * math.sin(a)
        for rot in (0, 90, 180, 270):
            fp.SetOrientationDegrees(rot)
            fp.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
            p = {q.GetNumber(): q.GetPosition() for q in fp.Pads()}
            d = (mm(p["2"].x) - mm(p["1"].x), mm(p["2"].y) - mm(p["1"].y))
            if d[0] * f[0] + d[1] * f[1] < 0.5:              # pad 2 must lead toward the header
                continue
            c = courtyard(fp)
            if any(overlaps(c, o) for o in fixed):
                continue
            if any(hits_circle(c, px, py, g.PEG_HOLE / 2 + 0.25) for px, py in pegs):
                continue
            if any(hits_circle(c, px, py, g.POST_KEEPOUT) for px, py in posts):
                continue
            return
    sys.exit("no room for %s near (%.1f, %.1f)" % (fp.GetReference(), *want))


if __name__ == "__main__":
    check() if "--check" in sys.argv else main()
