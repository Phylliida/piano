#!/usr/bin/env python3
"""Draw the hub's copper: hub/hub.kicad_pcb, as gen_hub_pcb.py placed it.

    ./kpython.sh route_hub.py [board]

KiCad must not have unsaved changes: save first, run this, then File > Revert.
It refuses to run on a board that already has tracks: place it again first
(REGEN_ROUTED_BOARD=1 ./kpython.sh gen_hub_pcb.py).

The stages, in order (ROUTE_STAGES=fine,usb runs only those):
  fine     the eFuses: 0.2 mm tracks out of their side pins and short necks
           out of their power strips, which stand 0.2 mm from their neighbours
  usb      the USB socket: CC resistors, the data pair through its protection
           and down to the MCU
  pd       the charger's socket and the PD chip under it
  power    VBUS, V_PD and VIN, wide; the regulator and the 3.3 V trunk
  mcu      the MCU's supply and ground pins and their capacitors
  near     what joins neighbours: LEDs, buttons, crystal, the three dividers
           by the MCU, the two lines to the tile port
  escapes  a short track and a via at each pad that the back's copper shuts
           in, for the long runs over the front to start and end on
  gnd      a via to the front's pour at every ground pad
  signals  everything else, searched (pcbroute's joiner); what must cross the
           wide tracks or the data pair hops over the front
  ties     vias between the two pours where either is cut into pieces

All parts but the LEDs and buttons are on the back, so the copper is too; the
front is the ground plane, with the hops and little else.  Coordinates are
KiCad's (mm, y down).
"""

import json
import math
import os
import subprocess
import sys
import time

import pcbnew

import hubgeom as g
import pcbroute

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "hub", "hub.kicad_pcb")
STAGES = os.environ.get("ROUTE_STAGES", "all").split(",")      # for trying one stage at a time
LOOSE = bool(os.environ.get("ROUTE_LOOSE"))                    # go on after a broken rule, to see it drawn

SHELLS = [(16.9, 11.0, 27.1, 18.9), (37.9, 11.0, 48.1, 18.9)]  # no vias under the USB sockets
ORIGIN = (26.25, 56.425)                                       # the search grid runs through U1's pads


def oops(msg):
    if not LOOSE:
        sys.exit("route_hub: " + msg)
    print("  !!", msg)


def L(r, net, pts, layer="B", w=None):
    """A track that is checked as it is drawn."""
    if not r.track_ok(net, pts, layer, w):
        oops("%s track breaks a rule: %s" % (net, pts))
    return r.line(net, pts, layer, w)


def V(r, net, x, y, size=None, drill=None, pad_gap=0.15):
    """A via that is checked as it is drawn."""
    if not r.via_ok(net, x, y, size, pad_gap=pad_gap):
        oops("%s via breaks a rule at (%.3f, %.3f)" % (net, x, y))
    return r.via(net, x, y, size=size, drill=drill)


def efuse(r, ref):
    """Where an eFuse's side pins are: {pin: (x, y)}.  Pins 1, 4, 7 and 10 have a
    second pad round the corner; this is the one on the side."""
    (x5, y), (x6, _) = r.pad(ref, 5), r.pad(ref, 6)
    e, w = (x5 + x6) / 2 + 0.9, (x5 + x6) / 2 - 0.9
    return {1: (e, y - 0.7), 2: (e, y - 0.225), 3: (e, y + 0.225), 4: (e, y + 0.7),
            7: (w, y + 0.7), 8: (w, y + 0.225), 9: (w, y - 0.225), 10: (w, y - 0.7)}


# ------------------------------------------------------------------ the eFuses ---
NECK = 0.3              # as wide as a power strip
BAR = 1.0               # the wide tracks that take over 1 mm past a strip's end


