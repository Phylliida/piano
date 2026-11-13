#!/usr/bin/env python3
"""Build the hub's own KiCad libraries, hub/hub.kicad_sym and hub/hub.pretty.

  hub:AT32F405RxT7   symbol      the tile library's (which copies HE60's, GPL-3)
  hub:TPS259470A     symbol      drawn here from TI SLVSFC9C, "Pin Configuration
                                 and Functions": 1 EN/UVLO, 2 OVLO, 3 AUXOFF,
                                 4 FLT, 5 IN, 6 OUT, 7 DVDT, 8 GND, 9 ILM, 10 ITIMER
  hub:CH224A         symbol      drawn here from the WCH CH224 manual V2.1, table
                                 4-1 (ESSOP10): 1 VHV, 2 CFG2/SCL, 3 CFG3/SDA,
                                 4 DP, 5 DM, 6 CC2, 7 CC1, 8 VBUS, 9 CFG1, 10 PG,
                                 exposed pad GND.  KiCad's SSOP-10-1EP footprint
                                 numbers the exposed pad 11, so the symbol does too.
  hub:TI_RPW0010A_QFN-10_2x2mm   footprint, from the land pattern example of TI
                                 drawing RPW0010A (in the same datasheet)

The RPW package, seen from above (the footprint's view):

        1 +--+ |  | +--+ 10         5 and 6 (IN, OUT) are the two bars down
          '--  |  |  --'            the middle; the corner pads are L-shaped,
        2 ---  |5 |6 --- 9          with an arm along the top or bottom edge.
        3 ---  |  |  --- 8          Every gap between pads is 0.2 mm, which is
          .--  |  |  --.            why the board's supply nets may not ask for
        4 +--+ |  | +--+ 7          more clearance than that.

It also starts the KiCad project, hub/hub.kicad_pro, as a copy of the tile's.

    ./kpython.sh gen_hub_lib.py     (any Python 3 will do)
"""

import copy
import json
import os
import uuid

import kisexp as k
from kisexp import S, Sym
from gen_lib import lib_symbol

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "hub")
TILE_SYM = os.path.join(HERE, "pcb", "tile.kicad_sym")

EFUSE_FP = "TI_RPW0010A_QFN-10_2x2mm"


# ------------------------------------------------------------------ symbols ---
def _font(hide=False, justify=None):
    e = S("effects", S("font", S("size", 1.27, 1.27)))
    if justify:
        e.append(S("justify", Sym(justify)))
    if hide:
        e.append(S("hide", Sym("yes")))
    return e


def box_symbol(name, footprint, description, left, right, bottom, half_w=10.16, half_h=10.16):
    """A rectangle with pins on a 2.54 mm grid.  left/right: [(y, number, name,
    type)], bottom: [(x, number, name, type)]."""
    pins = []

    def pin(kind, x, y, angle, number, pname):
        pins.append(S("pin", Sym(kind), Sym("line"), S("at", x, y, angle), S("length", 2.54),
                      S("name", pname, _font()), S("number", str(number), _font())))

    for y, number, pname, kind in left:
        pin(kind, -half_w - 2.54, y, 0, number, pname)
    for y, number, pname, kind in right:
        pin(kind, half_w + 2.54, y, 180, number, pname)
    for x, number, pname, kind in bottom:
        pin(kind, x, -half_h - 2.54, 90, number, pname)
    return S("symbol", name,
             S("pin_names", S("offset", 0.254)),
             S("exclude_from_sim", Sym("no")), S("in_bom", Sym("yes")), S("on_board", Sym("yes")),
             S("property", "Reference", "U", S("at", -half_w, half_h + 1.27, 0), _font(justify="left")),
             S("property", "Value", name, S("at", 1.27, half_h + 1.27, 0), _font(justify="left")),
             S("property", "Footprint", footprint, S("at", 0, 0, 0), _font(hide=True)),
             S("property", "Datasheet", "", S("at", 0, 0, 0), _font(hide=True)),
             S("property", "Description", description, S("at", 0, 0, 0), _font(hide=True)),
             S("symbol", name + "_0_1",
               S("rectangle", S("start", -half_w, half_h), S("end", half_w, -half_h),
                 S("stroke", S("width", 0.254), S("type", Sym("default"))),
                 S("fill", S("type", Sym("background"))))),
             S("symbol", name + "_1_1", *pins),
             S("embedded_fonts", Sym("no")))


