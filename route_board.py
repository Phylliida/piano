#!/usr/bin/env python3
"""Route everything the hand routing left open: pcb/tile.kicad_pcb, in place.

The board came to this script with the parts placed, every key's two stubs
and the power block routed by hand.  This adds the rest, in stages:

    clean    drop the VIN leftovers that ran to the ports' old spots
    nets     give the pads the nets tile_circuit.py now names (the mux channel
             order and U1's signal pins were chosen for the copper)
    lanes    the sensor outputs of rows 0, 1, 2 and 4, each into its row's mux
    bus      the mux selects and outputs, up the middle on the front
    lanes3   row 3's outputs, into the mux that stands beside U1
    supply   +3.3VA to every sensor, every mux and U1's VDDA
    vin      VIN between the four ports and to D1
    uarts    the two data lines of each port
    mcu      U1's +3V3, crystal, reset, BOOT0, debug pads and pull-downs
    gnd      a via to the front's ground plane at every ground pad
    ties     vias between the two ground pours, cell by cell

All parts are on the back, so the back carries nearly everything; the front
is the ground plane and only carries what has to cross: the mux select and
output lines up the middle, VIN, the port lines and a few hops.  Every track is checked against the board's own
clearances as it is added (pcbroute.py), and KiCad's DRC with schematic
parity judges the result, in a fresh process.

KiCad must not have unsaved changes: save first, run this, then File > Revert.
It refuses to run on a board it has already routed.

    ./kpython.sh route_board.py [board]      (default pcb/tile.kicad_pcb)
"""

import json
import math
import os
import subprocess
import sys

import numpy as np
import pcbnew

import pcbroute
import tilegeom as g
import tile_circuit as tc

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "pcb", "tile.kicad_pcb")
STAGES = os.environ.get("ROUTE_STAGES", "all").split(",")      # for trying one stage at a time
mm = pcbnew.ToMM

ROT0 = [m for m in range(g.ROWS) if m != 3]     # rows whose mux lies along its band


def key_no(c, m):
    """1-based key number (HE7, SW7, ...) of column c, row m."""
    return next(k[0] for k in tc.KEYS if k[1] == c and k[2] == m) + 1


def pin_of(ref, net):
    """The (first) pin of a part that is on a net."""
    return next(k for k, v in tc.PARTS[ref].pins.items() if v == net)


def cap_refs(n):
    """Key n's supply and output capacitors."""
    he, cv, co = [p.ref for p in tc.PARTS.values() if p.block == "keys"][3 * (n - 1): 3 * n]
    assert he == "HE%d" % n
    return cv, co


# -------------------------------------------------------------------- clean ---
def clean(r):
    """The VIN tracks drawn before the ports moved end in mid-air now."""
    def stale(t):
        if t.GetNetname() != "VIN":
            return False
        if t.GetClass() == "PCB_VIA":
            return True                         # both were at the N port's old spot
        ends = [(mm(p.x), mm(p.y)) for p in (t.GetStart(), t.GetEnd())]
        return t.GetLayer() == pcbnew.F_Cu or any(95 < x < 110 and 49 < y < 54 for x, y in ends) \
            or any(x < 27 and y > 43 for x, y in ends)
    print("clean: removed %d stale VIN tracks and vias" % r.remove_tracks(stale))
    # ... and a text that lost its footprint: "${REFERENCE}" means nothing on the board itself
    lost = [d for d in r.b.GetDrawings() if d.GetClass() == "PCB_TEXT" and d.GetText() == "${REFERENCE}"]
    for d in lost:
        r.b.Remove(d)
    pcbroute._REMOVED.extend(lost)              # KiCad crashes if Python frees a removed item


# --------------------------------------------------------------------- nets ---
def nets(r):
    """Pad nets follow tile_circuit.py: the mux channels and U1's signal pins
    were chosen for the copper after the board was first laid out."""
    b, n = r.b, 0
    # KiCad's net name for each pin left open, as gen_sch.py read it back from the schematic
    unused = json.load(open(os.path.join(HERE, "pcb", "symbol_uuids.json")))["unconnected"]
    for fp in b.GetFootprints():
        part = tc.PARTS.get(fp.GetReference())
        if part is None:
            continue
        for p in fp.Pads():
            want = part.pins.get(p.GetNumber()) or unused[part.ref][p.GetNumber()].replace("/", "{slash}")
            if p.GetNetname() == want:
                continue
            net = b.FindNet(want)
            if net is None:
                net = pcbnew.NETINFO_ITEM(b, want)
                b.Add(net)
            p.SetNet(net)
            n += 1
    r.__init__(b, r.keepouts)                   # the router keeps each pad's net
    print("nets: %d pads renamed" % n)
    return n


# -------------------------------------------------------------------- lanes ---
# Where a lane cannot run at its pin's height all the way: AM3 sits low in its
# band, so its lower lanes would graze the keys underneath and its own capacitor.
LANE_Y = {(2, 0): 80.2, (2, 6): 79.65, (2, 4): 80.05}
LANE_JOG = {(2, 0): 89.7, (2, 6): 97.75, (2, 4): 98.0}        # x where such a lane turns to its pin


def lanes(r):
    """Rows 0, 1, 2 and 4.  A key above the band (odd column) leaves its output
    capacitor downward, a key below (even column) leaves the sensor upward; then
    one straight lane to the pin.  The channel order keeps them from crossing."""
    d = 0.6
    for m in ROT0:
        am = "AM%d" % (m + 1)
        for c in range(g.COLS):
            n = key_no(c, m)
            net = "HE%d" % n
            px, py = r.pad(am, tc.CHANNEL_PINS[tc.CHANNEL_OF_COL[m][c]])
            sx, sy = r.pad(cap_refs(n)[1], 1) if c % 2 else r.pad("HE%d" % n, 2)
            east = 1 if px > sx else -1
            ly = LANE_Y.get((m, c), py)
            if (m, c) == (4, 0):                # the power block is in the way: over its top
                pts = [(sx, sy), (sx, 24.15 + d), (sx + d, 24.15), (39.3 - d, 24.15), (39.3, 24.15 + d),
                       (39.3, ly - d), (39.3 + d, ly)]
            elif (m, c) == (2, 2):              # its lane is level with the sensor: leave by the capacitor
                cx, cy = r.pad(cap_refs(n)[1], 1)
                pts = [(cx, cy), (cx, ly + 0.3), (cx + 0.3, ly)]
            else:
                pts = [(sx, sy), (sx, ly - d if sy < ly else ly + d), (sx + east * d, ly)]
            if ly != py:
                jx = LANE_JOG[(m, c)]
                pts += [(jx, ly), (jx + east * abs(py - ly), py)]
            r.line(net, pts + [(px, py)])
    print("lanes: 32 sensor outputs")


