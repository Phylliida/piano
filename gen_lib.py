#!/usr/bin/env python3
"""Build the project's own KiCad libraries, pcb/tile.kicad_sym and pcb/tile.pretty.

Everything here is either copied from a working design or derived from a
datasheet, and says which:

  tile:AT32F405RxT7    symbol      copied from HE60 (GPL-3, refs/HE60)
  tile:MT9102ET        symbol      copied from HE60
  tile:SK6812MINI-E    symbol      KiCad's LED:SK6812MINI renumbered to the
                                   OPSCO SK6812MINI-E-012 datasheet (sec. 5),
                                   which is the part LCSC sells as C5149201:
                                   1 GND, 2 DIN, 3 VDD, 4 DOUT
  tile:MX-1U           footprint   copied from HE60: the switch's two locating pegs
  tile:LED_SK6812MINI-E_Reverse    marbastlib's LED_MX_6028R outline (CERN-OHL-P),
                                   pads renumbered as below

The reverse-mount LED, and why its pads are a mirror image
---------------------------------------------------------
KiCad draws every footprint as seen from the side the part sits on.  This LED
sits under the board and shines up through a hole, so from below you see its
BACK.  The datasheet's top view (looking at the light) is therefore mirrored:

    datasheet top view          this footprint (from below, x mirrored)
      VDD .----. DIN              DIN .----. VDD
          |    |                      |    |
     DOUT '----' GND               GND '----' DOUT
"""

import copy
import os

import kisexp as k
from kisexp import S, Sym

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pcb")
HE60_SYM = os.path.join(HERE, "refs/HE60/HE60/lib/HE60.kicad_sym")
HE60_FP = os.path.join(HERE, "refs/HE60/HE60/lib/HE60.pretty")
MARBAST_LED = os.path.join(HERE, "refs/marbastlib/footprints/marbastlib-mx.pretty/LED_MX_6028R.kicad_mod")
SYMBOL_DIR = os.environ.get(
    "KICAD9_SYMBOL_DIR",
    "/nix/store/6ayphbhvpaql7s42qnwxpzl731scj0b1-kicad-symbols-e92aabf81d/share/kicad/symbols")

# Pad positions of the reverse-mount LED, from below (see the docstring).
LED_PADS = {"1": (-2.725, 0.75),    # GND
            "2": (-2.725, -0.75),   # DIN
            "3": (2.725, -0.75),    # VDD
            "4": (2.725, 0.75)}     # DOUT


def lib_symbol(path, name):
    tree = k.parse(open(path).read())
    return next(s for s in k.children(tree, "symbol") if s[1] == name)


def set_prop(sym, key, value):
    for p in k.children(sym, "property"):
        if p[1] == key:
            p[2] = value
            return
    raise KeyError(key)


def sk6812mini_e():
    """KiCad's SK6812MINI symbol with the MINI-E's pin numbers and names."""
    sym = copy.deepcopy(lib_symbol(os.path.join(SYMBOL_DIR, "LED.kicad_sym"), "SK6812MINI"))
    renumber = {"DOUT": ("4", "DOUT"), "VSS": ("1", "GND"), "DIN": ("2", "DIN"), "VDD": ("3", "VDD")}
    seen = set()
    for node in k.walk(sym):
        if k.head(node) == "symbol" and isinstance(node[1], str):
            node[1] = node[1].replace("SK6812MINI", "SK6812MINI-E")
        if k.head(node) == "pin":
            name = k.find(node, "name")
            num = k.find(node, "number")
            new_num, new_name = renumber[name[1]]
            num[1], name[1] = new_num, new_name
            seen.add(new_name)
    assert seen == {"GND", "DIN", "VDD", "DOUT"}, seen
    set_prop(sym, "Value", "SK6812MINI-E")
    set_prop(sym, "Footprint", "tile:LED_SK6812MINI-E_Reverse")
    set_prop(sym, "Description", "RGB LED with integrated controller, reverse mount, "
                                 "OPSCO pin numbering (LCSC C5149201)")
    return sym


def led_footprint():
    fp = k.parse(open(MARBAST_LED).read())
    fp[1] = "LED_SK6812MINI-E_Reverse"
    pads = k.children(fp, "pad")
    assert len(pads) == 4
    for pad in pads:
        at = k.find(pad, "at")
        x, y = at[1], at[2]
        num = next(n for n, (px, py) in LED_PADS.items() if abs(px - x) < 1e-6 and abs(py - y) < 1e-6)
        pad[1] = num
    assert sorted(p[1] for p in pads) == ["1", "2", "3", "4"]
    for node in list(fp):
        if k.head(node) == "descr":
            node[1] = ("SK6812MINI-E reverse mount, from below: 1 GND, 2 DIN, 3 VDD, 4 DOUT "
                       "(OPSCO numbering). Outline from marbastlib LED_MX_6028R (CERN-OHL-P).")
    for p in k.children(fp, "property"):
        if p[1] == "Value":
            p[2] = "SK6812MINI-E"
    return fp


def main():
    os.makedirs(os.path.join(OUT, "tile.pretty"), exist_ok=True)
    syms = [copy.deepcopy(lib_symbol(HE60_SYM, "AT32F405RxT7")),
            copy.deepcopy(lib_symbol(HE60_SYM, "MT9102ET")),
            sk6812mini_e()]
    lib = S("kicad_symbol_lib", S("version", 20241209), S("generator", "tile_gen_lib"),
            S("generator_version", "9.0"), *syms)
    with open(os.path.join(OUT, "tile.kicad_sym"), "w") as fh:
        fh.write(k.dump(lib) + "\n")

    mx = k.parse(open(os.path.join(HE60_FP, "MX-1U.kicad_mod")).read())
    with open(os.path.join(OUT, "tile.pretty", "MX-1U.kicad_mod"), "w") as fh:
        fh.write(k.dump(mx) + "\n")
    with open(os.path.join(OUT, "tile.pretty", "LED_SK6812MINI-E_Reverse.kicad_mod"), "w") as fh:
        fh.write(k.dump(led_footprint()) + "\n")

    # Project library tables, so KiCad's GUI finds the "tile" libraries.
    with open(os.path.join(OUT, "sym-lib-table"), "w") as fh:
        fh.write('(sym_lib_table\n\t(version 7)\n\t(lib (name "tile")(type "KiCad")'
                 '(uri "${KIPRJMOD}/tile.kicad_sym")(options "")(descr "project symbols"))\n)\n')
    with open(os.path.join(OUT, "fp-lib-table"), "w") as fh:
        fh.write('(fp_lib_table\n\t(version 7)\n\t(lib (name "tile")(type "KiCad")'
                 '(uri "${KIPRJMOD}/tile.pretty")(options "")(descr "project footprints"))\n)\n')
    print("wrote pcb/tile.kicad_sym (%d symbols), pcb/tile.pretty (2 footprints), lib tables"
          % len(syms))


if __name__ == "__main__":
    main()