def tps259470a():
    # Signal pins are "passive": they meet resistors, capacitors and GPIOs,
    # and ERC has nothing useful to say about those.  The two open-drain
    # outputs say so, and the supply pins are power inputs, so ERC still
    # insists that IN and GND are fed.
    return box_symbol(
        "TPS259470A", "hub:" + EFUSE_FP,
        "eFuse 2.7-23 V, 5.5 A, 28 mOhm: adjustable current limit with current monitor, "
        "reverse current blocking, adjustable OVLO, auto-retry (TI SLVSFC9)",
        left=[(7.62, 5, "IN", "power_in"), (2.54, 1, "EN/UVLO", "passive"),
              (0, 2, "OVLO", "passive"), (-2.54, 7, "DVDT", "passive"),
              (-5.08, 10, "ITIMER", "passive")],
        right=[(7.62, 6, "OUT", "passive"), (2.54, 4, "~{FLT}", "open_collector"),
               (0, 3, "AUXOFF", "open_collector"), (-5.08, 9, "ILM", "passive")],
        bottom=[(0, 8, "GND", "power_in")])


def ch224a():
    return box_symbol(
        "CH224A", "Package_SO:SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm",
        "USB PD 3.2 sink controller, 5-28 V request by resistor, pin levels or I2C "
        "(WCH CH224 manual V2.1); ESSOP10, exposed pad = GND = pad 11",
        left=[(7.62, 1, "VHV", "power_in"), (5.08, 8, "VBUS", "passive"),
              (0, 7, "CC1", "passive"), (-2.54, 6, "CC2", "passive"),
              (-5.08, 4, "DP", "passive"), (-7.62, 5, "DM", "passive")],
        right=[(7.62, 10, "PG", "open_collector"), (2.54, 9, "CFG1", "passive"),
               (0, 2, "CFG2/SCL", "passive"), (-2.54, 3, "CFG3/SDA", "passive")],
        bottom=[(0, 11, "GND", "power_in")])


# ---------------------------------------------------------------- footprint ---
def _uid(*key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "piano-hub/" + "/".join(str(x) for x in key)))


# (number, x, y, width, height): rectangles in mm, KiCad's axes (y down), from
# the land pattern.  A corner pad is two rectangles with the same number.
def rpw_pads():
    pads = []
    for sx, top, bottom, upper, lower in ((-1, "1", "4", "2", "3"), (1, "10", "7", "9", "8")):
        for sy, corner, inner in ((-1, top, upper), (1, bottom, lower)):
            pads.append((corner, sx * 0.9, sy * 0.7, 0.6, 0.3))             # the pin's own pad
            pads.append((corner, sx * 0.725, sy * 0.875, 0.25, 0.65))       # its arm to the edge
            pads.append((inner, sx * 0.9, sy * 0.225, 0.6, 0.25))
    pads.append(("5", -0.25, 0.0, 0.3, 2.4))
    pads.append(("6", 0.25, 0.0, 0.3, 2.4))
    return pads


def rpw_min_gap(pads):
    """Smallest gap between pads of different numbers."""
    best = 9.0
    for i, (n1, x1, y1, w1, h1) in enumerate(pads):
        for n2, x2, y2, w2, h2 in pads[i + 1:]:
            if n1 == n2:
                continue
            dx = abs(x1 - x2) - (w1 + w2) / 2
            dy = abs(y1 - y2) - (h1 + h2) / 2
            gap = max(dx, dy) if min(dx, dy) < 0 else (dx * dx + dy * dy) ** 0.5
            best = min(best, gap)
    return best


