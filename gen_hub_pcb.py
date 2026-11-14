#!/usr/bin/env python3
"""Place hub_circuit.py on a board with KiCad's own API: hub/hub.kicad_pcb.

Run gen_hub_sch.py first: every footprint is linked to its schematic symbol
by UUID.  gen_pcb.py supplies the tools (the tile is placed with them too);
this file says where each part goes, and checks that.  route_hub.py then
draws the copper.

    ./kpython.sh gen_hub_pcb.py

The hub is small and nearly every part has one right place, so nothing is
searched for: each part has its spot in a table below, in hubgeom's
coordinates (mm, y up, seen from above, through the board).  What is checked:
every part is in the table with the nets the circuit gives it; no courtyards
overlap; nothing is where a cover post presses, where the cable lies, or on
the legs of the parts that stand on the other side; and each part reads back
the way round it was meant to be.

The lay of the board
  top (the plate side): only what reaches through the plate -- two LEDs and
        two push buttons, in a row near the south end
  back, north:  the two USB-C sockets, opening north.  Under the USB socket
        its protection, the 3.3 V regulator and the host eFuse; under the
        charger's socket the PD chip and its eFuse.  Both eFuses feed VIN,
        which runs east along the edge of the cable channel to the tile port.
  back, middle: the cable channel (bare) with the tile port at its east end;
        south of it the MCU, its crystal east of it, the ADC dividers beyond
  back, south:  the debug pads, under the LEDs and buttons
"""

import json
import math
import os

import pcbnew

import gen_pcb
import hubgeom as g
import hub_circuit as hc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "hub")
gen_pcb.configure(g, hc, OUT, "hub")
MM, kpt, tpt, load, Placer, pads, rot_pointing = (gen_pcb.MM, gen_pcb.kpt, gen_pcb.tpt, gen_pcb.load,
                                                  gen_pcb.Placer, gen_pcb.pads, gen_pcb.rot_pointing)

N, E, S, W = (0, 1), (1, 0), (0, -1), (-1, 0)
LEG_CLEAR = 0.2         # courtyards stay this far from the solder joints of the other side's legs
LEG_PAD = 2.0           # the largest of those joints

# The eFuses' power pins are 0.2 mm from their neighbours, so no class may ask for more.
NETCLASSES = [
    # name     track  clearance  via    drill  nets
    ("Default", 0.20, 0.15, 0.60, 0.30, []),
    ("Power",   0.40, 0.20, 0.80, 0.40, ["+3V3", "+3.3VA", "GND"]),
    ("Input",   1.00, 0.20, 0.80, 0.40, ["VIN", "V_PD", "VBUS"]),     # up to 2 A
]

# ------------------------------------------------------------------ the table ---
LINK_X, LINK_Y = g.link()["header"]
USB = g.sockets()

# Parts with more than two pins: ref -> (x, y, side, pads `frm`, pads `to`, the
# direction `to` lies in from `frm`).  x, y is the footprint's origin.
CHIPS = {
    hc.U1.ref: (20.0, 58.5, "B", ["48"], ["1"], E),                 # pin 1 at the NE corner
    hc.Y1.ref: (30.8, 62.8, "B", ["3"], ["1"], (0.6, 1.0)),         # upright, pin 1 at its NE corner
    hc.J_USB.ref: (*USB["USB"], "B", ["A6", "B6"], ["S1"], N),
    hc.J_PWR.ref: (*USB["PWR"], "B", ["A6", "B6"], ["S1"], N),
    hc.J_LINK.ref: (LINK_X, LINK_Y, "B", ["1", "2", "3", "4", "5", "6"], ["MP"], g.link()["facing"]),
    hc.U3.ref: (12.0, 94.6, "B", ["3", "4"], ["1", "6"], E),        # the data lines run through it, north to south
    hc.U2.ref: (6.0, 92.0, "B", ["5", "4"], ["1", "2", "3"], N),    # in from the north, out to the south
    hc.U4.ref: (31.1, 93.6, "B", ["1", "10"], ["5", "6"], N),       # D+/D- and CC pins toward the socket
    hc.U_HOST.ref: (21.0, 92.5, "B", ["6"], ["5"], E),              # in at the north end, out at the south
    hc.U_PD.ref: (38.6, 87.5, "B", ["6"], ["5"], E),                # south of the PD chip: its passives need the room
    hc.DBG.ref: (12.3, 32.8, "B", ["6"], ["1"], W),
    hc.AUX.ref: (26.5, 32.8, "B", ["3"], ["1"], W),
}