# ---------------------------------------------------------------------- bus ---
# The front carries the mux lines up the middle of the board, between the pegs
# of columns 3 and 4.  x of each line where it runs straight:
BUS_X = {"MUX_OUT1": 90.6, "MUX_OUT2": 91.4, "MUX_OUT3": 92.2, "MUX_S2": 93.7, "MUX_S1": 94.5, "MUX_S0": 95.3}
SELECTS = [("MUX_S2", 9, 24), ("MUX_S1", 10, 25), ("MUX_S0", 11, 26)]     # net, mux pin, U1 pin; west to east
AM4_VIA_Y = {"MUX_S2": 48.0, "MUX_S1": 47.2, "MUX_S0": 48.0}  # AM4's select vias, north of its pins
OUT_VIA_X = 85.6                                # under U1: where the outputs come down from the front
OUT_VIA_Y = {5: 57.1, 3: 57.85, 2: 58.6, 1: 59.35}


def bus(r):
    """The three selects and the five mux outputs.  U1 hands the selects and
    MUX_OUT4 to AM4 beside it on the back; AM4's select pins pass them on to
    the front, which carries them north to AM5 and south to AM3, AM2, AM1.
    The other four outputs come to U1 on the front and drop onto its pins
    through vias under its body."""
    c = 0.3                                     # corner cut
    for i, (net, mpin, upin) in enumerate(SELECTS):
        # U1 -> AM4, nested Ls on the back, into AM4's pins from between its rows
        ux, uy = r.pad("U1", upin)
        ax, ay = r.pad("AM4", mpin)
        y = 50.8 + 0.35 * i
        r.line(net, [(ux, uy), (ux, y + c), (ux + c, y), (ax - c, y), (ax, y - c), (ax, ay)])
        # AM4's pin is the bridge to its via on the other side
        vy = AM4_VIA_Y[net]
        r.line(net, [(ax, ay), (ax, vy)])
        r.via(net, ax, vy)
        # the front: AM5 ... over AM4 ... AM1
        X = BUS_X[net]
        n5, s1 = r.pad("AM5", mpin)[1], r.pad("AM1", mpin)[1]
        jn, js = 43.7 - 0.2 * i, 56.2 - 0.5 * i
        r.line(net, [(X, n5), (X, jn), (ax, jn + X - ax), (ax, js), (X, js + X - ax), (X, s1)], "F")
        for m in ROT0:
            px, py = r.pad("AM%d" % (m + 1), mpin)
            r.via(net, X, py)
            r.line(net, [(X, py), (px, py)])
    # MUX_OUT4: straight across, into AM4's pin 3 from between its rows
    ux, uy = r.pad("U1", 27)
    ax, ay = r.pad("AM4", 3)
    r.line("MUX_OUT4", [(ux, uy), (ux, 53.75 + c), (ux + c, 53.75), (ax - c, 53.75), (ax, 53.75 + c), (ax, ay)])
    # the other outputs: a via beside pin 3, the front to U1, a via under U1, the pin
    vx = BUS_X["MUX_OUT3"]
    for m in ROT0:
        net = "MUX_OUT%d" % (m + 1)
        px, py = r.pad("AM%d" % (m + 1), 3)
        r.line(net, [(px, py), (vx, py)])
        r.via(net, vx, py)
        X = BUS_X.get(net, BUS_X["MUX_OUT1"])   # MUX_OUT5 comes from the north on MUX_OUT1's line
        oy = OUT_VIA_Y[m + 1]
        pts = [(vx, py)]
        if X != vx:                             # step west, clear of the vias further along
            jy = py + 1.2 if m == 4 else py - 5.0
            pts += [(vx, jy), (X, jy + (X - vx) * (-1 if m == 4 else 1))]
        pts += [(X, oy + (c if py > oy else -c)), (X - c, oy), (OUT_VIA_X, oy)]
        r.line(net, pts, "F")
        r.via(net, OUT_VIA_X, oy)
        ux, uy = r.pad("U1", pin_of("U1", net))
        r.line(net, [(OUT_VIA_X, oy), (ux + c, oy), (ux, oy - c), (ux, uy)])
    print("bus: 3 selects, 5 mux outputs")


# ------------------------------------------------------------------- lanes3 ---
def lanes3(r):
    """Row 3: its mux stands across the band, beside U1, so the lanes come to
    its two pin rows from outside: the four western keys round its north-west
    corner into the top row, the four eastern ones along the band into the
    bottom row (one into the end pin, three from below)."""
    c = 0.3

    def pin(c_):
        return r.pad("AM4", tc.CHANNEL_PINS[tc.CHANNEL_OF_COL[3][c_]])

    def he(c_):
        n = key_no(c_, 3)
        return "HE%d" % n, r.pad("HE%d" % n, 2), r.pad(cap_refs(n)[1], 1)

    # west.  HE19 is level with the pins' far side already: over its right peg, down into its pin
    net, (sx, sy), _ = he(3)
    px, py = pin(3)
    r.line(net, [(sx, sy), (sx, 44.6 + c), (sx + c, 44.6), (px - c, 44.6), (px, 44.6 + c), (px, py)])
    # the other three: along the band, up at AM4's corner, over the select vias, down
    for i, c_ in enumerate([1, 0, 2]):          # north to south in the band
        net, (sx, sy), _ = he(c_)
        px, py = pin(c_)
        ly, rx, ty = 48.7 + 0.35 * i, 89.9 + 0.35 * i, 45.9 + 0.35 * i
        if c_ == 1:                             # from above: over its right peg, down beside the W port
            pts = [(sx, sy), (sx, 44.6 + c), (sx + c, 44.6), (47.75 - c, 44.6), (47.75, 44.6 + c),
                   (47.75, ly - c), (47.75 + c, ly)]
        elif c_ == 0:                           # under D1, between the W port's tab and its VIN pin
            pts = [(sx, sy), (sx, 49.57 + c), (sx + c, 49.57), (49.6, 49.57), (49.6 + 49.57 - ly, ly)]
        else:
            pts = [(sx, sy), (sx, ly + c), (sx + c, ly)]
        r.line(net, pts + [(rx - c, ly), (rx, ly - c), (rx, ty + c), (rx + c, ty), (px - c, ty), (px, ty + c), (px, py)])
    # east.  In the band north to south: HE29, HE39, HE34, HE24, all north of HE24's sensor
    for i, c_ in enumerate([5, 7, 6, 4]):
        net, (sx, sy), (cx, cy) = he(c_)
        px, py = pin(c_)
        ly = py + 0.35 * i
        if c_ == 5:
            pts = [(cx, cy), (cx, ly - c), (cx - c, ly)]
        elif c_ == 7:                           # between the E port's tab and its VIN pin, then down to its lane
            pts = [(cx, cy), (cx, 49.42 - c), (cx - c, 49.42), (152.0, 49.42), (152.0 - (ly - 49.42), ly)]
        else:
            pts = [(sx, sy), (sx, ly + c), (sx - c, ly)]
        if i == 0:                              # into the end pin from the side
            r.line(net, pts + [(px, py)])
            continue
        uy = py + 0.725 + 0.35 * i              # under the pin row
        jx = 97.05 + 0.15 * i                   # step down east of AM4, the diagonals side by side
        r.line(net, pts + [(jx + uy - ly, ly), (jx, uy), (px + c, uy), (px, uy - c), (px, py)])
    print("lanes3: 8 sensor outputs")


