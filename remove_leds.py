#!/usr/bin/env python3
"""Take the LEDs off the routed board, in place: pcb/tile.kicad_pcb.

The LEDs left the circuit on 2026-10-20 (see tile_circuit.py).  This does what
KiCad's "Update PCB from Schematic" would, without its leftovers: it deletes
the 89 LED parts (their light holes go with them), every track and via on a
net only they used, and moves U1 pad 58 (the old LED data pin) to its
no-connect net.  Then DRC with schematic parity must find no new errors.

Run gen_sch.py first.  KiCad must not have unsaved changes: save first, run
this, then File > Revert.

    ./kpython.sh remove_leds.py [board]      (default pcb/tile.kicad_pcb)
"""

import json
import os
import subprocess
import sys

import pcbnew

import tile_circuit as tc

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "pcb", "tile.kicad_pcb")
LED_PARTS = ({"LED%d" % i for i in range(1, 41)} | {"C%d" % i for i in range(104, 149)}
             | {"U4", "U5", "L2", "R15"})


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
    """DRC after the edit, in a fresh process: forking DRC from the one that
    deleted the footprints crashes KiCad's Python."""
    before = json.load(open(PCB + ".before.json"))
    os.remove(PCB + ".before.json")
    after = drc(PCB)
    new = sorted(set(after[0]) - set(before[0]))
    print("DRC errors: %d before, %d after, %d new; parity issues %d; unrouted %d -> %d"
          % (len(before[0]), len(after[0]), len(new), after[1], before[2], after[2]))
    for e in new:
        print("   NEW", e)
    sys.exit(1 if new or after[1] else 0)


def main():
    sch = json.load(open(os.path.join(HERE, "pcb", "symbol_uuids.json")))
    want = {(ref, pin): net for net, pins in tc.nets().items() for ref, pin in pins}
    for ref, pins in sch["unconnected"].items():
        for pin, net in pins.items():
            want[(ref, pin)] = net.replace("/", "{slash}")

    json.dump(drc(PCB), open(PCB + ".before.json", "w"))
    b = pcbnew.LoadBoard(PCB)
    gone = [f for f in b.GetFootprints() if f.GetReference() not in tc.PARTS and f.GetPath().AsString()]
    assert {f.GetReference() for f in gone} == LED_PARTS, \
        "unexpected parts to delete: %s" % sorted({f.GetReference() for f in gone} ^ LED_PARTS)
    gone_pads = [p for f in gone for p in f.Pads()]
    dead_nets = {p.GetNetname() for p in gone_pads} - set(want.values())

    renamed = []
    for f in [f for f in b.GetFootprints() if f.GetReference() not in LED_PARTS]:
        for p in f.Pads():
            net = want.get((f.GetReference(), p.GetNumber()))
            if net is None or p.GetNetname() == net:
                continue
            ni = b.FindNet(net)
            if ni is None:
                ni = pcbnew.NETINFO_ITEM(b, net)
                b.Add(ni)
            renamed.append("%s.%s %s -> %s" % (f.GetReference(), p.GetNumber(), p.GetNetname(), net))
            p.SetNet(ni)
    assert renamed == ["U1.58 LED_DATA_3V3 -> unconnected-(U1-PB6-Pad58)"], renamed

    removed = stray = 0
    for t in list(b.GetTracks()):
        if t.GetNetname() in dead_nets:
            b.Remove(t)
            removed += 1
        elif any(p.HitTest(pt) for p in gone_pads for pt in (t.GetStart(), t.GetEnd())):
            print("  note: %s track on %s ends on a deleted LED part's pad; delete it by hand"
                  % (t.GetNetname(), b.GetLayerName(t.GetLayer())))
            stray += 1
    for f in gone:
        b.Remove(f)

    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    print("deleted %d LED parts and %d tracks/vias on %d LED-only nets; %s"
          % (len(gone), removed, len(dead_nets), renamed[0]))

    if stray:
        sys.exit("fix the tracks noted above, then run DRC")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), PCB, "--check"])


if __name__ == "__main__":
    check() if "--check" in sys.argv else main()