# Two-pin parts: ref -> (x, y, where pin 1 lies seen from pin 2, pin 1's net, pin 2's net).
# The nets are only checked: they catch this table drifting from hub_circuit.py.
PARTS = {
    # --- MCU: decoupling at its supply pins, crystal loads, straps
    "C1": (28.0, 63.3, S, "+3V3", "GND"),
    "C2": (12.25, 56.25, E, "+3V3", "GND"),
    "C3": (23.6, 66.3, S, "+3V3", "GND"),
    "C4": (25.3, 68.3, N, "+3V3", "GND"),
    "C5": (27.7, 56.5, S, "+3.3VA", "GND"),
    "C6": (28.75, 56.5, S, "+3.3VA", "GND"),
    "FB1": (30.8, 56.02, E, "+3V3", "+3.3VA"),
    "C7": (28.3, 59.25, W, "NRST", "GND"),
    "C8": (31.65, 65.95, S, "HEXT_IN", "GND"),
    "C9": (29.95, 59.65, N, "HEXT_OUT", "GND"),
    "R1": (35.2, 36.3, W, "BOOT0", "GND"),
    "R2": (12.25, 54.6, E, "OTGHS_R", "GND"),
    "R10": (19.6, 68.6, N, "+3V3", "PD_PG"),
    "R12": (22.2, 68.6, N, "+3V3", "FLT_PD"),
    "R18": (23.3, 68.6, N, "+3V3", "FLT_HOST"),
    # --- the ADC's dividers: a row each, its tap leaving west to the MCU.  The rows are
    # 2.2 mm apart: a row's link runs above it, and its two ground pads get their vias below
    "C21": (34.0, 60.9, W, "VIN_SENSE", "GND"),
    "R23": (36.1, 60.9, W, "VIN_SENSE", "GND"),
    "R22": (38.2, 60.9, E, "VIN", "VIN_SENSE"),
    "C22": (34.0, 58.7, W, "VPD_SENSE", "GND"),
    "R25": (36.1, 58.7, W, "VPD_SENSE", "GND"),
    "R24": (38.2, 58.7, E, "V_PD", "VPD_SENSE"),
    "C23": (34.0, 56.5, W, "VBUS_SENSE", "GND"),
    "R27": (36.1, 56.5, W, "VBUS_SENSE", "GND"),
    "R26": (38.2, 56.5, E, "VBUS", "VBUS_SENSE"),
    # --- the USB socket: CC resistors, regulator
    "R3": (9.25, 95.4, E, "USB_CC1", "GND"),
    "R5": (9.25, 96.6, E, "USB_CC1", "CC1_SENSE"),
    "R4": (14.75, 95.4, W, "USB_CC2", "GND"),
    "R6": (14.75, 96.6, W, "USB_CC2", "CC2_SENSE"),
    "C10": (6.0, 95.2, W, "VBUS", "GND"),
    "C11": (5.3, 89.0, W, "+3V3", "GND"),
    # --- the host eFuse
    "C18": (22.9, 95.6, W, "VBUS", "GND"),
    "R17": (23.9, 93.2, W, "EN_HOST", "GND"),
    "C17": (17.6, 94.0, E, "ITIMER_HOST", "GND"),
    "R20": (17.6, 92.9, E, "ILM_HOST", "IMON_HOST"),
    "R19": (17.6, 91.8, E, "ILM_HOST", "GND"),
    "R21": (17.6, 90.7, E, "ILM_HOST", "ILIM_HI"),
    "C16": (19.5, 90.0, N, "DVDT_HOST", "GND"),
    # --- VIN, along the channel's edge
    "C19": (19.1, 87.45, S, "VIN", "GND"),
    "D2": (26.8, 88.2, W, "VIN", "GND"),
    "C20": (32.5, 87.3, S, "VIN", "GND"),
    # --- the PD chip
    "C12": (25.75, 91.2, E, "V_PD", "GND"),
    "R8": (26.3, 92.55, W, "PD_SCL", "CH_SCL"),
    "R9": (26.3, 93.65, W, "PD_SDA", "CH_SDA"),
    "R7": (35.95, 92.6, W, "PD_CFG1", "GND"),
    "D1": (40.3, 102.6, S, "V_PD", "GND"),
    # --- the PD eFuse
    "C15": (40.3, 93.175, S, "V_PD", "GND"),
    # its west pins' parts stand in a column beside them, just east of the channel's
    # end; the two on ILM meet level with that pin
    "C14": (36.0, 90.68, S, "ITIMER_PD", "GND"),
    "R14": (36.0, 88.72, S, "ILM_PD", "IMON_PD"),
    "R13": (36.0, 86.75, N, "ILM_PD", "GND"),
    "C13": (36.0, 84.79, N, "DVDT_PD", "GND"),
    "R11": (39.95, 90.3, S, "EN_PD", "GND"),
    "R15": (41.25, 88.75, E, "V_PD", "OVLO_PD"),
    "R16": (41.25, 87.65, W, "OVLO_PD", "GND"),
    # --- lights and buttons
    "R30": (10.9, 42.4, N, "LED_STATUS", "LED_STATUS_A"),
    "R31": (17.9, 42.4, N, "LED_POWER", "LED_POWER_A"),
    "R32": (31.0, 40.3, N, "+3V3", "BTN_USER"),
}
LINK_R_X = 41.3         # the port's two series resistors stand east of it, in line, one hanging
                        # south of its pin and one north; far enough east that the port's two
                        # ground pins get their vias between the pins and the resistors