# ------------------------------------------------------------------- supply ---
# +3.3VA is a ladder on the back: a spine down each side edge and a rung in
# every band that has no mux in it.  A rung feeds the keys on both its sides.
RUNG = {4: 40.0, 3: 64.0, 2: 88.5, 1: 115.6}    # y of the rung south of even-column row m (north of odd-column row m - 1)
SPINE_W, SPINE_E = 13.9, 173.8
MUX_FEED_X = 97.5                               # the muxes' supply comes up beside them from the nearest rung
A = "+3.3VA"


def supply(r):
    """The analog supply: from the ferrite's capacitor to every sensor, mux
    and U1's VDDA.  The keys in the tabs are across a mux band from any rung:
    theirs comes over on the front.  U1 sits in the middle of a rung, so that
    rung is in two parts and HE18, under U1, is fed round its west side."""
    c = 0.3
    small = dict(size=0.6, drill=0.3)

    def cap(n):
        return r.pad(cap_refs(n)[0], 1)

    def sens(n):
        return r.pad("HE%d" % n, 1)

    # trunk: out of the power block between it and HE5's lane, down to the top rung, which runs to the east spine
    fx, fy = r.pad("C103", 1)
    r.line(A, [(fx, fy), (fx + 0.4, fy - 0.4), (38.4 - c, fy - 0.4), (38.4, fy - 0.4 + c), (38.4, RUNG[4] - c),
               (38.4 + c, RUNG[4]), (SPINE_E, RUNG[4])])
    # east spine, HE40 at its top
    sx, sy = sens(40)
    r.line(A, [(sx, sy), (sx, 19.5 + c), (sx + c, 19.5), (SPINE_E - c, 19.5), (SPINE_E, 19.5 + c),
               (SPINE_E, RUNG[1] - c), (SPINE_E - c, RUNG[1]), (SPINE_W, RUNG[1])])
    # west spine: HE5's lane fences the power block in, so over it on the front; HE1 at its foot
    bx, by = r.pad("FB1", 2)
    hy = 24.9
    r.line(A, [(bx, by), (bx, hy)])
    r.via(A, bx, hy)
    r.line(A, [(bx, hy), (SPINE_W, hy)], "F")
    r.via(A, SPINE_W, hy)
    x, y = cap(1)
    r.line(A, [(SPINE_W, hy), (SPINE_W, 128.4 - c), (SPINE_W + c, 128.4), (x - c, 128.4), (x, 128.4 + c), (x, y)])
    x, y = cap(5)
    r.line(A, [(SPINE_W, 32.3), (x - c, 32.3), (x, 32.3 + c), (x, y)])
    # the other rungs
    r.line(A, [(SPINE_W, RUNG[2]), (SPINE_E, RUNG[2])])
    x, y = cap(24)
    r.line(A, [(SPINE_E, RUNG[3]), (x + c, RUNG[3]), (x, RUNG[3] - c), (x, y)])
    wx = 69.5                                   # west of the crystal
    r.line(A, [(SPINE_W, RUNG[3]), (wx, RUNG[3])])
    # U1's VDDA and its two capacitors, from the west rung's end
    x, y = r.pad("C90", 1)
    r.line(A, [(wx, RUNG[3]), (wx, 57.3 + c), (wx + c, 57.3), (x - c, 57.3), (x, 57.3 + c), (x, y), ("C91", 1)])
    r.line(A, [(x, y), ("U1", 13)], w=0.25)
    # HE18: on round U1's south-west corner
    x, y = cap(18)
    r.line(A, [(wx, RUNG[3]), (wx, 72.6 - c), (wx + c, 72.6), (x - c, 72.6), (x, 72.6 - c), (x, y)])
    # the keys
    hops = 0
    for c_ in range(g.COLS):
        for m in range(g.ROWS):
            n = key_no(c_, m)
            if n in (1, 5, 18, 22, 24, 40):
                continue
            if c_ % 2 == 0 and m:               # the rung is south of it: into the capacitor
                x, y = cap(n)
                r.line(A, [(x, y), (x, RUNG[m])])
            elif c_ % 2 and m < 4:              # north of it: into the sensor
                x, y = sens(n)
                r.line(A, [(x, y), (x, RUNG[m + 1])])
            else:                               # a tab key: via beside its capacitor, the front, via on the rung
                x, y = cap(n)
                vy, ry = (128.6, RUNG[1]) if m == 0 else (22.7, RUNG[4])
                r.line(A, [(x, y), (x, vy)])
                r.via(A, x, vy, **small)
                r.line(A, [(x, vy), (x, ry)], "F")
                r.via(A, x, ry, **small)
                hops += 1
    # the muxes.  AM5, AM3, AM2: pin 16 to its capacitor, and a feed from the rung beside the mux
    X = MUX_FEED_X
    px, py = r.pad("AM5", 16)
    r.line(A, [(px, py), ("C85", 1)], w=0.3)
    r.line(A, [(X, py), (X, RUNG[4])])
    px, py = r.pad("AM3", 16)
    cx, cy = r.pad("C83", 1)
    r.line(A, [(px, py), (cx - (py - cy), py), (cx, cy)], w=0.3)
    r.line(A, [(X, py), (X, RUNG[2])])
    px, py = r.pad("AM2", 16)
    cx, cy = r.pad("C82", 1)
    r.line(A, [(px, py), (X, py)], w=0.3)
    r.line(A, [(X, py), (X, RUNG[1])])
    r.line(A, [(X, cy), (cx, cy)], w=0.3)
    # HE22 has the S port between it and its rung: from AM2's capacitor
    x, y = cap(22)
    r.line(A, [(cx, cy), (cx, 103.3 + c), (cx + c, 103.3), (x - c, 103.3), (x, 103.3 + c), (x, y)], w=0.3)
    # AM1: from HE21's hop
    x, y = cap(21)
    cx, cy = r.pad("C81", 1)
    r.line(A, [(x, 128.6), (x - c, 128.3), (cx + c, 128.3), (cx, 128.3 - c), (cx, cy)])
    r.line(A, [(cx, cy), ("AM1", 16)], w=0.3)
    # AM4: the N port's VIN crosses below the rung, so over it on the front
    cx, cy = r.pad("C84", 1)
    vx = 98.1
    r.via(A, vx, RUNG[4], **small)
    r.line(A, [(vx, RUNG[4]), (vx, cy)], "F")
    r.via(A, vx, cy, **small)
    r.line(A, [(vx, cy), (cx, cy)])
    r.line(A, [(cx, cy), ("AM4", 16)], w=0.3)
    print("supply: +3.3VA to 40 sensors, 5 muxes and VDDA; %d hops on the front" % (hops + 2))