def fine(r):
    """Out of the eFuses.  A side pin's track leaves level with the pin, 0.2 wide;
    a strip's neck goes on in line with it until a wide track is clear of the
    pads round the strip's end."""
    # --- U6, the host's: timer and current limit to the west, enable and fault to the east
    p = efuse(r, "U6")
    (cx, cy), (rx, ry) = r.pad("C17", 1), r.pad("R20", 1)
    L(r, "ITIMER_HOST", [p[10], (p[10][0] - 0.45, p[10][1]), (cx + (p[10][1] - cy) - 0.04, cy), (cx, cy)])
    L(r, "ILM_HOST", [p[9], (p[9][0] - 0.6, p[9][1]), (p[9][0] - 0.6 - (p[9][1] - ry), ry), (rx, ry)])
    L(r, "ILM_HOST", [("R20", 1), ("R19", 1), ("R21", 1)])
    L(r, "GND", [p[8], (p[8][0] - 1.1, p[8][1])], w=0.2)
    V(r, "GND", p[8][0] - 1.1, p[8][1], 0.6, 0.3)
    cx, cy = r.pad("C16", 1)
    L(r, "DVDT_HOST", [p[7], (p[7][0] - 0.2, p[7][1]), (cx, p[7][1] + (p[7][0] - 0.2 - cx)), (cx, cy)])
    L(r, "EN_HOST", [p[1], ("R17", 1)])
    L(r, "GND", [p[2], (p[2][0] + 0.6, p[2][1]), (p[2][0] + 0.85, p[2][1] + 0.25)], w=0.2)
    V(r, "GND", p[2][0] + 0.85, p[2][1] + 0.25, 0.6, 0.3)
    L(r, "FLT_HOST", [p[4], (p[4][0] + 0.7, p[4][1])])
    x, y = r.pad("U6", 5)
    L(r, "VBUS", [(x, y - 0.975), (x, y - 1.975)], w=NECK)
    x, y = r.pad("U6", 6)
    L(r, "VIN", [(x, y + 1.025), (x, y + 2.025)], w=NECK)

    # --- U5, the charger's: its west pins' parts stand in a column beside them, in the pins' order
    p = efuse(r, "U5")
    cx, cy = r.pad("C14", 1)
    L(r, "ITIMER_PD", [p[10], (p[10][0] - 0.45, p[10][1]), (cx + 0.85, p[10][1] - 0.4), (cx + 0.85, cy + 0.45),
                       (cx + 0.4, cy), (cx, cy)])
    L(r, "ILM_PD", [p[9], (cx, p[9][1])])
    L(r, "ILM_PD", [("R14", 1), ("R13", 1)])
    gx, gy = r.pad("R13", 2)
    L(r, "GND", [p[8], (p[8][0] - 0.6, p[8][1]), (gx + 0.65, p[8][1] + 0.45), (gx + 0.65, gy - 0.33),
                 (gx + 0.32, gy), (gx, gy)], w=0.2)
    dx, dy = r.pad("C13", 1)
    L(r, "DVDT_PD", [p[7], (p[7][0] - 0.4, p[7][1]), (dx + 1.1, p[7][1] + 0.2), (dx + 1.1, dy - 0.4),
                     (dx + 0.7, dy), (dx, dy)])
    ex, ey = r.pad("R11", 1)
    L(r, "EN_PD", [p[1], (ex, p[1][1] - (ex - p[1][0])), (ex, ey)])
    ox, oy = r.pad("R16", 1)
    L(r, "OVLO_PD", [p[2], (ox - 0.44, p[2][1]), (ox - 0.44 + (oy - p[2][1]), oy), (ox, oy)])
    L(r, "OVLO_PD", [("R16", 1), ("R15", 2)])
    L(r, "FLT_PD", [p[4], (p[4][0] + 0.7, p[4][1])])
    x, y = r.pad("U5", 5)
    L(r, "V_PD", [(x, y - 1.025), (x, y - 2.025)], w=NECK)
    x, y = r.pad("U5", 6)
    L(r, "VIN", [(x, y + 1.025), (x, y + 2.025)], w=NECK)


# ---------------------------------------------------------------------- the USB ---
PAIR_X = (22.2, 22.6)   # D- and D+ on their way south to the MCU


def usb(r):
    """The USB socket.  Its pads come in the order CC1 D- D+ D- D+ CC2 between
    the two VBUS pads, so on the back one D- pad cannot reach the other: it
    hops over the front, from a via below the pads to one under U3.  U3's VBUS
    pin stands between the data lines; it gets a via under U3 too, to the
    VBUS bar on the front (power() draws that)."""
    def j2(x):
        return (x, r.pad("J2", "A5")[1])

    cc1, cc2 = r.pad("J2", "A5")[0], r.pad("J2", "B5")[0]
    assert abs(cc1 - 20.75) < 0.01 and abs(cc2 - 23.75) < 0.01, "J2 has moved: the USB stage is drawn for x 22.0"
    (ax, ay), (bx, by) = r.pad("R5", 1), r.pad("R6", 1)
    L(r, "USB_CC1", [j2(cc1), (cc1, ay - (cc1 - ax)), (ax, ay), ("R3", 1)])
    L(r, "USB_CC2", [j2(cc2), (cc2, by - (bx - cc2)), (bx, by), ("R4", 1)])
    (mx, my), (px, py) = r.pad("U3", 4), r.pad("U3", 6)         # D- in at U3's pin 4, D+ at pin 6
    _, low = r.pad("U3", 3)
    L(r, "USB_DM", [j2(21.25), (21.25, 21.2), (mx, 21.2 + (21.25 - mx)), (mx, low)])
    L(r, "USB_DP", [j2(22.75), (22.75, 20.0), (px, 20.0 + (px - 22.75)), (px, low)])
    L(r, "USB_DP", [j2(21.75), (21.75, 20.0), (21.65, 20.1), (21.65, 21.2), (22.05, 21.6), (px - 0.4, 21.6), (px, 22.0)])
    L(r, "USB_DM", [j2(22.25), (22.25, 20.75)])
    V(r, "USB_DM", 22.25, 20.75, 0.6, 0.3)
    hop = (my + low) / 2                                        # between U3's two rows of pads
    L(r, "USB_DM", [(22.25, 20.75), (22.25, 21.2), (mx, 21.2 + (22.25 - mx)), (mx, hop)], layer="F")
    V(r, "USB_DM", mx, hop, 0.6, 0.3, pad_gap=0.1)
    # on south: the pair comes together below U3 and runs straight down to U1's pins 44 and 45
    gx, gy = r.pad("U3", 2)
    L(r, "GND", [(gx, gy), (gx, gy + 1.4)], w=0.3)
    V(r, "GND", gx, gy + 1.4, 0.6, 0.3)
    dm, dp = PAIR_X
    (x44, y44), (x45, y45) = r.pad("U1", 44), r.pad("U1", 45)
    L(r, "USB_DM", [(mx, low), (mx, 26.9), (dm, 26.9 + (dm - mx)), (dm, y44 - 0.4), (dm + 0.4, y44), (x44, y44)])
    L(r, "USB_DP", [(px, low), (px, 26.9), (dp, 26.9 + (px - dp)), (dp, y45 - 0.4), (dp + 0.4, y45), (x45, y45)])