# What must read back as intended: (ref, pad) -> (x, y).
EXPECT = {
    (hc.U1.ref, "1"): (25.675, 62.25), (hc.U1.ref, "17"): (23.75, 52.825),
    (hc.U1.ref, "33"): (14.325, 54.75), (hc.U1.ref, "49"): (16.25, 64.175),
    (hc.J_USB.ref, "A5"): (10.75, 99.53), (hc.J_USB.ref, "B5"): (13.75, 99.53),
    (hc.J_PWR.ref, "A5"): (31.75, 99.53),
    (hc.U3.ref, "6"): (12.95, 95.7375), (hc.U3.ref, "3"): (11.05, 93.4625),
    (hc.U2.ref, "1"): (5.05, 93.1375), (hc.U2.ref, "5"): (5.05, 90.8625),
    (hc.U4.ref, "5"): (28.4625, 95.6), (hc.U4.ref, "6"): (33.7375, 95.6),
    (hc.U_HOST.ref, "2"): (21.9, 92.725), (hc.U_PD.ref, "9"): (37.7, 87.725),
}


def main():
    gen_pcb.refuse_to_clobber()
    sch = json.load(open(os.path.join(OUT, "symbol_uuids.json")))
    links = sch["symbols"]
    unconnected = {ref: {pin: n.replace("/", "{slash}") for pin, n in pins.items()}
                   for ref, pins in sch["unconnected"].items()}
    board = pcbnew.NewBoard(gen_pcb.PCB)
    board.SetCopperLayerCount(2)
    ds = board.GetDesignSettings()
    ds.m_CopperEdgeClearance = MM(0.25)
    ds.m_MinResolvedSpokes = 1
    placer = Placer(board)
    nets = {}
    for name in list(hc.nets()) + sorted({n for pins in unconnected.values() for n in pins.values()}):
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        nets[name] = ni
    fps, rects = {}, {}

    def make(ref):
        p = hc.PARTS[ref]
        fp = load(p.footprint, board)
        fp.SetReference(ref)
        fp.SetValue(p.value)
        fp.SetPath(pcbnew.KIID_PATH("/" + links[ref]))
        fp.SetSheetname("/")
        fp.SetSheetfile("hub.kicad_sch")
        fp.SetDNP(p.dnp)
        have = {pd.GetNumber() for pd in fp.Pads()}
        missing = set(p.pins) - have
        assert not missing, "%s: circuit pins %s have no pad in %s" % (ref, missing, p.footprint)
        for pd in fp.Pads():
            net = p.pins.get(pd.GetNumber()) or unconnected.get(ref, {}).get(pd.GetNumber())
            if net:
                pd.SetNet(nets[net])
        if p.symbol in ("Device:R", "Device:C", "Device:L", "Device:FerriteBead"):
            fp.Reference().SetVisible(False)        # too close together for names
        fps[ref] = fp
        return fp

    def put(ref, x, y, rot, side, centre=False):
        fp = fps[ref]
        placer.put(fp, x, y, rot, side, record=False)
        if centre:          # x, y is the middle of its pads, not its origin
            cx, cy = gen_pcb.centroid([tpt(pd.GetPosition()) for pd in fp.Pads()])
            placer.put(fp, 2 * x - cx, 2 * y - cy, rot, side, record=False)
        rects[ref] = (side, Placer.courtyard(fp))

    # -- chips and connectors -------------------------------------------------
    for ref, (x, y, side, frm, to, d) in CHIPS.items():
        fp = make(ref)
        put(ref, x, y, rot_pointing(fp, side, frm, to, d), side)

    # -- two-pin parts --------------------------------------------------------
    for ref, (x, y, d, n1, n2) in PARTS.items():
        p = hc.PARTS[ref]
        assert (p.pins["1"], p.pins["2"]) == (n1, n2), \
            "%s is %s -- %s in the circuit, not %s -- %s" % (ref, p.pins["1"], p.pins["2"], n1, n2)
        fp = make(ref)
        put(ref, x, y, rot_pointing(fp, "B", ["2"], ["1"], d), "B")
    # the port's series resistors: pad 2 of each level with the pin it serves
    link_pads = pads(fps[hc.J_LINK.ref])
    series = sorted((link_pads[next(k for k, v in hc.J_LINK.pins.items() if v == p.pins["2"])][1], ref)
                    for ref, p in hc.PARTS.items() if p.block == "link" and p.symbol == "Device:R")
    assert len(series) == 2
    for (y, ref), d in zip(series, (S, N)):
        fp = make(ref)
        rot = rot_pointing(fp, "B", ["2"], ["1"], d)
        put(ref, LINK_R_X, y, rot, "B")
        off = pads(fp)["2"][1] - y
        put(ref, LINK_R_X, y - off, rot, "B")

    # -- the top: LEDs and buttons, centred under their holes in the plate ----
    for name, (x, y) in g.leds().items():
        fp = make(hc.LEDS[name].ref)
        put(hc.LEDS[name].ref, x, y, rot_pointing(fp, "F", ["1"], ["2"], E), "F", centre=True)
    for name, sw in (("BOOT", hc.SW_BOOT), ("USER", hc.SW_USER)):
        fp = make(sw.ref)
        put(sw.ref, *g.buttons()[name], rot_pointing(fp, "F", ["2"], ["1"], N), "F", centre=True)

    left = [r for r in hc.PARTS if r not in fps]
    assert not left, "no place given for: %s" % left

    # -- checks ---------------------------------------------------------------
    problems = []
    for (ref, pin), want in EXPECT.items():
        got = pads(fps[ref])[pin]
        if math.hypot(got[0] - want[0], got[1] - want[1]) > 0.02:
            problems.append("%s pad %s is at (%.3f, %.3f), not (%.3f, %.3f)" % (ref, pin, *got, *want))
    legs = {"F": [], "B": []}       # where a through-hole part's legs come out, by the side they come out on
    for ref, fp in fps.items():
        other = "F" if rects[ref][0] == "B" else "B"
        for pd in fp.Pads():
            if pd.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                x, y = tpt(pd.GetPosition())
                d = max(pcbnew.ToMM(pd.GetSize().x), pcbnew.ToMM(pd.GetSize().y)) + 2 * LEG_CLEAR
                legs[other].append((ref, g._rect(x, y, d, d)))
    outline = g.pcb_outline()
    bare = [("the cable channel", g.channel())] + \
           [("a cover post", g._rect(x, y, 2 * g.POST_KEEPOUT, 2 * g.POST_KEEPOUT)) for x, y in g.posts()]
    refs = list(rects)
    for i, a in enumerate(refs):
        side, r = rects[a]
        if not g._rect_inside(r, outline) and a not in (hc.J_USB.ref, hc.J_PWR.ref):    # the sockets reach the wall
            problems.append("%s is off the board: %s" % (a, [round(v, 2) for v in r]))
        for b in refs[i + 1:]:
            if rects[b][0] == side and g.overlaps(r, rects[b][1]):
                problems.append("%s and %s overlap" % (a, b))
        for owner, leg in legs[side]:
            if owner != a and g.overlaps(r, leg):
                problems.append("%s is on a leg of %s" % (a, owner))
        if side == "B" and a != hc.J_LINK.ref:      # the port stands at the channel's end, by design
            for what, k in bare:
                if g.overlaps(r, k):
                    problems.append("%s is in %s" % (a, what))
    for p in problems:
        print("PLACEMENT:", p)

    # -- outline, keep-outs, planes -------------------------------------------
    for a, b in zip(outline, outline[1:] + outline[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(MM(0.1))
        seg.SetStart(kpt(*a))
        seg.SetEnd(kpt(*b))
        board.Add(seg)

    def zone(points, layer, net=None):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        ol = z.Outline()
        ol.NewOutline()
        for p in points:
            v = kpt(*p)
            ol.Append(v.x, v.y)
        if net is None:         # a rule area: parts keep out, copper may pass
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
             pcbnew.B_Cu)
    cx0, cy0, cx1, cy1 = g.channel()
    cx1 = rects[hc.J_LINK.ref][1][0]        # up to the port, which stands in the channel's end
    zone([(cx0, cy0), (cx1, cy0), (cx1, cy1), (cx0, cy1)], pcbnew.B_Cu)
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        zone(outline, layer, "GND")

    pcbnew.SaveBoard(gen_pcb.PCB, board)
    gen_pcb.set_netclasses(gen_pcb.PCB.replace(".kicad_pcb", ".kicad_pro"), NETCLASSES)
    board = pcbnew.LoadBoard(gen_pcb.PCB)
    board.GetDesignSettings().m_MinResolvedSpokes = 1
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(gen_pcb.PCB, board)
    print("wrote %s: %d footprints, %d nets" % (os.path.relpath(gen_pcb.PCB, HERE), len(board.GetFootprints()), len(nets)))
    gen_pcb.dump_geometry(board)
    assert not problems, "%d placement problems" % len(problems)
    gen_pcb.drc()


if __name__ == "__main__":
    main()
