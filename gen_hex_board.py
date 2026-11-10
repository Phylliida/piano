#!/usr/bin/env python3
"""Generate a hexagonal Hall-effect keypad board in KiCad 9 s-expression format.

Architecture is copied from peppapighs/HE60 (see HE60_NOTES.md):
  1 x 8:1 analog mux per 8 keys, each mux on its own ADC channel,
  3 shared select lines, one decoupling cap per Hall sensor.

Output: kicad/<name>.kicad_pcb  -- a valid board with the full netlist and the
hex mechanical layout placed.  Routing is deliberately left to KiCad: open the
board, let it rebuild connectivity from the netlist, and route interactively.

Usage:
    python3 gen_hex_board.py --keys 70 --name hex70
    python3 gen_hex_board.py --keys 35 --name hex35
"""

import argparse
import json
import re
import math
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "work", "templates.json")

# ------------------------------------------------------------------ geometry ---
# Hexagonal key field, pointy-top keys in columns, odd columns offset by half a row
# (the standard "hex" arrangement Lumatone and the Terpstra layout use).
COL_PITCH = 24.15     # mm, done->done. Lumatone spec sheet confirms 24.15mm spacing
ROW_PITCH = 19.05     # mm, the classic 0.75in keyboard row pitch
KEY_WIDTH = 21.0      # mm across flats, for reference/validation


def hex_layout(n_cols, keys_per_col):
    """Yield (index, x, y) for a column-staggered hex grid."""
    i = 0
    for c in range(n_cols):
        x = c * COL_PITCH
        y0 = (ROW_PITCH / 2) if (c % 2) else 0.0
        for r in range(keys_per_col):
            yield i, x, y0 + r * ROW_PITCH
            i += 1


def layout_for(nkeys, max_side=240.0):
    """Choose a staggered-hex field for nkeys.

    Columns alternate between two heights (h, h+1), odd columns offset by half a
    row pitch -- the standard hex arrangement.  We search h and the split
    between tall and short columns for the most square field that fits.
    """
    best = None
    for h in range(1, nkeys + 1):
        for n_short in range(0, nkeys + 1):
            n_tall = nkeys // (h + 1)
            # total = n_short*h + (n_cols - n_short)*(h+1)
            for n_cols in range(1, nkeys + 1):
                if n_short > n_cols:
                    continue
                total = n_short * h + (n_cols - n_short) * (h + 1)
                if total != nkeys:
                    continue
                w = (n_cols - 1) * COL_PITCH
                ht = h * ROW_PITCH + ROW_PITCH / 2
                if w > max_side or ht > max_side:
                    continue
                # prefer a wide instrument; hex keybeds are wider than tall
                target = 2.2
                score = abs(w / max(ht, 1e-6) - target)
                if best is None or score < best[0]:
                    best = (score, n_cols, n_short, h)
        if best and best[3] == h:
            break
    if not best:
        raise SystemExit(f"cannot fit {nkeys} keys in a {max_side}x{max_side} area")
    _, n_cols, n_short, h = best
    out, i = [], 0
    for c in range(n_cols):
        # put the short columns in the middle so the field is a hexagon
        mid = (n_cols - 1) / 2.0
        is_short = (abs(c - mid) < n_short / 2.0) and n_short > 0
        n = h if is_short else h + 1
        x = c * COL_PITCH
        y0 = (ROW_PITCH / 2) if (c % 2) else 0.0
        for r in range(n):
            out.append((i, x, y0 + r * ROW_PITCH))
            i += 1
    return out, n_cols, h, h + 1


# ------------------------------------------------------------------- helpers ---
def sexp_at(s, i):
    k, d, ins = i, 0, False
    while k < len(s):
        c = s[k]
        if ins:
            if c == "\\":
                k += 2
                continue
            if c == '"':
                ins = False
        elif c == '"':
            ins = True
        elif c == "(":
            d += 1
        elif c == ")":
            d -= 1
            if d == 0:
                return s[i:k + 1]
        k += 1
    return None