# ----------------------------------------------------------- the charger's socket ---
def pd(r):
    """The charger's socket and the PD chip below it.  The socket's pads read CC1
    D- D+ D- D+ CC2 from the west; the chip wants the data on its west side and
    the CC lines on its east.  CC2 and the data stay on the back.  CC1 crosses
    the data over the front, and the second D- pad hops as it does at the USB
    socket."""
    y0 = r.pad("J3", "A5")[1]
    cc1, cc2 = r.pad("J3", "A5")[0], r.pad("J3", "B5")[0]
    assert abs(cc1 - 41.75) < 0.01 and abs(cc2 - 44.75) < 0.01, "J3 has moved: the pd stage is drawn for x 43.0"
    (x4, y4), (x5, y5), (x6, y6), (x7, y7) = [r.pad("U4", n) for n in (4, 5, 6, 7)]
    L(r, "PD_CC2", [(cc2, y0), (cc2, y6 - 0.475), (cc2 - 0.475, y6), (x6, y6)])
    # D+: its two pads meet round the south of D-'s via, then west and down the gap beside the ground pad
    lane = (x4 + 0.8375 + r.pad("U4", 11)[0] - 1.05) / 2
    L(r, "PD_DM", [(43.25, y0), (43.25, 20.75)])
    V(r, "PD_DM", 43.25, 20.75, 0.6, 0.3)
    L(r, "PD_DP", [(43.75, y0), (43.75, 20.0), (43.95, 20.2), (43.95, 22.0), (43.65, 22.3), (lane + 0.375, 22.3),
                   (lane, 22.675), (lane, y4 - 0.375), (lane - 0.375, y4), (x4, y4)])
    L(r, "PD_DP", [(42.75, y0), (42.75, 20.0), (42.65, 20.1), (42.65, 22.1), (42.45, 22.3)])
    # D-: the first pad west above D+ and into pin 5 from the north; the second pad's hop lands on it
    L(r, "PD_DM", [(42.25, y0), (42.25, 21.5), (42.05, 21.7), (39.3, 21.7), (38.9, 22.1), (38.9, y5)])
    V(r, "PD_DM", 41.0, 21.7, 0.6, 0.3)
    L(r, "PD_DM", [(43.25, 20.75), (42.3, 21.7), (41.0, 21.7)], layer="F")
    # CC1: a via beside its pad, over the front north of D-'s via, down to a via east of pin 7
    L(r, "PD_CC1", [(cc1, y0), (cc1, 20.3), (cc1 - 0.25, 20.55), (cc1 - 0.25, 20.75)])
    V(r, "PD_CC1", cc1 - 0.25, 20.75, 0.6, 0.3)
    ex = x7 + 1.3125
    L(r, "PD_CC1", [(cc1 - 0.25, 20.75), (cc1 - 0.25, 20.3), (cc1 + 0.15, 19.9), (43.6, 19.9), (44.6, 20.9),
                    (44.6, y7 - 0.875), (ex, y7 - 0.425), (ex, y7)], layer="F")
    V(r, "PD_CC1", ex, y7, 0.6, 0.3)
    L(r, "PD_CC1", [(ex, y7), (x7, y7)])
    L(r, "PD_CFG1", [("U4", 9), ("R7", 1)])
    L(r, "CH_SCL", [("R8", 2), ("U4", 2)])
    L(r, "CH_SDA", [("R9", 2), ("U4", 3)])

# -------------------------------------------------------------------- the power ---
def socket_bar(r, ref, net, y=19.85):
    """A socket's two supply pads stand either side of its data pads.  Each gets a
    via just below it, and a bar on the front joins the two."""
    (wx, py), (ex, _) = r.pad(ref, "B9"), r.pad(ref, "B4")
    assert wx < ex
    vy = 20.75
    for x in wx, ex:
        L(r, net, [(x, py), (x, vy)], w=0.6)
    V(r, net, wx, vy, 0.8, 0.4)
    V(r, net, ex + 0.85, vy + 0.05, 0.8, 0.4)
    L(r, net, [(wx, vy), (wx, y + 0.45), (wx + 0.45, y), (ex + 0.9 - (vy - y), y), (ex + 0.85, vy + 0.05)], layer="F", w=0.6)
    return (wx, vy), (ex, vy)