def efuse_footprint():
    def text_effects(size=1.0, thick=0.15):
        return S("effects", S("font", S("size", size, size), S("thickness", thick)))

    def prop(key, value, y, layer, hide=False):
        node = S("property", key, value, S("at", 0, y, 0), S("layer", layer))
        if hide:
            node.append(S("hide", Sym("yes")))
        node += [S("uuid", _uid("prop", key)), text_effects()]
        return node

    def line(a, b, layer, width, n):
        return S("fp_line", S("start", *a), S("end", *b),
                 S("stroke", S("width", width), S("type", Sym("solid"))),
                 S("layer", layer), S("uuid", _uid("line", layer, n)))

    def loop(pts, layer, width):
        return [line(pts[i], pts[(i + 1) % len(pts)], layer, width, i) for i in range(len(pts))]

    pads = rpw_pads()
    gap = rpw_min_gap(pads)
    assert abs(gap - 0.2) < 1e-9, "land pattern: smallest gap %.3f, the drawing says 0.2" % gap
    fp = S("footprint", EFUSE_FP, S("version", 20241229), S("generator", "hub_gen_lib"),
           S("generator_version", "9.0"), S("layer", "F.Cu"),
           S("descr", "TI RPW0010A, 10-pin QFN with two power bars, 2 x 2 x 0.75 mm, 0.45 mm pitch "
                      "(TPS25947 datasheet SLVSFC9, land pattern example)"),
           S("tags", "QFN RPW TPS25947 eFuse"),
           prop("Reference", "REF**", -2.4, "F.SilkS"),
           prop("Value", EFUSE_FP, 2.4, "F.Fab"),
           prop("Datasheet", "", 0, "F.Fab", hide=True),
           prop("Description", "", 0, "F.Fab", hide=True),
           S("attr", Sym("smd")))
    # Pin 1 mark: a triangle beside the top left corner, clear of the pads.
    fp.append(S("fp_poly", S("pts", S("xy", -1.45, -1.25), S("xy", -1.75, -1.25), S("xy", -1.45, -1.55)),
                S("stroke", S("width", 0.12), S("type", Sym("solid"))), S("fill", Sym("yes")),
                S("layer", "F.SilkS"), S("uuid", _uid("pin1"))))
    fp += loop([(-1.45, -1.45), (1.45, -1.45), (1.45, 1.45), (-1.45, 1.45)], "F.CrtYd", 0.05)
    fp += loop([(-0.5, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0), (-1.0, -0.5)], "F.Fab", 0.1)
    fp.append(S("fp_text", Sym("user"), "${REFERENCE}", S("at", 0, 0, 90), S("layer", "F.Fab"),
                S("uuid", _uid("fabref")), text_effects(0.4, 0.06)))
    for i, (num, x, y, w, h) in enumerate(pads):
        pad = S("pad", num, Sym("smd"), Sym("rect"), S("at", round(x, 4), round(y, 4)), S("size", w, h),
                S("layers", "F.Cu", "F.Mask", "F.Paste"))
        if num in ("5", "6"):
            # The drawing's stencil covers 82 % of these two: 0.25 x 2.35.
            pad.append(S("solder_paste_margin", -0.025))
        pad.append(S("uuid", _uid("pad", i)))
        fp.append(pad)
    fp.append(S("embedded_fonts", Sym("no")))
    return fp


def main():
    os.makedirs(os.path.join(OUT, "hub.pretty"), exist_ok=True)
    syms = [copy.deepcopy(lib_symbol(TILE_SYM, "AT32F405RxT7")), tps259470a(), ch224a()]
    lib = S("kicad_symbol_lib", S("version", 20241209), S("generator", "hub_gen_lib"),
            S("generator_version", "9.0"), *syms)
    with open(os.path.join(OUT, "hub.kicad_sym"), "w") as fh:
        fh.write(k.dump(lib) + "\n")
    with open(os.path.join(OUT, "hub.pretty", EFUSE_FP + ".kicad_mod"), "w") as fh:
        fh.write(k.dump(efuse_footprint()) + "\n")
    with open(os.path.join(OUT, "sym-lib-table"), "w") as fh:
        fh.write('(sym_lib_table\n\t(version 7)\n\t(lib (name "hub")(type "KiCad")'
                 '(uri "${KIPRJMOD}/hub.kicad_sym")(options "")(descr "project symbols"))\n)\n')
    with open(os.path.join(OUT, "fp-lib-table"), "w") as fh:
        fh.write('(fp_lib_table\n\t(version 7)\n\t(lib (name "hub")(type "KiCad")'
                 '(uri "${KIPRJMOD}/hub.pretty")(options "")(descr "project footprints"))\n)\n')
    # KiCad reads a project's library tables only if there is a project.  It
    # starts as the tile's, which brings the same design rules and DRC
    # severities; gen_hub_pcb.py then sets the hub's own net classes.
    pro = os.path.join(OUT, "hub.kicad_pro")
    if not os.path.exists(pro):
        d = json.load(open(os.path.join(HERE, "pcb", "tile.kicad_pro")))
        d["meta"]["filename"] = "hub.kicad_pro"
        with open(pro, "w") as fh:
            json.dump(d, fh, indent=2)
    print("wrote hub/hub.kicad_sym (%d symbols), hub/hub.pretty (1 footprint), lib tables" % len(syms))


if __name__ == "__main__":
    main()