# ---------------------------------------------------------------------- vin ---
VIN_Y = 42.6                                    # the trunk: in the band north of row 3, south of its rung
VIN_STEM_X = 96.9                               # the stem to the S port: just east of the mux lines
VIN_BRIDGE = (84.0, 85.1, 110.5, 111.6)         # x of the vias at the ends of the trunk's stretch on the back
VIN_S_JOIN = 113.4                              # y of the track joining the S port's two VIN pins


def vin(r):
    """VIN joins the four ports and feeds D1.  The trunk runs west to east on
    the front (the back of that band belongs to the sensors' supply), comes
    down to the back to pass under the mux lines and through the N port's
    pins, and a stem runs down the middle of the front to the S port.  What
    passes through a tile is the current of every tile further on, so there
    are two vias wherever that changes layer."""
    V, y = "VIN", VIN_Y
    # D1: only this tile's own current
    dx, dy = r.pad("D1", 1)
    r.line(V, [(dx, dy), (30.4, dy)])
    r.via(V, 30.4, dy)
    r.line(V, [(30.4, dy), (30.4 + dy - y, y), (VIN_BRIDGE[1], y)], "F")
    # W: the two pins joined west of them; HE4's lane is close above pin 6.  The drop to them stops
    # well short of the +3V3 trunk, to leave the ground plane a way through
    (x1, y1), (x6, y6) = r.pad("J4", 1), r.pad("J4", 6)
    jx, vx = 46.55, 44.5
    r.line(V, [(x6, y6), (jx, y6), (jx, y6 + 0.5)], w=0.6)
    r.line(V, [(jx, y6 + 0.5), (jx, y1), (x1, y1)])
    for vy in (51.3, 52.4):
        r.line(V, [(jx, vy), (vx, vy)])
        r.via(V, vx, vy)
    r.line(V, [(vx, 52.4), (vx, y)], "F")
    # N: the back, under the mux lines, a branch down into each pin
    for x in VIN_BRIDGE:
        r.via(V, x, y)
    r.line(V, [(VIN_BRIDGE[0], y), (VIN_BRIDGE[3], y)])
    for pin in (1, 6):
        px, py = r.pad("J3", pin)
        r.line(V, [(px, y), (px, py)])
    # E: down the west side of the port, a via beside each pin; HE39's lane is close above pin 1
    (x1, y1), (x6, y6) = r.pad("J2", 1), r.pad("J2", 6)
    ex = 155.3
    r.line(V, [(VIN_BRIDGE[2], y), (ex - 0.5, y), (ex, y + 0.5), (ex, max(y1, y6))], "F")
    for px, py in ((x1, y1), (x6, y6)):
        r.via(V, ex, py)
        r.line(V, [(ex, py), (px, py)], w=0.6)
    # S: the stem, then the two pins joined behind them, far enough behind for a ground via at each ground pin
    sx = VIN_STEM_X
    r.line(V, [(sx, y), (sx, y + 1.1)])
    r.via(V, sx, y + 1.1)
    r.via(V, sx, y)
    (x1, y1), (x6, y6) = r.pad("J5", 1), r.pad("J5", 6)
    vx = 99.8
    r.line(V, [(sx, y), (sx, y6 - (vx - sx)), (vx, y6)], "F")
    r.via(V, vx, y6)
    r.via(V, vx - 0.9, y6 - 0.9)
    r.line(V, [(vx - 0.9, y6 - 0.9), (vx, y6), (x6, y6), (x6, VIN_S_JOIN), (x1, VIN_S_JOIN), (x1, y1)])
    print("vin: D1 and the four ports")


# -------------------------------------------------------------------- uarts ---
def series(net):
    """The series resistor on a net: its ref, its pad on that net, its other pad."""
    ref = next(p.ref for p in tc.PARTS.values() if p.ref.startswith("R") and net in p.pins.values())
    a = pin_of(ref, net)
    return ref, a, next(k for k in tc.PARTS[ref].pins if k != a)