def power(r):
    """The wide tracks.  VBUS: from the USB socket west to the regulator and east
    to the host eFuse.  V_PD: from the charger's socket east to its eFuse.  VIN:
    out of both eFuses, along the channel's north edge to the tile port.  All on
    the back; together with the eFuses they fence in the PD chip and its
    neighbours, whose signals leave over the front (signals())."""
    # --- VBUS
    (wx, wy), (ex, ey) = socket_bar(r, "J2", "VBUS")
    by = 20.8                                                   # the bars below the socket
    cx, cy = r.pad("C10", 1)
    L(r, "VBUS", [(wx, wy), (wx, by), (cx, by)], w=BAR)
    L(r, "VBUS", [(cx, by), (cx, cy)], w=0.6)
    (ax, ay), (bx, b_y) = r.pad("U2", 1), r.pad("U2", 3)
    L(r, "VBUS", [(cx, cy), (cx, 24.0), (ax, 24.0 + (cx - ax)), (ax, ay)], w=0.5)
    L(r, "VBUS", [(bx + 0.45, by), (bx + 0.45, 24.0), (bx, 24.45), (bx, b_y)], w=0.4)
    sx, sy = r.pad("U6", 5)
    top = sy - 1.975                                            # where fine() left the neck
    L(r, "VBUS", [(ex, ey), (ex + 0.85, by)], w=0.6)             # the bar starts clear of CC2's track
    L(r, "VBUS", [(ex + 0.85, by), (sx - 0.75, by), (sx, by + 0.75), (sx, top)], w=BAR)
    kx, ky = r.pad("C18", 1)
    L(r, "VBUS", [(sx, ky), (kx, ky)], w=0.5)
    # U3's supply pin stands between the data lines: a via under U3, a track on the front to the bar
    ux, uy = r.pad("U3", 5)
    L(r, "VBUS", [(ux, uy), (ux, 24.0)], w=0.3)
    V(r, "VBUS", ux, 24.0, 0.6, 0.3, pad_gap=0.05)
    L(r, "VBUS", [(ux, 24.0), (ux + 0.5, 24.0), (ux + 1.3, 23.2), (ux + 1.3, 19.85)], layer="F", w=0.3)

    # --- V_PD: bar east to the diode at the corner, trunk south to the eFuse, its capacitor on the way
    (wx, wy), (ex, ey) = socket_bar(r, "J3", "V_PD", y=19.0)     # further north: CC1 hops over the front below it
    sx, sy = r.pad("U5", 5)
    top = sy - 2.025
    dx, dy = r.pad("D1", 1)
    L(r, "V_PD", [(ex, ey), (ex + 0.85, by)], w=0.6)
    L(r, "V_PD", [(ex + 0.85, by), (dx - 0.5, by), (dx, by - 0.5), (dx, dy)], w=BAR)
    L(r, "V_PD", [(sx, by), (sx, top)], w=BAR)
    kx, ky = r.pad("C15", 1)
    L(r, "V_PD", [(sx, ky), (kx, ky)], w=0.8)
    tx, ty = r.pad("R15", 1)                                    # the over-voltage divider's top
    L(r, "V_PD", [(kx, ky), (tx - 0.46, ky), (tx, ky + 0.46), (tx, ty)], w=0.25)
    # the PD chip's two supply pins: pin 8 up the east side to the socket's pad, pin 1 to its capacitor
    px, py = r.pad("U4", 8)
    L(r, "V_PD", [(px, py), (ex - 0.1, py), (ex + 0.3, py - 0.4), (ex + 0.3, 21.6), (ex, 21.3), (ex, ey)], w=0.3)
    (qx, qy), (kx, ky) = r.pad("U4", 1), r.pad("C12", 1)        # pin 1: under the body, between the
    lane = (r.pad("U4", 11)[0] + 1.05 + px - 0.8375) / 2        # ground pad and the east pins
    L(r, "V_PD", [(px, py), (px - 0.8375, py), (lane, py + 0.375), (lane, qy), (qx, qy)], w=0.3)
    L(r, "V_PD", [(kx, ky), (qx - (ky - qy), ky), (qx, qy)], w=0.3)

    # --- VIN: out of U6, south to its capacitor and east to the clamp; on along the channel to
    # the bulk capacitor, U5's output and the port
    sx, sy = r.pad("U6", 6)
    (dx, dy), (kx, ky) = r.pad("D2", 1), r.pad("C19", 1)
    L(r, "VIN", [(sx, sy + 2.025), (sx, dy), (dx, dy)], w=BAR)
    L(r, "VIN", [(sx, dy), (sx, ky), (kx, ky)], w=0.8)
    cx, cy = r.pad("C20", 1)
    L(r, "VIN", [(dx, dy), (dx, 31.9), (dx + 0.6, 32.5), (cx - 0.35, 32.5), (cx, cy)], w=BAR)
    sx, sy = r.pad("U5", 6)
    jx, jy = r.pad("J4", 6)
    L(r, "VIN", [(sx, sy + 2.025), (sx, jy)], w=BAR)
    L(r, "VIN", [(cx, cy), (cx, 34.4), (cx + 0.85, 35.25), (sx, 35.25)], w=0.7)
    _, j1 = r.pad("J4", 1)                                      # the port's other VIN pin: round under its body
    L(r, "VIN", [(jx, jy), (47.1, jy), (46.8, jy + 0.3), (46.8, j1 - 0.3), (47.1, j1), (jx, j1)], w=0.6)

    # --- 3.3 V: regulator to its capacitor, then a trunk down the west edge to the debug pads
    (ux, uy), (kx, ky) = r.pad("U2", 5), r.pad("C11", 1)
    L(r, "+3V3", [(ux, uy), (ux, 28.6), (kx, 28.6 + (ux - kx)), (kx, ky)], w=0.4)
    jx, jy = r.pad("J1", 1)
    L(r, "+3V3", [(kx, ky), (kx, 30.4), (TRUNK_X, 30.4 + (kx - TRUNK_X)), (TRUNK_X, jy - (jx - TRUNK_X)), (jx, jy)], w=0.4)


TRUNK_X = 13.75         # the 3.3 V trunk: by the west edge, clear of the west post