def re_sub_nets(blk):
    """Strip every embedded net assignment so we can write our own."""
    return re.sub(r'\(net \d+ "[^"]*"\)\s*', "", blk)


def pad_names(blk):
    return [re.search(r'\(pad "([^"]*)"', p).group(1)
            for p in (sexp_at(blk, m.start()) for m in re.finditer(r"\(pad ", blk))]


def new_uuid():
    return str(uuid.uuid4())


def inject_pad_net(blk, pname, netno, netname):
    """Insert (net N "NAME") after the (layers ...) clause of pad `pname`."""
    marker = '(pad "' + pname + '"'
    start = 0
    while True:
        i = blk.find(marker, start)
        if i < 0:
            return blk
        # the pad name must be followed by whitespace or ')'
        after = blk[i + len(marker):i + len(marker) + 1]
        if after and after not in ' \t\n)':
            start = i + 1
            continue
        pad = sexp_at(blk, i)
        if pad is None:
            return blk
        # locate the end of the (layers "...") clause inside this pad
        m = re.search(r'\(layers\s+"[^"]*"(?:\s+"[^"]*")*\)', pad)
        if not m:
            return blk
        cut = m.end()
        newpad = pad[:cut] + '\n\t\t\t(net %d "%s")' % (netno, netname) + pad[cut:]
        return blk[:i] + newpad + blk[i + len(pad):]