def uarts(r):
    """Two data lines per port: U1 pin, the back, a via, the front to the
    port, a via, series resistor, port pin.  W leaves U1's north-west corner;
    N and E its east side and S its south side, fanned out on the back
    between the debug pads (the mux lines and VIN wall off the front there)
    to vias east of them."""
    c = 0.3

    def via(net, x, y):
        r.via(net, x, y)
        return x, y

    def port(edge, t, vx, vy, bend=()):
        """From the via beside the port: into the resistor, out of it to the port's pin."""
        ref, a, b = series("%s_%s" % (edge, t))
        r.line("%s_%s" % (edge, t), [(vx, vy), (ref, a)])
        ab = "%s_%s" % (edge, "A" if t == "TX" else "B")
        r.line(ab, [(ref, b)] + list(bend) + [(tc.PORT[edge], pin_of(tc.PORT[edge], ab))])

    def u1(net):
        return r.pad("U1", pin_of("U1", net))

    # W: over the keys west of U1
    ux, uy = u1("W_TX")
    y = r.pad(series("W_TX")[0], 1)[1]
    r.line("W_TX", [(ux, uy), (76.4 + c, uy), (76.4, uy - c), (76.4, 54.6)])
    via("W_TX", 76.4, 54.6)
    r.line("W_TX", [(76.4, 54.6), (76.4 - (54.6 - y), y), (52.4, y)], "F")
    port("W", "TX", *via("W_TX", 52.4, y))
    ux, uy = u1("W_RX")
    y = r.pad(series("W_RX")[0], 1)[1]
    r.line("W_RX", [(ux, uy), (ux, 53.4)])
    via("W_RX", ux, 53.4)
    r.line("W_RX", [(ux, 53.4), (ux - (53.4 - y), y), (52.4, y)], "F")
    port("W", "RX", *via("W_RX", 52.4, y))

    # N and E: four tracks side by side off U1's east side, N north of the GND pad of J1, E south of it
    for i, (net, ly, vx) in enumerate([("N_TX", 59.65, 100.4), ("N_RX", 60.35, 101.05),
                                       ("E_TX", 61.05, 101.05), ("E_RX", 61.45, 100.3)]):
        ux, uy = u1(net)
        x = 89.9 - 0.05 * i
        pts = [(ux, uy), (x, uy), (x + ly - uy, ly)]
        if net[0] == "E":                       # step down to pass under the pad
            x, y = (92.35, 62.35) if net == "E_TX" else (92.15, 62.75)
            pts += [(x, ly), (x + y - ly, y)]
            ly = y
        if net == "E_RX":                       # its via sits south-west of E_TX's
            pts += [(vx - 0.5, ly)]
            ly += 0.5
        r.line(net, pts + [(vx, ly)])
        via(net, vx, ly)
        if net[0] == "N":                       # the front: north past HE24's peg, then over to the resistor
            rx = r.pad(series(net)[0], 1)[0]
            r.line(net, [(vx, ly), (vx, 52.3 + rx - vx), (rx, 52.3)], "F")
            port("N", net[2:], *via(net, rx, 52.3))
        else:                                   # east along the band, north beside the port
            px = 152.4 if net == "E_TX" else 153.0
            y = r.pad(series(net)[0], 1)[1]
            r.line(net, [(vx, ly), (px - c, ly), (px, ly - c), (px, y)], "F")
            port("E", net[2:], *via(net, px, y))

    # S: under U1's south side, down between the debug pads' rows, south on the front
    ux, uy = u1("S_TX")
    r.line("S_TX", [(ux, uy), (ux, 68.0 - c), (ux + c, 68.0), (88.95, 68.0), (90.95, 70.0), (101.4, 70.0)])
    via("S_TX", 101.4, 70.0)
    x, y = r.pad(series("S_TX")[0], 1)
    vx = 105.5
    r.line("S_TX", [(101.4, 70.0), (101.4, y - (vx - 101.4)), (vx, y)], "F")
    ax, ay = r.pad(tc.PORT["S"], pin_of(tc.PORT["S"], "S_A"))
    x2, y2 = r.pad(series("S_TX")[0], series("S_TX")[2])
    port("S", "TX", *via("S_TX", vx, y),
         bend=[(x2 - 0.6, y2 + 0.6), (ax + c, y2 + 0.6), (ax, y2 + 0.6 + c)])
    ux, uy = u1("S_RX")
    r.line("S_RX", [(ux, uy), (ux, 68.4 - c), (ux + c, 68.4), (88.8, 68.4), (90.8, 70.4), (99.85, 70.4),
                    (100.2, 70.75), (100.6, 70.75)])
    via("S_RX", 100.6, 70.75)
    x, y = r.pad(series("S_RX")[0], 1)
    r.line("S_RX", [(100.6, 70.75), (100.6, y)], "F")
    bx, by = r.pad(tc.PORT["S"], pin_of(tc.PORT["S"], "S_B"))
    x2, y2 = r.pad(series("S_RX")[0], series("S_RX")[2])
    port("S", "RX", *via("S_RX", 100.6, y),
         bend=[(x2 + 0.5, y2 + 0.5), (bx - c, y2 + 0.5), (bx, y2 + 0.5 + c)])
    print("uarts: 4 ports, 8 lines")


# ---------------------------------------------------------------------- mcu ---
U1_GND_VIA = {12: (79.3, 58.85), 31: (86.451, 56.3), 63: (79.951, 64.6)}      # U1's ground pins: a via each, under the body