def mcu(r):
    """U1's supply.  The trunk's branch hops over the front (the debug and LED
    lines run south under it) to C2 and pin 36; from there a track under U1
    carries 3.3 V to pins 64 and 1 and the parts round the north-east corner.
    The three ground pins get a via under U1 each."""
    # --- ground first: the vias stand 1.2 mm and more inside the pad rows
    (x, y) = r.pad("U1", 63)
    L(r, "GND", [(x, y), (x, y + 1.2), (x - 0.5, y + 1.7)], w=0.25)
    V(r, "GND", x - 0.5, y + 1.7, 0.6, 0.3)
    (x, y) = r.pad("U1", 12)
    L(r, "GND", [(x, y), (x - 1.525, y)], w=0.25)
    V(r, "GND", x - 1.525, y, 0.6, 0.3)
    (x, y) = r.pad("U1", 31)
    L(r, "GND", [(x, y), (x, y - 1.55)], w=0.25)
    V(r, "GND", x, y - 1.55, 0.6, 0.3)
    # --- the branch to C2 and pin 36
    (cx, cy), (px, py) = r.pad("C2", 1), r.pad("U1", 36)
    hy = cy + 0.82                                              # between C2 and R2
    L(r, "+3V3", [(TRUNK_X, hy), (15.2, hy)], w=0.4)
    V(r, "+3V3", 15.2, hy, 0.8, 0.4)
    L(r, "+3V3", [(15.2, hy), (20.7, hy)], layer="F", w=0.4)
    V(r, "+3V3", 20.7, hy, 0.8, 0.4)
    L(r, "+3V3", [(20.7, hy), (cx, hy), (cx, cy)], w=0.4)
    L(r, "+3V3", [(cx, cy), (cx + 0.47, py), (px, py)], w=0.25)
    # --- under U1 to pin 64, and on to pin 1
    (nx, ny), (ex, ey) = r.pad("U1", 64), r.pad("U1", 1)
    turn = 57.0
    L(r, "+3V3", [(px, py), (nx - (py - turn), py), (nx, turn), (nx, ny)], w=0.25)
    L(r, "+3V3", [(nx, ey), (ex, ey)], w=0.25)
    kx, ky = r.pad("C1", 1)
    L(r, "+3V3", [(ex, ey), (kx - (ey - ky), ey), (kx, ky)], w=0.25)
    # --- north of pin 64: its capacitor, the bulk capacitor and the three pull-ups in a row.
    # The way to the bulk capacitor goes round its east side: C3's ground pad stands between
    # the two and needs the gap for its via
    kx, ky = r.pad("C3", 1)
    L(r, "+3V3", [(nx, ny), (nx, ky)], w=0.25)
    bx, b_y = r.pad("C4", 1)
    _, row = r.pad("R18", 1)
    east = bx + 1.1
    L(r, "+3V3", [(nx, ky), (nx + 0.15, ky + 0.15), (east - 0.5, ky + 0.15), (east, ky - 0.35),
                  (east, b_y + 0.5), (east - 0.5, b_y), (bx, b_y)], w=0.3)
    L(r, "+3V3", [(bx, b_y), (bx - 0.7, row), ("R18", 1), ("R12", 1), ("R10", 1)], w=0.3)
    # --- the analogue supply: pin 13, its two capacitors, the bead
    L(r, "+3.3VA", [("U1", 13), (r.pad("C5", 1)[0] - 0.235, r.pad("U1", 13)[1]), ("C5", 1), ("C6", 1), ("FB1", 2)], w=0.25)


# ------------------------------------------------------------- the short links ---
def both(r, ref, num):
    """A button has two pads to a contact: where they are, the west one first."""
    return sorted((x, y) for f, n, _, x, y, _, _ in r.terminals if f == ref and n == str(num))


LINK_X = (51.3, 52.0)   # LINK_TX and LINK_RX on their way north along the east edge