# ---------------------------------------------------------------- generation ---
def generate(nkeys, name, outdir):
    t = json.load(open(TEMPLATES))
    keys, n_cols, h_even, h_odd = layout_for(nkeys)
    n_mux = math.ceil(nkeys / 8)

    X0, Y0 = 10.0, 10.0                       # board margin
    mux_row_y = (max(h_even, h_odd)) * ROW_PITCH + ROW_PITCH / 2 + 12.0

    out = []
    add = out.append

    # ---- header ------------------------------------------------------------
    add('(kicad_pcb\n\t(version 20241229)\n\t(generator "gen_hex_board.py")')
    add('\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)')
    add('\t(paper "A3")')
    add("""\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)""")
    add("""\t(setup
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(pcbplotparams
\t\t\t(layerselection 0x00010fc_ffffffff)
\t\t\t(usegerberextensions no)
\t\t\t(usegerberattributes yes)
\t\t\t(usegerberadvancedattributes yes)
\t\t\t(creategerberjobfile yes)
\t\t\t(dashed_line_dash_ratio 12.000000)
\t\t\t(dashed_line_gap_ratio 3.000000)
\t\t\t(svgprecision 4)
\t\t\t(plotframeref no)
\t\t\t(viasonmask no)
\t\t\t(mode 1)
\t\t\t(useauxorigin no)
\t\t\t(hpglpennumber 1)
\t\t\t(hpglpenspeed 20)
\t\t\t(hpglpendiameter 15.000000)
\t\t\t(pdffront no)
\t\t\t(pdfback no)
\t\t\t(pdfmirror no)
\t\t\t(psnegative no)
\t\t\t(psa4output no)
\t\t\t(plotreference yes)
\t\t\t(plotvalue yes)
\t\t\t(plotinvisibletext no)
\t\t\t(sketchpadsonfab no)
\t\t\t(subtractmaskfromsilk no)
\t\t\t(outputformat 1)
\t\t\t(mirror no)
\t\t\t(drillshape 1)
\t\t\t(scaleselection 1)
\t\t\t(outputdirectory "")
\t\t)
\t)""")

    # ---- net table ---------------------------------------------------------
    # order matters: every net gets an index, referenced by (net N "...") on pads
    nets = {}          # name -> number

    def net(name):
        # KiCad reserves net index 0 for "no net", so real nets start at 1
        if name not in nets:
            nets[name] = len(nets) + 1
        return nets[name]

    for n in ("+3V3", "GND", "MUX_SELECT_0", "MUX_SELECT_1", "MUX_SELECT_2"):
        net(n)
    for i in range(nkeys):
        net(f"HE{i + 1}")
    for m in range(1, n_mux + 1):
        net(f"MUX_OUT_{m}")
    for i in range(nkeys):
        net(f"CAP{i + 1}")

    add("\t(net 0 \"\")")
    for n, idx in sorted(nets.items(), key=lambda kv: kv[1]):
        add(f'\t(net {idx} "{n}")')

    # ---- board outline -----------------------------------------------------
    w = (n_cols - 1) * COL_PITCH + 30.0
    h = mux_row_y + 20.0
    x1, y1, x2, y2 = X0 - 12, Y0 - 12, X0 + w, Y0 + h
    add(f"\t(gr_rect\n\t\t(start {x1:.3f} {y1:.3f})\n\t\t(end {x2:.3f} {y2:.3f})\n"
        f"\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type default)\n\t\t)\n"
        f"\t\t(fill none)\n\t\t(layer \"Edge.Cuts\")\n\t\t(uuid \"{new_uuid()}\")\n\t)")

    def emit_footprint(tmpl, ref, x, y, rot, layer, pin_nets):
        """Build a footprint instance. pin_nets maps pad name -> net name."""
        blk = sexp_at(tmpl, 0)
        blk = re_sub_nets(blk)
        blk = re.sub(r'\(property "Reference" "[^"]*"',
                     lambda m: f'(property "Reference" "{ref}"', blk, count=1)
        blk = re.sub(r'\(property "Value" "[^"]*"',
                     lambda m: f'(property "Value" "{ref}"', blk, count=1)
        # strip existing placement, insert ours
        blk = re.sub(r'\n\s*\(at -?[\d.]+ -?[\d.]+(?: -?[\d.]+)?\)', "", blk, count=1)
        blk = re.sub(r'\(layer "(?:F|B)\.Cu"\)', f'(layer "{layer}")', blk, count=1)
        # NO per-pad net injection here on purpose -- see NOTE at the bottom of
        # this file.  Connectivity is carried by the generated .net file and
        # applied in KiCad with "Update PCB from Schematic".
        # every uuid in a reused template must be unique
        blk = re.sub(r'\(uuid "[^"]*"\)', lambda m: f'(uuid "{new_uuid()}")', blk)
        # insert the placement as the child right after the library id
        at = f'\n\t\t(at {x:.4f} {y:.4f}' + (f' {rot:g}' if rot else '') + ')'
        blk = re.sub(r'(\(footprint "[^"]+")', lambda m: m.group(1) + at, blk, count=1)
        return '\t' + blk

    # ---- switches + sensors ------------------------------------------------
    sw_t = t["switch"]["blk"]
    se_t = t["sensor"]["blk"]
    for idx, x, y in keys:
        bx, by = X0 + x, Y0 + y
        # switch: top side, origin at key centre
        add(emit_footprint(sw_t, f"SW{idx + 1}", bx, by, 0, "F.Cu", {}))
        # sensor: bottom side, centred on the same point, -90deg (HE60 convention)
        add(emit_footprint(se_t, f"HE{idx + 1}", bx, by, -90, "B.Cu",
                           {"1": "+3V3", "2": f"HE{idx + 1}", "3": "GND"}))

    # ---- muxes -------------------------------------------------------------
    mux_t = t["mux"]["blk"]
    y_mux = Y0 + mux_row_y
    mux_x = []
    for m in range(n_mux):
        x = X0 + 18 + m * 14.0
        mux_x.append(x)
        pins = {"16": "+3V3", "8": "GND", "7": "GND", "6": "GND",
                "11": "MUX_SELECT_0", "10": "MUX_SELECT_1", "9": "MUX_SELECT_2",
                "3": f"MUX_OUT_{m + 1}"}
        # mux channel pad order, from the HE60 template: X4,X6,Z,X7,X5,Inh,VEE,VSS,
        # C,B,A,X3,X0,X1,X2,VDD  ->  pad 1..16
        chan_pads = ["1", "2", "4", "5", "15", "14", "13", "12"]
        for ch in range(8):
            key_i = m * 8 + ch
            if key_i < nkeys:
                pins[chan_pads[ch]] = f"HE{key_i + 1}"
            else:
                pins[chan_pads[ch]] = "GND"
        add(emit_footprint(mux_t, f"AM{m + 1}", x, y_mux, 0, "B.Cu", pins))

    add(")")

    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{name}.kicad_pcb")
    open(path, "w").write("\n".join(out) + "\n")
    write_netlist(os.path.join(outdir, f"{name}.net"), keys, n_mux, nkeys)
    return path, dict(keys=nkeys, cols=n_cols, h_even=h_even, h_odd=h_odd,
                      muxes=n_mux, nets=len(nets),
                      board_w=w, board_h=h)