def mcu(r):
    """What U1 needs besides its signals: +3V3, the crystal, reset, BOOT0, the
    debug pads, the pull-downs and its ground pins."""
    c = 0.3
    small = dict(size=0.6, drill=0.3)
    P = "+3V3"
    # pull-downs beside their pins
    r.line("OTGHS_R", [("U1", 33), ("R4", 1)])
    r.line("PA11_PD", [("U1", 44), ("R2", 1)])
    (x, y), (rx, ry) = r.pad("U1", 45), r.pad("R3", 1)
    r.line("PA12_PD", [(x, y), (rx - (ry - y), y), (rx, ry)])
    # the debug pads, in the order the tracks leave U1: SWDIO, NRST, SWCLK, BOOT0 from north to south
    r.line("SWDIO", [("U1", 46), (89.7, 63.35), (90.0, 63.65), (90.0, 64.3), (90.4, 64.7), (92.5, 64.7),
                     (93.16, 64.04), ("J1", 5)])
    x, y = r.pad("U1", 7)
    r.line("NRST", [(x, y), ("C92", 1)])
    jx, jy = r.pad("J1", 4)                     # under U1, out between its south-east corner pins
    r.line("NRST", [(x, y), (83.951, y), (83.951 + jy - y, jy), (jx, jy)])
    x, y = r.pad("U1", 49)
    jx, jy = r.pad("J1", 3)
    r.line("SWCLK", [(x, y), (x, 67.6 - c), (x + c, 67.6), (93.47 - (jy - 67.6), 67.6), (93.47, jy), (jx, jy)])
    x, y = r.pad("U1", 60)
    jx, jy = r.pad("J1", 2)
    r.line("BOOT0", [(x, y), (x, 68.2 - c), (x + c, 68.2), (84.6, 68.2), (85.2, 68.8), (88.65, 68.8),
                     (88.65 + jy - 68.8, jy), (jx, jy)])
    # BOOT0's pull-down is across pin 64's supply: over it on the front
    rx, ry = r.pad("R1", 1)
    r.line("BOOT0", [(x, y), (x, 64.7)])
    r.via("BOOT0", x, 64.7)
    r.line("BOOT0", [(x, 64.7), (x, ry - c), (x - c, ry), (77.4, ry)], "F")
    r.via("BOOT0", 77.4, ry)
    r.line("BOOT0", [(77.4, ry), (rx, ry)])
    # the crystal and its capacitors
    x, y = r.pad("U1", 6)
    r.line("HEXT_OUT", [(x, y), (76.63, y), (76.5, y + 0.13), (73.73, y + 0.13), (73.4, y - 0.2), ("Y1", 3)])
    r.line("HEXT_OUT", [("Y1", 3), ("C94", 1)])
    x, y = r.pad("U1", 5)
    r.line("HEXT_IN", [(x, y), (72.1, y), (71.5, y + 0.6), ("Y1", 1)])
    r.line("HEXT_IN", [("Y1", 1), ("C93", 1)])
    # +3V3: out of the power block on the front, along the band north of the crystal, in under U1
    x, y = r.pad("C102", 1)
    ty, tx, ey, jx = 56.0, 84.0, 60.3, 89.9
    r.line(P, [(x, y), (x, 30.2)])
    r.via(P, x, 30.2)
    jy = r.pad("J1", 1)[1]
    r.line(P, [(x, 30.2), (x, ty - c), (x + c, ty), (tx - c, ty), (tx, ty + c), (tx, ey - c), (tx + c, ey),
               (jx - c, ey), (jx, ey + c), (jx, jy)], "F")
    r.via(P, jx, jy)                            # ... on down U1's east side to the debug pad
    r.line(P, [(jx, jy), ("J1", 1)])
    r.via(P, 87.0, ey)                          # pin 36 and its capacitor
    r.line(P, [(87.0, ey), (87.0, 58.65), (87.3, 58.35), ("U1", 36)], w=0.25)
    r.line(P, [("U1", 36), ("C87", 1)], w=0.25)
    bx, by = r.pad("C89", 1)                    # a branch to the bulk capacitor, pin 1 and pin 64
    r.line(P, [(bx, ty), (bx, 67.25)], "F")
    r.via(P, bx, 67.25)
    r.line(P, [(bx, by), (bx, 67.25)])
    x, y = r.pad("C86", 1)
    r.line(P, [("U1", 1), (x, y)], w=0.25)
    r.line(P, [(x, y), (x, by), (bx, by)], w=0.4)
    x, y = r.pad("U1", 64)                      # pin 64's capacitor is past HE18's peg: a thin track between peg and C35
    r.line(P, [(x, y), (x, 66.8), (79.4, 66.851), (79.4, 70.244), ("C88", 1)], w=0.2)
    r.line(P, [(bx, 67.25), (bx + 0.2, 67.45), (79.4, 67.45)], w=0.3)
    # ground pins: each to a via under the body
    for pin, (vx, vy) in U1_GND_VIA.items():
        r.line("GND", [("U1", pin), (vx, vy)], w=0.25)
        r.via("GND", vx, vy, **small)
    print("mcu: +3V3, crystal, reset, BOOT0, debug pads, pull-downs")


# ---------------------------------------------------------------------- gnd ---
COMPASS = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0),
           "NE": (1, -1), "NW": (-1, -1), "SE": (1, 1), "SW": (-1, 1)}
SMALL_FP = (tc.FP_C0402, tc.FP_R0402)


def parts(ps):
    """The outlines of a polygon set, each with its holes and its box: [(set, x0, y0, x1, y1)]."""
    out = []
    for i in range(ps.OutlineCount()):
        one = pcbnew.SHAPE_POLY_SET()
        one.AddOutline(ps.Outline(i))
        for h in range(ps.HoleCount(i)):
            one.AddHole(ps.Hole(i, h))
        bb = one.BBox()
        out.append((one, mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())))
    return out


def pours(b):
    """The copper of the two ground pours, island by island, as it would be if
    none were dropped for want of a connection: {"F": [...], "B": [...]}."""
    zones = [z for z in b.Zones() if not z.GetIsRuleArea()]
    modes = [z.GetIslandRemovalMode() for z in zones]
    for z in zones:
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_NEVER)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    out = {l: [isl for z in zones if z.IsOnLayer(layer) for isl in parts(z.GetFilledPolysList(layer))]
           for l, layer in pcbroute.LAYER.items()}
    for z, m in zip(zones, modes):
        z.SetIslandRemovalMode(m)
    return out


def shrunk(poly, by, back=0.0):
    """A pour's copper drawn in by `by` mm all round, then let out again by `back`."""
    p = pcbnew.SHAPE_POLY_SET(poly)
    p.Unfracture()                              # KiCad keeps a fill's holes as slits; drawn in, a slit would cut
    p.Deflate(pcbnew.FromMM(by), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
    if back:
        p.Inflate(pcbnew.FromMM(back), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
    return p


def inside(poly, x, y):
    return poly.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))