def near(r):
    """What joins neighbours, drawn by hand: the resistors at the LEDs, the
    buttons and the tile port; the crystal; the reset capacitor; the three
    dividers east of U1 with their tracks to pins 8, 9 and 10; the two lines to
    the tile port round U1's south-east corner."""
    (px, py), (rx, ry) = r.pad("U1", 33), r.pad("R2", 1)
    L(r, "OTGHS_R", [(px, py), (rx + (ry - py), py), (rx, ry)])
    for res, led, net in ("R30", "D3", "LED_STATUS_A"), ("R31", "D4", "LED_POWER_A"):
        (x, y), (dx, dy) = r.pad(res, 2), r.pad(led, 2)
        L(r, net, [(x, y), (x, dy - (x - dx)), (dx, dy)])
    # --- the buttons: a contact's two pads, and the resistor between the two buttons
    for ref, num, net in ("SW1", 1, "+3V3"), ("SW1", 2, "BOOT0"), ("SW2", 1, "BTN_USER"):
        L(r, net, both(r, ref, num), w=0.25)
    (ax, ay), (bx, by) = r.pad("R32", 1), r.pad("R32", 2)
    (sx, sy), (ux, uy) = both(r, "SW1", 1)[0], both(r, "SW2", 1)[1]
    L(r, "+3V3", [(ax, ay), (ax + (ay - sy), sy), (sx, sy)], w=0.25)
    L(r, "BTN_USER", [(bx, by), (bx - 0.7, by), (ux, by - (bx - 0.7 - ux)), (ux, uy)])
    (x, y), (_, sy) = r.pad("R1", 1), both(r, "SW1", 2)[0]
    L(r, "BOOT0", [(x, y), (x, sy)])
    # --- the tile port's data lines to their series resistors
    L(r, "LINK_A", [("J4", 3), ("R28", 2)])
    L(r, "LINK_B", [("J4", 4), ("R29", 2)])

    # --- the crystal.  Out: pin 6 to its capacitor, the capacitor to the crystal above it.
    # In: north-east between C1 and the crystal, through the gap between the crystal's two
    # west pads and into the north-east one, whose capacitor stands above it.
    tip = r.pad("U1", 5)[0] + 0.775                             # where the east side's pads end
    (x6, y6), (kx, ky) = r.pad("U1", 6), r.pad("C9", 1)
    L(r, "HEXT_OUT", [(x6, y6), (tip + 0.45, y6), (tip + 0.7, y6 - 0.25), (kx, y6 - 0.25), ("Y1", 3)])
    (x5, y5), (qx, qy) = r.pad("U1", 5), r.pad("Y1", 1)
    gap = (r.pad("Y1", 4)[1] + r.pad("Y1", 3)[1]) / 2
    L(r, "HEXT_IN", [(x5, y5), (tip + 0.15, y5), (tip + 0.15 + (y5 - gap), gap), (qx, gap), (qx, qy), ("C8", 1)])
    L(r, "NRST", [("U1", 7), ("C7", 1)])

    # --- the dividers: three rows, each read from the east: supply, top resistor, bottom
    # resistor, capacitor.  The tapped net's three pads have ground pads between them, so
    # their link runs in the gap above the row.  West of the rows the three tracks from
    # pins 8, 9 and 10 run side by side below C7 and C9 and fan out to the capacitors.
    lanes = (60.1, 60.45, 60.8)
    for k, (net, cap, low, top, pin) in enumerate((("VIN_SENSE", "C21", "R23", "R22", 8),
                                                   ("VPD_SENSE", "C22", "R25", "R24", 9),
                                                   ("VBUS_SENSE", "C23", "R27", "R26", 10))):
        (cx, cy), (lx, _), (tx, _) = r.pad(cap, 1), r.pad(low, 1), r.pad(top, 2)
        up = cy - 0.75
        L(r, net, [(cx, cy), (cx, up), (tx, up), (tx, cy)])
        L(r, net, [(lx, up), (lx, cy)])
        (px, py), y = r.pad("U1", pin), lanes[k]
        jog = abs(y - py)
        if jog < 0.05:                                          # as good as level: start in the pad
            pts = [(px + 0.3, y)]
        else:
            pts = [(px, py), (tip + 0.15, py), (tip + 0.15 + jog, y)]
        fan = cx - 0.62                                         # the fan's east end, short of the capacitors
        if abs(y - cy) > 0.05:
            pts += [(fan - abs(y - cy), y), (fan, cy), (cx, cy)]
        else:
            pts += [(cx, y)]
        L(r, net, pts)
    # VIN's tap: the tile port's pin 1 stands straight above the top row's supply pad
    tx, ty = r.pad("R22", 1)
    L(r, "VIN", [(tx, r.pad("J4", 1)[1]), (tx, ty)], w=0.25)

    # --- LINK_TX and LINK_RX: pins 16 and 17, east below the bead, north along the east edge
    (x16, y16), (x17, y17) = r.pad("U1", 16), r.pad("U1", 17)
    (tx, ty), (rx, ry) = r.pad("R28", 1), r.pad("R29", 1)
    a, b = LINK_X
    ya, yb = y16 + 0.225, y16 + 0.575
    L(r, "LINK_TX", [(x16, y16), (tip + 0.3, y16), (tip + 0.525, ya), (a - 0.55, ya), (a, ya - 0.55), (a, ty)])
    low = y17 + 1.1                                             # below the south side's pads
    L(r, "LINK_RX", [(x17, y17), (x17, low), (x17 + 1.1, low), (x17 + 1.1 + (low - yb), yb), (b - 0.6, yb),
                     (b, yb - 0.6), (b, ry + 0.4), (b - 0.4, ry), (rx, ry)])

    # --- V_PD's tap.  VIN's tap, the tile port and those two lines shut the middle row's
    # supply pad in on the back, so it goes over the front: up the east edge, above the two
    # lines, to the track into R15
    (tx, ty), (ex, ey) = r.pad("R24", 1), r.pad("R15", 1)
    vx, top = a - 0.8, ey - 3.125
    L(r, "V_PD", [(tx, ty), (vx, ty)], w=0.25)
    V(r, "V_PD", vx, ty)
    L(r, "V_PD", [(vx, ty), (ex - 0.56, ty), (ex, ty - 0.56), (ex, top)], "F", w=0.25)
    V(r, "V_PD", ex, top)


