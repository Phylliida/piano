#!/usr/bin/env python3
"""Place tile_circuit.py on a board with KiCad's own API: pcb/tile.kicad_pcb.

Run gen_sch.py first: every footprint is linked to its schematic symbol by
UUID, so KiCad's "Update PCB from Schematic" and DRC's schematic-parity check
both see one design.  Then this checks itself with KiCad's DRC.

    ./kpython.sh gen_pcb.py

What it places, and how
  * under every key, at fixed spots from tilegeom: the switch (front), and on
    the back the Hall sensor and its two capacitors
  * the edge ports at tilegeom.ports(), opening toward the wall slot
  * everything else by search: start at a target near the pins it serves and
    spiral outward until the part's courtyard is clear of every courtyard,
    switch peg, cover post and the board edge

Orientation is never assumed: parts are rotated until the pads they read back
point the way they should (e.g. the sensor's supply pins toward its caps).
Placement is the job here; routing (drawing the copper) is left to KiCad.
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
OUT = os.path.join(HERE, "pcb")
PCB = os.path.join(OUT, "tile.kicad_pcb")
FPDIR = os.environ["KICAD9_FOOTPRINT_DIR"]
MM = pcbnew.FromMM
PEG_CLEAR = 0.25        # courtyard-to-hole clearance around the switch pegs
# Copper layers.  2 is cheaper and enough here: every part is on the back, so
# the front (switch side) is free for long runs and ground -- HE60 routes 67
# keys on 2 layers.  4 would give a solid ground plane and easier routing.
LAYERS = 2


# ------------------------------------------------------------- coordinates ---
def kpt(x, y):
    """Tile coordinates (y up) -> KiCad VECTOR2I (y down)."""
    kx, ky = g.to_kicad(x, y)
    return pcbnew.VECTOR2I(MM(kx), MM(ky))


def tpt(v):
    """KiCad VECTOR2I -> tile coordinates."""
    x0, _, _, y1 = g.bbox(g.outline())
    return pcbnew.ToMM(v.x) - 10.0 + x0, y1 + 10.0 - pcbnew.ToMM(v.y)


def load(fpid, board):
    """Load a footprint and add it to the board at once: KiCad's Flip()
    segfaults on a footprint that has no board yet."""
    nick, name = fpid.split(":", 1)
    path = os.path.join(OUT, "tile.pretty") if nick == "tile" else os.path.join(FPDIR, nick + ".pretty")
    fp = pcbnew.FootprintLoad(path, name)
    assert fp is not None, "footprint %s not found" % fpid
    fp.SetFPID(pcbnew.LIB_ID(nick, name))
    board.Add(fp)
    return fp


# ------------------------------------------------------------------ placer ---
class Placer:
    """Keeps every placed courtyard and hole as a rectangle in tile coordinates."""

    def __init__(self, board):
        self.board = board
        self.rects = {"F": [], "B": []}
        self.outline = g.pcb_outline()

    def block(self, rect, sides="FB"):
        for s in sides:
            self.rects[s].append(rect)

    def free(self, rect, side, edge_ok=False):
        if any(g.overlaps(rect, r) for r in self.rects[side]):
            return False
        if edge_ok:     # e.g. a connector whose opening sits on the board edge
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            return g.inside(cx, cy, self.outline)
        return g._rect_inside(rect, self.outline)

    @staticmethod
    def set(fp, x, y, rot, side):
        if (side == "B") != (fp.GetLayer() == pcbnew.B_Cu):
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetPosition(kpt(x, y))
        fp.SetOrientationDegrees(rot)

    @staticmethod
    def courtyard(fp):
        fp.BuildCourtyardCaches()
        layer = pcbnew.B_CrtYd if fp.GetLayer() == pcbnew.B_Cu else pcbnew.F_CrtYd
        poly = fp.GetCourtyard(layer)
        bb = poly.BBox() if poly.OutlineCount() else fp.GetBoundingBox(False)
        (x0, y0), (x1, y1) = tpt(bb.GetOrigin()), tpt(bb.GetEnd())
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def put(self, fp, x, y, rot, side, record=True):
        self.set(fp, x, y, rot, side)
        if fp.GetParent() is None:
            self.board.Add(fp)
        rect = self.courtyard(fp)
        if record:
            self.block(rect, side)
        return rect

    def search(self, fp, target, side, rots=(0, 90, 180, 270), radius=60.0, step=0.5, edge_ok=False,
               extra=None):
        """Nearest clear spot to `target`, trying each rotation at each spot.
        `extra` is a further rectangle, relative to the part, that must be clear
        too and is then reserved (e.g. the space a mated plug occupies)."""
        rel = {}
        for rot in rots:
            self.set(fp, 0.0, 0.0, rot, side)
            rel[rot] = self.courtyard(fp)
        n = int(radius / step)
        spots = sorted(((i * step, j * step) for i in range(-n, n + 1) for j in range(-n, n + 1)),
                       key=lambda d: d[0] ** 2 + d[1] ** 2)
        for dx, dy in spots:
            x, y = target[0] + dx, target[1] + dy
            for rot in rots:
                r = rel[rot]
                rect = (r[0] + x, r[1] + y, r[2] + x, r[3] + y)
                if self.free(rect, side, edge_ok):
                    if extra:
                        ex = (extra[0] + x, extra[1] + y, extra[2] + x, extra[3] + y)
                        if not self.free(ex, side):
                            continue
                        self.block(ex, side)
                    self.put(fp, x, y, rot, side)
                    return x, y, rot
        dump_geometry(self.board)       # so the failure can be looked at
        raise RuntimeError("no room for %s near %s" % (fp.GetReference(), target))


def pads(fp):
    """{pad number: (x, y)} in tile coordinates (the last pad wins for repeats)."""
    return {p.GetNumber(): tpt(p.GetPosition()) for p in fp.Pads()}


def centroid(pts):
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def rot_pointing(fp, side, frm, to, direction, rots=(0, 90, 180, 270)):
    """The rotation at which pads `to` lie in `direction` from pads `frm`."""
    best = None
    for rot in rots:
        Placer.set(fp, 0.0, 0.0, rot, side)
        # every pad of each number: a connector has two pads called "MP"
        a = centroid([tpt(pd.GetPosition()) for pd in fp.Pads() if pd.GetNumber() in frm])
        b = centroid([tpt(pd.GetPosition()) for pd in fp.Pads() if pd.GetNumber() in to])
        v = (b[0] - a[0], b[1] - a[1])
        score = (v[0] * direction[0] + v[1] * direction[1]) / (math.hypot(*v) or 1)
        if best is None or score > best[0]:
            best = (score, rot)
    return best[1]


# ------------------------------------------------------------------- build ---
def refuse_to_clobber():
    """Never overwrite a board someone has started routing.  Set
    REGEN_ROUTED_BOARD=1 to really start over (copy your board first)."""
    if os.path.exists(PCB) and os.environ.get("REGEN_ROUTED_BOARD") != "1":
        routed = len(pcbnew.LoadBoard(PCB).GetTracks())
        if routed:
            sys.exit("%s already has %d tracks/vias -- not overwriting your routing.\n"
                     "Back it up and set REGEN_ROUTED_BOARD=1 to regenerate anyway." % (PCB, routed))


def main():
    refuse_to_clobber()
    sch = json.load(open(os.path.join(OUT, "symbol_uuids.json")))
    links = sch["symbols"]
    # The netlist export spells "/" inside these names plainly; KiCad's own net
    # names keep it escaped, since "/" separates sheet paths.
    unconnected = {ref: {pin: n.replace("/", "{slash}") for pin, n in pins.items()}
                   for ref, pins in sch["unconnected"].items()}
    board = pcbnew.NewBoard(PCB)
    board.SetCopperLayerCount(LAYERS)
    ds = board.GetDesignSettings()
    # JLC's minimum copper-to-edge is 0.2 mm.
    ds.m_CopperEdgeClearance = MM(0.25)
    ds.m_MinResolvedSpokes = 1      # crowded small pads get one thermal spoke; enough
    placer = Placer(board)
    nets = {}
    for name in list(tc.nets()) + sorted({n for pins in unconnected.values() for n in pins.values()}):
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        nets[name] = ni
    fps = {}
    quiet = ("keys",)               # dense block: references on the fab layer only

    def make(ref):
        p = tc.PARTS[ref]
        fp = load(p.footprint, board)
        fp.SetReference(ref)
        fp.SetValue(p.value)
        fp.SetPath(pcbnew.KIID_PATH("/" + links[ref]))
        fp.SetSheetname("/")
        fp.SetSheetfile("tile.kicad_sch")
        fp.SetDNP(p.dnp)
        have = {pd.GetNumber() for pd in fp.Pads()}
        missing = set(p.pins) - have
        assert not missing, "%s: circuit pins %s have no pad in %s" % (ref, missing, p.footprint)
        for pd in fp.Pads():
            net = p.pins.get(pd.GetNumber()) or unconnected.get(ref, {}).get(pd.GetNumber())
            if net:
                pd.SetNet(nets[net])
        if p.block in quiet:
            fp.Reference().SetVisible(False)
        fps[ref] = fp
        return fp

    # -- switches (front) and the parts under each key (back) -----------------
    he_of_key = {}
    for (i, c, r, x, y), n in zip(g.keys(), range(1, len(g.keys()) + 1)):
        sw = load("tile:MX-1U", board)
        sw.SetReference("SW%d" % n)
        sw.SetValue("MX_HE")
        sw.SetBoardOnly(True)
        sw.SetExcludedFromBOM(True)
        sw.SetExcludedFromPosFiles(True)
        sw.Reference().SetVisible(False)
        placer.put(sw, x, y, 0, "F", record=False)
        placer.block(g._rect(x, y, g.SWITCH_CUTOUT + 1, g.SWITCH_CUTOUT + 1), "F")
        for px, py in ((x - g.PEG_DX, y), (x + g.PEG_DX, y)):
            d = g.PEG_HOLE + 2 * PEG_CLEAR
            placer.block(g._rect(px, py, d, d))

        he_ref, cv_ref, co_ref = [p.ref for p in tc.PARTS.values() if p.block == "keys"][3 * i: 3 * i + 3]
        he = make(he_ref)
        placer.put(he, x, y, rot_pointing(he, "B", ["3"], ["1", "2"], (0, 1)), "B")   # pins 1, 2 north
        hp = pads(he)
        he_of_key[i] = he_ref
        for cref, pin in ((cv_ref, "1"), (co_ref, "2")):
            cfp = make(cref)
            side = math.copysign(1.0, hp[pin][0] - x)     # the side its sensor pin is on
            cx = x + side * g.SENSOR_CAP_DX
            cy = y + g.SENSOR_CAP_DY
            placer.put(cfp, cx, cy, rot_pointing(cfp, "B", ["2"], ["1"], (-side, 0)), "B")

    # -- posts: nothing on the back where the cover presses ------------------
    for x, y in g.posts():
        placer.block(g._rect(x, y, 2 * g.POST_KEEPOUT, 2 * g.POST_KEEPOUT), "B")

    # -- edge ports -----------------------------------------------------------
    # Each header sits in a gap between keys, opening toward its wall slot
    # (tilegeom.port_layout).  The plug that sticks out of it is reserved as
    # well, so nothing gets placed where the plug goes; its cable then bends to
    # the slot through the 5 mm under the board, over low parts.
    plug_w = 9.5                                 # GHR-06V-S plug: 8.75 mm wide
    plug_d = g.PLUG_PROTRUSION + 0.5
    for edge, lay in g.port_layout().items():
        j = make(tc.PORT[edge])
        fx, fy = lay["facing"]
        rot = rot_pointing(j, "B", ["1", "2", "3", "4", "5", "6"], ["MP"], (fx, fy))
        Placer.set(j, 0.0, 0.0, rot, "B")
        r = Placer.courtyard(j)
        if fx > 0:
            zone_rel = (r[2], -plug_w / 2, r[2] + plug_d, plug_w / 2)
        elif fx < 0:
            zone_rel = (r[0] - plug_d, -plug_w / 2, r[0], plug_w / 2)
        elif fy > 0:
            zone_rel = (-plug_w / 2, r[3], plug_w / 2, r[3] + plug_d)
        else:
            zone_rel = (-plug_w / 2, r[1] - plug_d, plug_w / 2, r[1])
        placer.search(j, lay["header"], "B", rots=(rot,), radius=12, step=0.25, extra=zone_rel)

    # -- MCU and its parts, then everything else near the pins it serves -----
    mcu = make(tc.U1.ref)
    placer.search(mcu, (3 * g.COL_PITCH, 3 * g.ROW_PITCH + g.PORT_GAP_DY), "B", rots=(0,))

    def near_pin(ref, part_ref, pin, gap=2.5, radius=20):
        """Place `ref` just outside `part_ref`'s pad, its pin 1 toward the pad."""
        anchor_fp = fps[part_ref]
        cx, cy = tpt(anchor_fp.GetPosition())
        px, py = pads(anchor_fp)[pin]
        d = (px - cx, py - cy)
        if abs(d[0]) > abs(d[1]):
            v = (math.copysign(1, d[0]), 0)
        else:
            v = (0, math.copysign(1, d[1]))
        fp = make(ref)
        rots = (0, 90, 180, 270)
        if len(fp.Pads()) == 2:
            rots = (rot_pointing(fp, "B", ["2"], ["1"], (-v[0], -v[1])),)
        return placer.search(fp, (px + v[0] * gap, py + v[1] * gap), "B", rots=rots, radius=radius)

    def across_pins(ref, part_ref, pin_a, pin_b, gap=2.4):
        """Place a 2-pin part across the end of `part_ref` where pads pin_a and
        pin_b sit, its pin 1 toward pin_a -- e.g. a buck's input cap straddling
        VIN and GND, for the smallest possible current loop."""
        anchor_fp = fps[part_ref]
        c = tpt(anchor_fp.GetPosition())
        P = pads(anchor_fp)
        a, b = P[pin_a], P[pin_b]
        m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        L = math.hypot(m[0] - c[0], m[1] - c[1]) or 1.0
        out = ((m[0] - c[0]) / L, (m[1] - c[1]) / L)
        fp = make(ref)
        rot = rot_pointing(fp, "B", ["2"], ["1"], (a[0] - b[0], a[1] - b[1]))
        return placer.search(fp, (m[0] + out[0] * gap, m[1] + out[1] * gap), "B", rots=(rot,), radius=8)

    def unplaced(net, block):
        return [p.ref for p in tc.PARTS.values()
                if p.block == block and net in p.pins.values() and p.ref not in fps]

    mcu_caps = [p.ref for p in tc.PARTS.values() if p.block == "mcu" and p.symbol == "Device:C"]
    vdd_caps = [r for r in mcu_caps if tc.PARTS[r].pins.get("1") == "+3V3"]
    for ref, pin in zip(vdd_caps, ["1", "36", "64", "1"]):
        near_pin(ref, tc.U1.ref, pin)
    for ref in [r for r in mcu_caps if tc.PARTS[r].pins.get("1") == "+3.3VA"]:
        near_pin(ref, tc.U1.ref, "13")
    near_pin(next(r for r in mcu_caps if tc.PARTS[r].pins.get("1") == "NRST"), tc.U1.ref, "7")
    near_pin(tc.Y1.ref, tc.U1.ref, "5", gap=4.5)
    for ref, pin in zip([r for r in mcu_caps if tc.PARTS[r].pins.get("1", "").startswith("HEXT")], ["1", "3"]):
        near_pin(ref, tc.Y1.ref, pin, gap=1.5)
    mcu_pin_of = {"BOOT0": "60", "PA11_PD": "44", "PA12_PD": "45", "OTGHS_R": "33"}
    for p in tc.PARTS.values():
        if p.block == "mcu" and p.symbol == "Device:R":
            near_pin(p.ref, tc.U1.ref, mcu_pin_of[p.pins["1"]])
    near_pin(tc.DBG.ref, tc.U1.ref, "46", gap=10, radius=40)

    # Muxes: each in the middle of its key row, with its cap at pin 16.
    for m in range(g.ROWS):
        am_ref = "AM%d" % (m + 1)
        fp = make(am_ref)
        placer.search(fp, (3.5 * g.COL_PITCH, m * g.ROW_PITCH + 0.25 * g.ROW_PITCH), "B")
        near_pin(unplaced("+3.3VA", "muxes")[0], am_ref, "16")

    # Port resistors beside their connector's data pins.
    for edge, jref in tc.PORT.items():
        for ref in [p.ref for p in tc.PARTS.values() if p.block == "ports" and p.symbol == "Device:R"
                    and any(n.startswith(edge + "_") for n in p.pins.values())]:
            pin = "3" if "%s_A" % edge in tc.PARTS[ref].pins.values() else "4"
            near_pin(ref, jref, pin, gap=3.0, radius=25)

    # Power: the 3.7 V buck with its input cap straddling VIN/GND, then the
    # inductor, feedback divider, LDO and the analog ferrite around it.  It
    # sits left of the MCU, clear of every port's cable path.
    u2 = make(tc.U2.ref)
    placer.search(u2, (1.0 * g.COL_PITCH, 3 * g.ROW_PITCH + g.PORT_GAP_DY), "B")
    across_pins(tc.BUCK_CIN.ref, tc.U2.ref, "3", "4")
    near_pin(tc.L1.ref, tc.U2.ref, "5", gap=4.0)
    for ref in unplaced("BST", "power"):
        near_pin(ref, tc.U2.ref, "6")
    for ref in unplaced("FB", "power"):
        near_pin(ref, tc.U2.ref, "1")
    near_pin(tc.D1.ref, tc.BUCK_CIN.ref, "1", gap=3.0)
    for ref in [r for r in unplaced("+3V7", "power") if tc.PARTS[r].value.startswith("22u")]:
        near_pin(ref, tc.L1.ref, "2")
    u3 = make(tc.U3.ref)
    placer.search(u3, tpt(fps[tc.L1.ref].GetPosition()), "B")
    for ref in unplaced("+3V7", "power"):
        near_pin(ref, tc.U3.ref, "1")
    for ref in [r for r in unplaced("+3V3", "power") if r != tc.FB1.ref]:
        near_pin(ref, tc.U3.ref, "5")
    near_pin(tc.FB1.ref, tc.U3.ref, "5", gap=4.0)
    for ref in unplaced("+3.3VA", "power"):
        near_pin(ref, tc.FB1.ref, "2")

    # Regulator ground pins connect solidly to the ground fill (heat, and the
    # buck's current loop); everything else gets thermal spokes, so small
    # parts don't tombstone in reflow.
    for ref, pin in ((tc.U2.ref, "4"), (tc.U3.ref, "2")):
        for pd in fps[ref].Pads():
            if pd.GetNumber() == pin:
                pd.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)

    left = [r for r in tc.PARTS if r not in fps]
    assert not left, "unplaced: %s" % left

    # -- outline, keep-outs, planes ------------------------------------------
    pts = g.pcb_outline()
    for a, b in zip(pts, pts[1:] + pts[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(MM(0.1))
        seg.SetStart(kpt(*a))
        seg.SetEnd(kpt(*b))
        board.Add(seg)

    def zone(points, layer, net=None, rule=False):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        ol = z.Outline()
        ol.NewOutline()
        for p in points:
            v = kpt(*p)
            ol.Append(v.x, v.y)
        if rule:
            z.SetIsRuleArea(True)
            z.SetDoNotAllowFootprints(True)
            z.SetDoNotAllowPads(True)
            z.SetDoNotAllowTracks(False)
            z.SetDoNotAllowVias(False)
            z.SetDoNotAllowCopperPour(False)
        else:
            z.SetNet(nets[net])
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
            z.SetMinThickness(MM(0.2))
            z.SetLocalClearance(MM(0.3))
        board.Add(z)
        return z

    for x, y in g.posts():
        r = g.POST_KEEPOUT
        zone([(x + r * math.cos(a), y + r * math.sin(a)) for a in (i * math.pi / 4 for i in range(8))],
             pcbnew.B_Cu, rule=True)
    # Ground fills (filled after saving).  On 2 layers, ground covers both
    # sides and the router stitches them together with vias; on 4, the first
    # inner layer is a solid plane.
    for layer in ((pcbnew.F_Cu, pcbnew.B_Cu) if LAYERS == 2 else (pcbnew.F_Cu, pcbnew.In1_Cu)):
        zone(pts, layer, "GND")

    pcbnew.SaveBoard(PCB, board)
    # Fill the ground planes on the board as KiCad loads it from disk: filling
    # the in-memory board above ignores hole clearances.
    set_netclasses(PCB.replace(".kicad_pcb", ".kicad_pro"))
    board = pcbnew.LoadBoard(PCB)
    board.GetDesignSettings().m_MinResolvedSpokes = 1
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(PCB, board)
    print("wrote %s: %d footprints (%d switches), %d nets"
          % (os.path.relpath(PCB, HERE), len(board.GetFootprints()), g.KEYS_PER_TILE, len(nets)))
    dump_geometry(board)
    drc()


# Track widths per net, so KiCad's router picks the right one by itself.
# JLCPCB 4-layer handles far finer than this; these are comfortable, not minimal.
NETCLASSES = [
    # name     track  clearance  via    drill  nets
    ("Default", 0.20, 0.15, 0.60, 0.30, []),
    ("Power",   0.50, 0.20, 0.80, 0.40, ["+3V7", "+3V3", "+3.3VA", "GND", "SW"]),
    ("Input",   1.00, 0.30, 0.80, 0.40, ["VIN"]),      # up to 2 A passed between tiles
]


def set_netclasses(pro):
    """Write NETCLASSES into the .kicad_pro, after KiCad's own save."""
    d = json.load(open(pro))
    ns = d.setdefault("net_settings", {})
    base = next(c for c in ns["classes"] if c["name"] == "Default")
    classes = []
    for prio, (name, track, clear, via, drill, _) in enumerate(NETCLASSES):
        c = dict(base, name=name, track_width=track, clearance=clear, via_diameter=via, via_drill=drill)
        c["priority"] = 2147483647 if name == "Default" else prio
        classes.append(c)
    ns["classes"] = classes
    ns["netclass_patterns"] = [{"netclass": name, "pattern": net}
                               for name, *_, nets in NETCLASSES for net in nets]
    # Crowded small pads get one thermal spoke; that carries their current fine.
    d["board"]["design_settings"]["rules"]["min_resolved_spokes"] = 1
    with open(pro, "w") as fh:
        json.dump(d, fh, indent=2)


def dump_geometry(board):
    """pcb/placement.json: courtyards and pads in tile coordinates, for renders."""
    out = {"outline": g.pcb_outline(), "posts": g.posts(), "parts": []}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        part = tc.PARTS.get(ref)
        out["parts"].append({
            "ref": ref, "side": "B" if fp.GetLayer() == pcbnew.B_Cu else "F",
            "block": part.block if part else "switch", "dnp": bool(part and part.dnp),
            "courtyard": Placer.courtyard(fp),
            "pads": [[pd.GetNumber(), *tpt(pd.GetPosition()), pcbnew.ToMM(pd.GetSize().x),
                      pcbnew.ToMM(pd.GetSize().y), pd.GetNetname()] for pd in fp.Pads()]})
    with open(os.path.join(OUT, "placement.json"), "w") as fh:
        json.dump(out, fh)


def drc():
    rpt = os.path.join(OUT, "tile.drc.json")
    if os.path.exists(rpt):
        os.remove(rpt)          # never read last run's report by mistake
    r = subprocess.run([os.path.join(HERE, "kcli.sh"), "pcb", "drc", "--schematic-parity", "--severity-all",
                        "--format", "json", "-o", rpt, PCB], capture_output=True, text=True)
    assert os.path.exists(rpt), "DRC did not run:\n" + r.stdout[-2000:] + r.stderr[-2000:]
    data = json.load(open(rpt))
    counts = {}
    for key in ("violations", "unconnected_items", "schematic_parity"):
        for v in data.get(key, []):
            t = (key, v["severity"], v["type"])
            counts[t] = counts.get(t, 0) + 1
    for (key, sev, typ), n in sorted(counts.items()):
        print("DRC %-18s %-8s %-28s %d" % (key, sev, typ, n))
    bad = {t: n for t, n in counts.items()
           if t[1] == "error" and t[2] != "unconnected_items" or t[0] == "schematic_parity"}
    assert not bad, "DRC found problems: %s" % bad


if __name__ == "__main__":
    main()