def gnd(r):
    """Ground is the pour on both sides.  The front's is nearly whole; the
    back's is cut into cells by the supply rungs and the lanes, and some pads
    sit where no pour reaches.  So every ground pad gets a via to the front
    plane as near as one fits: each sensor, each capacitor, the muxes, the
    ports, and what stands round U1.  Where tracks wall the front in (east of
    U1), the pads are joined on the back to the nearest place that has one."""
    G = "GND"
    before = len(r.added)
    pour = pours(r.b)
    front = max(range(len(pour["F"])), key=lambda i: pour["F"][i][0].Area())
    rim = [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in range(0, 360, 30)]

    def pour_at(l, x, y, rad):
        """The island of a layer's pour that a disc lies in, whole; None if there is none."""
        pts = [(x, y)] + [(x + rad * cx, y + rad * cy) for cx, cy in rim]
        for i, (isl, x0, y0, x1, y1) in enumerate(pour[l]):
            if x0 <= x <= x1 and y0 <= y <= y1 and \
                    all(isl.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(px), pcbnew.FromMM(py))) for px, py in pts):
                return i
        return None

    def bends(a, b):
        """The ways from a to b with one bend at most, all at 45 degrees."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        k = min(abs(dx), abs(dy))
        if k < 1e-6 or abs(abs(dx) - abs(dy)) < 1e-6:
            return [[a, b]]
        ux, uy = math.copysign(k, dx), math.copysign(k, dy)
        return [[a, (round(b[0] - ux, 3), round(b[1] - uy, 3)), b], [a, (round(a[0] + ux, 3), round(a[1] + uy, 3)), b]]

    def stitch(at, *groups, far=2.0, w=0.3, size=0.6, drill=0.3):
        """A via into the front plane as near a pad as one fits, and a track to it.
        at: the pad, ("C7", 2), or a point in it.  groups: compass points, "N S" then
        "NE SE" ...: the nearest fit of the first group that has one, straight out of
        the pad.  If there is none: the nearest that a track with one bend reaches."""
        x, y = r.pad(*at) if isinstance(at[0], str) else at
        n = int(round(far / 0.05))
        straight = [[(x, y), (round(x + ux * d, 3), round(y + uy * d, 3))] for group in groups
                    for d in (0.05 * i for i in range(8, n + 1)) for ux, uy in (COMPASS[k] for k in group.split())]
        n = int(round((far + 0.5) / 0.1))
        ends = sorted((max(abs(i), abs(j)) + 0.414 * min(abs(i), abs(j)), i, j) for i in range(-n, n + 1)
                      for j in range(-n, n + 1) if i and j and abs(i) != abs(j))
        bent = [path for _, i, j in ends for path in bends((x, y), (round(x + 0.1 * i, 3), round(y + 0.1 * j, 3)))]
        for paths in straight, bent:
            for room in 0.05, 0.0:              # rather not right at the limit
                for path in paths:
                    vx, vy = path[-1]
                    if r.via_ok(G, vx, vy, size, room) and pour_at("F", vx, vy, size / 2 + room) == front \
                            and r.track_ok(G, path, w=w):
                        r.line(G, path, w=w)
                        r.via(G, vx, vy, size=size, drill=drill)
                        return vx, vy
        return None

    def join(ref, pin, far=4.0, w=0.3):
        """A track from a pad to the nearest pad or via that is tied to the plane."""
        x, y = r.pad(ref, pin)
        for d, tx, ty in sorted((math.hypot(tx - x, ty - y), tx, ty) for tx, ty in tied):
            for path in bends((x, y), (tx, ty)) if d <= far else ():
                if r.track_ok(G, path, w=w):
                    r.line(G, path, w=w)
                    return tx, ty
        return None

    def outward(ref, pin):
        """Compass points for one pad of a two-pad part: straight out of the part, sideways, out at an angle."""
        (x, y), (ox, oy) = r.pad(ref, pin), r.pad(ref, next(k for k in tc.PARTS[ref].pins if k != str(pin)))
        if abs(x - ox) > abs(y - oy):
            o = "E" if x > ox else "W"
            return o, "N S", "N%s S%s" % (o, o)
        o = "S" if y > oy else "N"
        return o, "E W", "%sE %sW" % (o, o)

    done, missed, tied = set(), [], []
    ANY = ("N S E W", "NE NW SE SW")
    big = dict(size=0.8, drill=0.4, w=0.5)

    def pad(ref, pin, *groups, **kw):
        done.add((ref, str(pin)))
        at = stitch((ref, pin), *(groups or outward(ref, pin)), **kw)
        if at is None:
            missed.append((ref, pin))
        else:
            tied.extend([at, r.pad(ref, pin)])
        return at

    # --- east of U1 the front is all mux lines.  AM4's three ground pins, the bias resistor and pin 36's
    # capacitor hang on a track to the via of U1's pin 31, which stands in the plane north of U1
    (x8, y8), (x6, _) = r.pad("AM4", 8), r.pad("AM4", 6)
    rx, ry = r.pad("R4", 2)
    vx, vy = U1_GND_VIA[31]
    jy, hy = y8 + 0.4, 56.15                    # the pins' join; the track's run between U1's corner pins
    r.line(G, [(x8, jy), (x6, jy)], w=0.3)
    r.line(G, [(rx, jy), (rx, ry)], w=0.3)
    r.line(G, [(vx, vy), (vx + vy - hy, hy), (x8 - (hy - jy - 0.25), hy), (x8, jy + 0.25), (x8, jy)], w=0.25)
    cx, cy = r.pad("C87", 2)
    r.line(G, [(rx, ry), (rx, cy - 0.2), (cx, cy)], w=0.3)
    # the two pull-downs are fenced in by the E port's lines: a via in the strip of plane between two mux lines
    (ax, ay), (bx, by) = r.pad("R2", 2), r.pad("R3", 2)
    sx = (BUS_X["MUX_OUT3"] + BUS_X["MUX_S2"]) / 2
    r.line(G, [(ax, ay), (bx, by)], w=0.3)
    r.line(G, [(ax, by - 0.55), (sx, by - 0.55)], w=0.3)
    r.via(G, sx, by - 0.55, size=0.6, drill=0.3)
    done.update({("AM4", "6"), ("AM4", "7"), ("AM4", "8"), ("R4", "2"), ("C87", "2"), ("R2", "2"), ("R3", "2")})
    done.update(("U1", str(pin)) for pin in U1_GND_VIA)

    # --- the other muxes: the three ground pins joined, a via beside them
    for m in ROT0:
        am = "AM%d" % (m + 1)
        r.line(G, [(am, 6), (am, 8)], w=0.3)
        pad(am, 7, "W", "NW SW", far=2.6)
        done.update({(am, "6"), (am, "8")})

    # --- the ports: what goes out on VIN comes back here.  A via as big as VIN's at each ground pin,
    # and one at each of the shell's tabs
    for edge, way in (("N", ("N",)), ("S", ("S",)), ("W", ("E", "NE SE")), ("E", ("E", "NE SE"))):
        j = tc.PORT[edge]
        for pin in (2, 5):
            pad(j, pin, *way, far=3.0, **big)
        done.add((j, "MP"))
        for p in r.b.FindFootprintByReference(j).Pads():
            if p.GetNumber() == "MP":
                assert stitch((mm(p.GetPosition().x), mm(p.GetPosition().y)), *ANY, far=3.0), "no via at a tab of " + j
    for way in "EW":
        pad("D1", 2, way, far=3.0, **big)

    # --- the keys: the sensor's ground pin and both capacitors
    for n in range(1, len(tc.KEYS) + 1):
        cv, co = cap_refs(n)
        got = [pad("HE%d" % n, 3, "S", "SE SW", "E W", far=2.5), pad(cv, 2), pad(co, 2)]
        assert any(got), "no ground via at key %d" % n

    # --- everything else with a ground pad
    for ref, part in tc.PARTS.items():
        for pin, net in part.pins.items():
            if net != G or (ref, pin) in done:
                continue
            if len(part.pins) != 2:
                pad(ref, pin, *ANY)
            elif part.footprint in SMALL_FP:
                pad(ref, pin)
            else:                               # the regulators' capacitors: big vias, two where the part is big
                pad(ref, pin, **big)
                if part.footprint != tc.FP_C0603:
                    stitch((ref, pin), *ANY, **big)
    # --- the pads that no via fits at: a track to the nearest that has one
    joined = [(ref, pin) for ref, pin in missed if join(ref, pin)]
    left = ["%s.%s" % m for m in missed if m not in joined]
    print("gnd: %d vias, %d pads joined to a neighbour's%s" % (
        sum(1 for a in r.added[before:] if a[0] == "C"), len(joined),
        "; left to the pour alone: " + ", ".join(left) if left else ""))


# --------------------------------------------------------------------- ties ---
TIE_NECK = 0.5              # where a pour is narrower than this, it counts as two cells
TIE_REACH = 8.0             # no place of a cell is farther than this from a via, if one fits there
TIE_AREA = 3.0              # mm2: smaller cells are left alone
TIE_PITCH = 0.25


def ties(r):
    """The two pours tied to each other.  The tracks cut each pour into cells
    (mostly the back's); after gnd() some have no via, and long ones have one
    at one end.  Here every cell gets a via to the other side's pour wherever
    it is more than TIE_REACH from the next, if one fits."""
    G = "GND"
    pour = pours(r.b)
    cell = {l: [c for isl in pour[l] for c in parts(shrunk(isl[0], TIE_NECK / 2, TIE_NECK / 2))] for l in "FB"}
    area = {(l, i): c[0].Area() / 1e12 for l in "FB" for i, c in enumerate(cell[l])}
    rim = [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in range(0, 360, 30)]

    def cells_at(l, x, y, rad=0.0):
        """The cells of a layer that a disc touches."""
        pts = [(x, y)] + [(x + rad * cx, y + rad * cy) for cx, cy in rim if rad]
        return [i for i, (c, x0, y0, x1, y1) in enumerate(cell[l])
                if x0 - rad <= x <= x1 + rad and y0 - rad <= y <= y1 + rad and any(inside(c, *q) for q in pts)]

    # the places for a via: where both pours are, with room to spare
    core = {l: [shrunk(isl[0], 0.3 + 0.15) for isl in pour[l]] for l in "FB"}
    spots, of = [], {}                          # (x, y, front cell, back cell); cell -> its spots
    for fc in core["F"]:
        for bc in core["B"]:
            both = pcbnew.SHAPE_POLY_SET(bc)
            both.BooleanIntersection(fc)
            for one, x0, y0, x1, y1 in parts(both):
                n0, n1 = math.ceil(x0 / TIE_PITCH), math.floor(x1 / TIE_PITCH)
                m0, m1 = math.ceil(y0 / TIE_PITCH), math.floor(y1 / TIE_PITCH)
                pts = [(n * TIE_PITCH, m * TIE_PITCH) for n in range(n0, n1 + 1) for m in range(m0, m1 + 1)
                       if inside(one, n * TIE_PITCH, m * TIE_PITCH)]
                if not pts:
                    continue
                f, k = cells_at("F", *pts[0]), cells_at("B", *pts[0])
                assert len(f) == 1 and len(k) == 1, "a place in %d front and %d back cells" % (len(f), len(k))
                for x, y in pts:
                    of.setdefault(("F", f[0]), []).append(len(spots))
                    of.setdefault(("B", k[0]), []).append(len(spots))
                    spots.append((x, y, f[0], k[0]))
    has = {}                                    # cell -> the ground vias in it
    for o in r.obs["B"]:
        if o[0] == "C" and o[1] == G:
            for l in "FB":
                for i in cells_at(l, o[2], o[3], o[4]):
                    has.setdefault((l, i), []).append((o[2], o[3]))

    n = 0
    for key in sorted(of, key=lambda k: (k[0] != "B", -area[k])):       # the back's cells first, big ones first
        if area[key] < TIE_AREA:
            continue
        mine = of[key]
        xy = np.array([spots[k][:2] for k in mine])
        near = np.full(len(mine), np.inf)       # each spot's distance to the nearest via of this cell
        for vx, vy in has.get(key, []):
            near = np.minimum(near, np.hypot(xy[:, 0] - vx, xy[:, 1] - vy))
        while not np.isnan(near).all():
            if np.isinf(near[~np.isnan(near)]).all():       # no via yet: begin in the middle
                mid = xy[~np.isnan(near)].mean(0)
                k = int(np.where(np.isnan(near), np.inf, np.hypot(xy[:, 0] - mid[0], xy[:, 1] - mid[1])).argmin())
            else:
                k = int(np.nanargmax(near))
                if near[k] < TIE_REACH:
                    break
            x, y, f, bk = spots[mine[k]]
            if r.via_ok(G, x, y, 0.6, room=0.1):
                r.via(G, x, y, size=0.6, drill=0.3)
                n += 1
                for other in ("F", f), ("B", bk):
                    has.setdefault(other, []).append((x, y))
                near = np.minimum(near, np.hypot(xy[:, 0] - x, xy[:, 1] - y))
            else:
                near[k] = np.nan                # no via fits here after all
    bare = sum(1 for k, a in area.items() if a >= TIE_AREA and k not in has)
    print("ties: %d vias between the pours; %d cells of %d mm2 and more have none" % (n, bare, TIE_AREA))


# --------------------------------------------------------------------- main ---
STAGE_FUNCS = [clean, nets, lanes, bus, lanes3, supply, vin, uarts, mcu, gnd, ties]


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


def main():
    if len(sys.argv) > 2 and sys.argv[2] == "--check":
        errs, parity, open_ = drc(PCB)
        print("DRC: %d errors, %d parity issues, %d unrouted" % (len(errs), parity, open_))
        for e in errs[:30]:
            print("   ", e)
        sys.exit(1 if errs or parity or open_ else 0)
    b = pcbnew.LoadBoard(PCB)
    if any(t.GetNetname() == "MUX_S0" for t in b.GetTracks()):
        sys.exit("this board is already routed; start from the hand-routed one (git show 991aaaf:pcb/tile.kicad_pcb)")
    r = pcbroute.Router(b, keepouts=[(x, y, g.POST_KEEPOUT) for x, y in POSTS(b)])
    for f in STAGE_FUNCS:
        if STAGES == ["all"] or f.__name__ in STAGES:
            f(r)
    bad = r.check()
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    b.BuildConnectivity()
    print("unrouted now %d" % b.GetConnectivity().GetUnconnectedCount(True))
    if bad:
        sys.exit("clearance problems: fix before trusting this board")
    # DRC in a fresh process (KiCad's Python can crash forking after edits)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), PCB, "--check"])


def POSTS(b):
    """Centres of the cover posts' keep-out areas, read from the board."""
    out = []
    for z in b.Zones():
        if z.GetIsRuleArea():
            bb = z.GetBoundingBox()
            out.append((mm(bb.GetCenter().x), mm(bb.GetCenter().y)))
    return out


if __name__ == "__main__":
    main()