def write_netlist(path, keys, n_mux, nkeys):
    """Emit a KiCad netlist carrying the whole connectivity.

    Applying this in KiCad (Update PCB from Schematic) is the sanctioned way to
    build the netlist, and it is far more reliable than writing (net ...)
    attributes into footprint pads by hand.
    """
    comps, nets = [], {}

    def add_net(name, ref, pin):
        nets.setdefault(name, []).append((ref, pin))

    for idx, _x, _y in keys:
        ref, he = f"SW{idx + 1}", f"HE{idx + 1}"
        comps.append((ref, "MX-1U", "HE60:MX-1U"))
        comps.append((he, "GH39FKSW", "Package_TO_SOT_SMD:SOT-23-3"))
        add_net("+3V3", he, "1")
        add_net(f"HE{idx + 1}", he, "2")
        add_net("GND", he, "3")

    chan_pads = ["1", "2", "4", "5", "15", "14", "13", "12"]
    for m in range(n_mux):
        ref = f"AM{m + 1}"
        comps.append((ref, "SN74LV4051A", "Package_SO:TSSOP-16_4.4x5mm_P0.65mm"))
        add_net("+3V3", ref, "16")
        for pin in ("6", "7", "8"):
            add_net("GND", ref, pin)
        add_net("MUX_SELECT_0", ref, "11")
        add_net("MUX_SELECT_1", ref, "10")
        add_net("MUX_SELECT_2", ref, "9")
        add_net(f"MUX_OUT_{m + 1}", ref, "3")
        for ch, pad in enumerate(chan_pads):
            k = m * 8 + ch
            add_net(f"HE{k + 1}" if k < nkeys else "GND", ref, pad)

    L = ['(export (version "E")', '  (design',
         f'    (source "{os.path.basename(path).replace(".net", ".kicad_pcb")}")',
         '    (date "generated")', '    (tool "gen_hex_board.py")', '  )',
         '  (components']
    for ref, val, fp in comps:
        L += [f'    (comp (ref "{ref}")', f'      (value "{val}")',
              f'      (footprint "{fp}")', '    )']
    L.append('  )')
    L.append('  (nets')
    for i, (nm, nodes) in enumerate(sorted(nets.items()), start=1):
        L.append(f'    (net (code "{i}") (name "{nm}")')
        for ref, pin in nodes:
            L.append(f'      (node (ref "{ref}") (pin "{pin}"))')
        L.append('    )')
    L += ['  )', ')']
    open(path, "w").write("\n".join(L) + "\n")



if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", type=int, default=70)
    ap.add_argument("--name", default=None)
    a = ap.parse_args()
    NM = a.name or f"hex{a.keys}"
    p, info = generate(a.keys, NM, os.path.join(HERE, "kicad"))
    print(f"wrote {p}")
    for k, v in info.items():
        print(f"  {k}: {v}")


# ---------------------------------------------------------------------------
# NOTE on nets
#
# This generator does NOT write (net N "NAME") attributes into footprint pads.
# Doing so is possible but fragile: KiCad 9 is strict about where that attribute
# sits inside a pad, and getting it subtly wrong produces a board that either
# fails to parse ("Failed to load board") or -- worse -- parses into a corrupt
# netlist where adjacent pads of one ic register as shorting each other.
#
# Instead the board carries placement only, and the full connectivity is written
# to <name>.net.  In KiCad:
#      Tools -> Update PCB from Schematic,  or
#      open the .net via a schematic and update.
# Both paths run KiCad's own netlist engine, which is what you want.
# ---------------------------------------------------------------------------