# ------------------------------------------------------------------ the escapes ---
def escapes(r):
    """Where the back's copper shuts a pad in, a short track and a via, for the
    long runs over the front to start and end on: U1's pins (under U1, or just
    outside its pads), with the three pull-ups north of it joined on the way,
    and the parts round the eFuses and the PD chip, behind VIN's wide track."""
    def pin(n):
        return r.pad("U1", n)

    def stub(net, at, *steps):
        """From a point by steps (dx, dy) to a via."""
        pts = [at]
        for dx, dy in steps:
            pts.append((pts[-1][0] + dx, pts[-1][1] + dy))
        L(r, net, pts)
        V(r, net, *pts[-1])

    # --- U1's north side.  3.3 V closes the space above pins 57 to 62 on three sides.  Pins
    # 57, 61 and 62 run up to their pull-ups with a via on the way; pin 59's via stands
    # above its pad; pins 58 and 60 turn in under U1.
    (x, y), (rx, ry) = pin(57), r.pad("R10", 2)
    L(r, "PD_PG", [(x, y), (x, y - 1.125), (rx, y - 1.125 - (x - rx)), (rx, ry)])
    V(r, "PD_PG", rx, ry + 1.1)
    (x, y), (rx, ry) = pin(61), r.pad("R12", 2)
    L(r, "FLT_PD", [(x, y), (x, y - 1.9), (x - 0.2, y - 2.1), (x - 0.2, ry)])
    V(r, "FLT_PD", x - 0.2, y - 3.0)
    (x, y), (rx, ry) = pin(62), r.pad("R18", 2)
    L(r, "FLT_HOST", [(x, y), (x, ry + 0.165 + (rx - x)), (rx, ry + 0.165), (rx, ry)])
    V(r, "FLT_HOST", x, y - 2.3)
    stub("PD_SDA", pin(59), (0, -1.3))
    stub("PD_SCL", pin(58), (0, 1.25))
    stub("BOOT0", pin(60), (0, 1.25))
    # --- the north side's west end and the west side: under U1, north-west of its 3.3 V track
    stub("SWCLK", pin(49), (0, 1.5))
    stub("AUX_TX", pin(51), (0, 1.5))
    stub("AUX_RX", pin(52), (0, 1.0), (0.5, 0.5))
    stub("SWDIO", pin(46), (1.275, 0))
    stub("LED_POWER", pin(38), (1.375, 0))
    # --- east side: under U1, south-east of the 3.3 V track
    stub("BTN_USER", pin(2), (-0.975, 0), (-0.3, 0.3))
    stub("NRST", pin(7), (-1.375, 0))
    stub("IMON_PD", pin(11), (-1.1, 0), (-0.425, -0.425))
    stub("IMON_HOST", pin(14), (-1.525, 0))
    stub("CC1_SENSE", pin(15), (-0.975, 0), (-0.3, 0.3), (-0.5, 0))
    # --- south side: pins 20 and 28 under U1; pin 31's ground via bars that for 30 and 32
    stub("CC2_SENSE", pin(20), (0, -1.45))
    stub("ILIM_HI", pin(28), (0, -1.45))
    stub("EN_PD", pin(30), (0, 1.55))
    stub("EN_HOST", pin(32), (0, 1.55))

    # --- the host's eFuse and what stands west of it
    stub("EN_HOST", r.pad("R17", 1), (0, -0.975))
    x, y = efuse(r, "U6")[4]
    stub("FLT_HOST", (x + 0.7, y), (0.625, 0.625))
    stub("IMON_HOST", r.pad("R20", 2), (-0.89, 0))
    stub("ILIM_HI", r.pad("R21", 2), (-0.89, 0))
    stub("CC2_SENSE", r.pad("R6", 2), (1.04, 0))
    stub("CC1_SENSE", r.pad("R5", 2), (-0.39, 0), (-0.3, 0.3), (0, 2.125))
    # --- the PD chip and the charger's eFuse
    stub("PD_SDA", r.pad("R9", 1), (0, -1.025))
    stub("PD_SCL", r.pad("R8", 1), (-0.545, 0), (-0.295, 0.295))
    stub("PD_PG", r.pad("U4", 10), (0, 0.925))
    stub("IMON_PD", r.pad("R14", 2), (-1.25, 0))
    stub("EN_PD", r.pad("R11", 1), (1.0, 0))
    x, y = efuse(r, "U5")[4]
    stub("FLT_PD", (x + 0.7, y), (0.175, 0), (0.525, 0.525))


# ------------------------------------------------------------------ the ground ---
BULK = ("C4", "C11", "C12", "C15", "C19", "C20", "D1", "D2", "U2")     # what the supplies' currents return through
TWICE = ("C20", "D2")                                                   # big pads: two vias


def gnd(r):
    """Ground is the pour on both sides.  The front's is the plane; the back's
    is cut into cells by everything else.  So every ground pad gets a via into
    the front's as near as one fits, and the few that none fits at a track to
    a neighbour's.  The sockets' shells, the LEDs and the user button go
    through the board and need none.  The long runs cut the front's pour too,
    into a few large pieces; ties() joins those through the back's."""
    G = "GND"
    before = len(r.added)
    g = pcbroute.Ground(r, least=20.0)          # the long runs cut the front's pour up; ties() joins the pieces
    big, ANY = g.BIG, g.ANY

    # --- what is tied already: the pads that go through the board, the pins that have a via
    for pads, places in r.pieces(G):
        if any(on == "FB" for _, _, on, _ in places):
            g.done.update(pads)
            g.tied.extend((x, y) for x, y, on, box in places if on == "FB" or box)

    # --- the USB sockets: each ground pin stands 0.3 mm from a leg of the shell
    for j in ("J2", "J3"):
        legs = [(x, y) for ref, num, _, x, y, _, _ in r.terminals if ref == j and num == "S1" and y > 16.0]
        for pin in ("A1", "A12"):
            x, y = r.pad(j, pin)
            lx, _ = min(legs, key=lambda p: abs(p[0] - x))
            L(r, G, [(x, y - 0.25), (lx, y - 0.25)], w=0.3)
            g.done.update({(j, pin), (j, {"A1": "B12", "A12": "B1"}[pin])})
            g.tied.append((x, y))

    # --- the tile port: what goes out on VIN comes back through pins 2 and 5.  A via as big as
    # VIN's east of each pin's tip, and one at each of the shell's tabs
    for pin in (2, 5):
        g.pad("J4", pin, "E", "NE SE", far=3.0, **big)
    g.done.add(("J4", "MP"))
    for ref, num, _, x, y, _, _ in r.terminals:
        if ref == "J4" and num == "MP" and not g.stitch((x, y), *ANY, far=3.0):
            g.missed.append(("J4", "MP"))

    # --- the supplies' capacitors and diodes, the regulator, the PD chip's pad: big vias
    for ref in BULK:
        g.pad(ref, 2, *ANY, far=3.0, **big)
    for ref in TWICE:
        at = g.stitch((ref, 2), *ANY, far=3.5, **big)
        if at:
            g.tied.append(at)
    # --- the PD chip's ground is the pad under it, with pins all round: two vias in the pad
    x, y = r.pad("U4", 11)
    for dy in (-0.9, 0.9):
        V(r, G, x, y + dy, 0.6, 0.3, pad_gap=-0.3)
    g.done.add(("U4", "11"))
    g.tied.append((x, y))

    # --- everything else with a ground pad
    pins = {}
    for ref, num, _, _, _, _, _ in r.terminals:
        pins.setdefault(ref, set()).add(num)
    for ref, num, net, _, _, _, _ in r.terminals:
        if net != G or (ref, num) in g.done:
            continue
        if len(pins[ref]) == 2:
            g.pad(ref, num)                     # straight out of the part, if it can be
        else:
            g.pad(ref, num, *ANY)
    # --- the pads that no via fits at: a track to the nearest that has one
    joined = [m for m in g.missed if m[1] != "MP" and g.join(*m)]
    tied = set()
    for pads, places in r.pieces(G):
        if any(on == "FB" for _, _, on, _ in places):
            tied |= pads
    left = sorted({"%s.%s" % m for m in g.missed if (m[0], str(m[1])) not in tied})
    print("gnd: %d vias, %d pads joined to a neighbour's%s" % (
        sum(1 for a in r.added[before:] if a[0] == "C"), len(joined),
        "; left to the pour alone: " + ", ".join(left) if left else ""))


# ----------------------------------------------------------------- the signals ---
THIN = {"+3V3": 0.3, "VIN": 0.25, "V_PD": 0.25, "VBUS": 0.25}  # what is left of these is taps
FIRST = ("VBUS",)       # its tap has the longest way, and one way out of the corner it starts in
LAST = ("+3V3",)        # short ends to the buttons and the bead, which fit in anywhere
SEARCH = dict(layers="FB", via_cost=40, greedy=2.0, pads=(3.0, 12.0, 200.0))
QUICK, DEEP = 1500000, 5000000          # the places a search may look at: some 20 s, and a minute


def signals(r):
    """Everything that is not joined yet, searched net by net, the short ones
    first.  Both sides cost the same and a via as much as 2 mm of track: the
    back is full near the chips, so the long runs go over the front wherever
    they cross something.  A net that finds no way is tried again looking
    further; if there is still none, one of the others is in its way: each in
    turn, the nearest first, is taken out, and put back if that does not let
    both through."""
    start = len(r.added)

    def search(net, nodes):
        t = time.time()
        bad = r.join(net, w=THIN.get(net), max_nodes=nodes, **SEARCH)
        return bad, time.time() - t

    def say(text):
        print(text)
        sys.stdout.flush()

    box = {}
    for ref, num, net, x, y, _, _ in r.terminals:
        if net and net != "GND" and not net.startswith("unconnected"):
            b = box.setdefault(net, [x, y, x, y])
            box[net] = [min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)]
    order = sorted(box, key=lambda n: (n in LAST, n not in FIRST, box[n][2] - box[n][0] + box[n][3] - box[n][1], n))
    lost = {}
    for net in order:
        if len(r.pieces(net)) > 1:
            lost[net], t = search(net, QUICK)
            say("  %-12s %5.1fs%s" % (net, t, "   no way yet" if lost[net] else ""))
            if not lost[net]:
                del lost[net]
    for net in list(lost):
        lost[net], t = search(net, DEEP)
        say("  %-12s %5.1fs   looking further%s" % (net, t, ": no way yet" if lost[net] else ""))
        if not lost[net]:
            del lost[net]

    def reach(net, pts):
        """How near a net's searched copper comes to any of the points."""
        near = 1e9
        for it in r.added[start:]:
            if it[1] == net and it[0] == "S":
                near = min([near] + [pcbroute._pt_seg(x, y, *it[3:7]) for x, y in pts])
            elif it[1] == net:
                near = min([near] + [math.hypot(x - it[2], y - it[3]) for x, y in pts])
        return near

    for net in list(lost):
        pts = []                                                # the pads and vias of the pieces left out
        for pads, places in r.pieces(net):
            if sorted(pads) in lost[net] or not pads:
                pts += [(x, y) for x, y, on, pad in places if pad or on == "FB"] or [places[0][:2]]
        others = {it[1] for it in r.added[start:]} - {net}
        for other in sorted(others, key=lambda n: (reach(n, pts), n)):
            was = r.take(other, start)
            mark = len(r.added)
            bad, t = search(net, QUICK)
            if bad:
                say("  %-12s %5.1fs   without %s: no way" % (net, t, other))
            else:
                bad, t2 = search(other, DEEP)
                say("  %-12s %5.1fs   without %s, which %s" % (
                    net, t, other, "finds no way back (%.1fs)" % t2 if bad else "is drawn again (%.1fs)" % t2))
            if not bad:
                del lost[net]
                break
            r.take(None, mark)
            r.put(was)
    if lost:
        oops("no way found for %s" % ", ".join(sorted(lost)))


def ties(r):
    """Vias between the two pours, where the tracks cut either into cells."""
    pcbroute.ties(r)


# ------------------------------------------------------------------------ main ---
STAGE_FUNCS = [fine, usb, pd, power, mcu, near, escapes, gnd, signals, ties]


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
    if b.GetTracks():
        sys.exit("this board already has tracks; place it again first (REGEN_ROUTED_BOARD=1 ./kpython.sh gen_hub_pcb.py)")
    posts = [g.to_kicad(x, y) + (g.POST_KEEPOUT,) for x, y in g.POSTS]
    r = pcbroute.Router(b, keepouts=posts + SHELLS, origin=ORIGIN)
    for f in STAGE_FUNCS:
        if STAGES == ["all"] or f.__name__ in STAGES:
            f(r)
    bad = r.check()
    b.GetDesignSettings().m_MinResolvedSpokes = 1
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    b.BuildConnectivity()
    print("unrouted now %d" % b.GetConnectivity().GetUnconnectedCount(True))
    if bad:
        sys.exit("clearance problems: fix before trusting this board")
    # DRC in a fresh process (KiCad's Python can crash forking after edits)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), PCB, "--check"])


if __name__ == "__main__":
    main()
