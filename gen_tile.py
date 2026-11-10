#!/usr/bin/env python3
"""Generate ONE tile of the Hall-effect keyboard.

State as of the last verified run (306 footprints, 306 unique designators):
  * TRUE HEX FIELD -- odd columns staggered half a row (see tile_layout()).
  * 21 mm keycaps fit: ROW_PITCH 25.0 > the cap's 24.25 mm across-points.
  * 9 columns x 8/7 rows = 68 keys, 9 muxes, 225 x 240 mm.
  * Power section (F1 fuse, U5 ESD, U2/U3 LDOs, C141-C147 bulk), driven rails.
  * Decoupling: 148 caps -- 4.7n per sensor output, 100n per sensor/mux supply,
    100n per MCU VDD pin.
  * Connectivity IS written into the board's pads. Nothing is routed yet, so
    DRC's unconnected_items (499) is the ratnest and is expected.

Still open -- see TILE95.md "Open items":
  * MCU unprogrammable: SWD, BOOT0 and NRST are unconnected.
  * No crystal (pins 5/6). Stock libhmk spins on crm_hext_stable_wait() without
    one, so the chip will not boot.
  * Pin 33 (OTGHS1_R) floating where HE60 fits 12k.
  * Layer count undecided: this is 2-layer, bom_model.py prices 4-layer.

Traps that have each silently shipped a broken board once:
  * emit_footprint()'s `rot` is a DELTA from the template's AUTHORED rotation.
    Zero keeps a part exactly as authored. Mixing angles transposes pads.
  * An omitted `value=` makes Value fall back to the designator (68 switches
    shipped as Value "SW1"). Always pass value=.
  * Pads must be found by walking a footprint block; '(pad "1"' also matches
    '(pad "10"'.
  * (net N "NAME") must be written AFTER a pad's (layers ...) clause.
  * A replacement lambda must re-emit the closing paren of its matched group,
    or the s-expression unbalances and every later step is skipped silently.
  * Match multi-quote layer lists with (layers(?:\s+"[^"]*")+), not "[^"]*".
  * kicad-cli caps DRC reporting at 199 per rule, so before/after comparisons
    at exactly 199 are meaningless.
"""

import argparse
import json
import math
import os
import re
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "work", "templates.json")

# ------------------------------------------------------------------ geometry ---
# Ten columns per tile.  The geometry is constrained, not chosen: the tile has
# to be a unit of a larger instrument, so the pitch across a seam must equal the
# pitch inside a tile.
#
#   outline -> first column centre  =  MARGIN
#   last column centre -> outline   =  MARGIN
#   across a seam the two halves add:  2 * MARGIN
#   therefore  MARGIN = COL_PITCH / 2
#
# Everything is measured from the board outline, so EDGE_INSET is added to the
# absolute positions rather than folded into MARGIN.
COLS = 9                      # ODD on purpose -- see the seam note below
N_SHORT, N_TALL = 7, 8        # odd cols 7 rows, even cols 8 -> 68 keys
KEYS_PER_TILE = (COLS // 2) * N_TALL + (COLS // 2 + 1) * N_SHORT   # 68
CAP_AF = 21.0                 # keycap across the flats
# A regular hexagon's minimum width over ALL rotations is its across-flats, so
# same-column neighbours need a row pitch of at least that plus clearance.
ROW_PITCH = 25.0              # 21mm cap = 24.25 across points, so 24.15
                              # would leave -0.10mm.  25.0 is the minimum.
COL_PITCH = 25.0
PANEL_MAX = 250.0
EDGE_INSET = 0.0
MARGIN = COL_PITCH / 2.0      # 12.5 -- derived; see the seam note
FIELD_W = (COLS - 1) * COL_PITCH
FIELD_H = (N_TALL - 1) * ROW_PITCH
BAND = 40.0                   # two mux rows + MCU + connector
BOARD_W = 2 * EDGE_INSET + 2 * MARGIN + FIELD_W
BOARD_H = 2 * EDGE_INSET + 2 * MARGIN + FIELD_H + BAND
FIELD_X0 = EDGE_INSET + MARGIN
FIELD_Y0 = EDGE_INSET + MARGIN

# The band below the key field holds, in this order: two mux rows, the MCU, then
# J1 hard against the bottom edge.  Every part in the band is positioned from
# these, so the decoupling pass derives its placement from the same numbers
# instead of repeating them (they used to be literals inside generate()).
MUX_PER_ROW = 6
MUX_DX, MUX_DY = 9.0, 12.0
BAND_Y = FIELD_Y0 + FIELD_H + 8.0     # 195.5 -- first mux row centre
MCU_X, MCU_Y = 108.0, BAND_Y + 6.0    # 108.0, 201.5


def mux_xy(m):
    """(x, y) of mux m, packed MUX_PER_ROW to a row."""
    return 30.0 + (m % MUX_PER_ROW) * MUX_DX, BAND_Y + (m // MUX_PER_ROW) * MUX_DY


def tile_layout():
    """Yield (key_index, x, y) for one tile, column by column.

    TRUE HEX FIELD: odd columns are offset half a row pitch from even ones, which
    is what makes the columns interlock into a hex lattice rather than a grid.
    gen_hex_board.py had this; tile_layout() originally dropped it.

    SEAM CONTINUITY, and why COLS is 9 (odd):
      offset(c) = ROW_PITCH/2 if c is odd else 0.
      A seam joins tile A's last column (index COLS-1) to tile B's first (index 0).
      For the key rows to line up, those two columns must have the SAME offset,
      which requires COLS-1 to be even -- i.e. COLS odd.  With an even COLS the
      two boundary columns both land on offset 0 ... which is also equal, but
      wait: with COLS even, COLS-1 is odd, so the last column is offset while the
      next tile's first is not, giving a ROW_PITCH/2 step at every seam.  Odd
      COLS avoids that.
    """
    out, i = [], 0
    for c in range(COLS):
        n = N_TALL if (c % 2 == 0) else N_SHORT
        x = FIELD_X0 + c * COL_PITCH
        y0 = FIELD_Y0 + (ROW_PITCH / 2.0 if (c % 2) else 0.0)
        for r in range(n):
            out.append((i, x, y0 + r * ROW_PITCH))
            i += 1
    return out


# ------------------------------------------------------------------- netlist ---
# ADC pins verified from the HE60 netlist: its 9 mux outputs land on these.
ADC_PINS = ["17", "20", "21", "22", "23", "24", "25", "26", "27", "14", "15", "16"]
# 17=PA3 20=PA4 21=PA5 22=PA6 23=PA7 24=PC4 25=PC5 26=PB0 27=PB1 14=PA0 15=PA1 16=PA2
MUX_SELECT_PINS = ["9", "10", "11"]        # PC1 / PC2 / PC3 (verified against the
                                           # AT32F405 footprint pinfunctions; HE60
                                           # and he60-v2 use C1/C2/C3 too)
USB_PINS = {"34": "USB_D-", "35": "USB_D+"}
MCU_LIB = "Package_QFP:LQFP-64_10x10mm_P0.5mm"

CONN_LIB = "Connector_JST:JST_SH_SM10B-SRSS-TB_1x10-1MP_P1.00mm_Horizontal"
# ^ hand-built from the 4-pin template; see make_connector_footprint()
# Pins 1-2 carry the raw input so F1 sits in series ahead of the 5V rail, which
# is the arrangement HE60 uses: there the fuse goes from a "VBUS" node fed by
# J2 pin 1.  Pins 5-6 are likewise the RAW USB pair, so U5 sits in series
# between the connector and the MCU -- HE60 does the same via J2 pins 2/3.
# Without that the ESD part hangs off a stub and protects nothing.  Pins 9-10
# are spare.
CONN_PINS = {"1": "VBUS", "2": "VBUS", "3": "GND", "4": "GND",
             "5": "USB_PLUG_D+", "6": "USB_PLUG_D-", "7": "GND", "8": "GND"}


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
    return re.sub(r'\(net \d+ "[^"]*"\)\s*', "", blk)


def new_uuid():
    return str(uuid.uuid4())


def make_connector_footprint(npins=10):
    """Build a JST SH side-entry header footprint from the official dimensions.

    Taken from KiCad's own Connector_JST library (reported by audit 2) and
    cross-checked against the JST eSH drawing:

        signal pads   x -4.5 .. +4.5, pitch 1.0, y -2, size 0.6 x 1.55, rot 0
        mounting pegs x +/-5.8, y +1.875,           size 1.2 x 1.8
        courtyard     +/-6.9   silkscreen +/-6.11   fab +/-6.0

    The previous version widened HE60's 4-pin footprint in place, which widened
    only the pads: the body, courtyard and silkscreen stayed at the 4-pin size
    (+/-3.9) and the peg span came out 9.6 mm instead of 11.6 mm, so the real
    pegs would have missed their pads.

    Pad rotation is 0 and the caller must place this at rotation 0 -- the pad
    angles and the footprint angle together determine the rendered orientation,
    and a mismatch transposes every land.
    """
    half = (npins - 1) / 2.0
    L = ['(footprint "Connector_JST:JST_SH_SM%02dB-SRSS-TB_1x%02d-1MP_P1.00mm_Horizontal"'
         % (npins, npins)]
    L.append('\t\t(layer "F.Cu")')
    L.append('\t\t(uuid "%s")' % new_uuid())
    L.append('\t\t(descr "JST SH series connector, %d circuits, 1.0 mm pitch, side '
             'entry, SMT")\n\t\t(tags "connector JST SH side entry")' % npins)
    L.append('\t\t(attr smd)')
    # Reference and Value, in HE60's own J2 layout (which is likewise a B.Cu
    # part).  Without these the connector carries no designator at all: the
    # board is still valid, J1 simply never turns up in a reference lookup.
    for kind, val, y in (("Reference", "REF**", -4.0), ("Value", "JST_SH", -3.98)):
        L.append('\t\t(property "%s" "%s"\n\t\t\t(at 0 %.2f 180)\n'
                 '\t\t\t(layer "%s")\n\t\t\t(uuid "%s")\n'
                 '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
                 '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n'
                 '\t\t\t\t(justify mirror)\n\t\t\t)\n\t\t)'
                 % (kind, val, y, "B.SilkS" if kind == "Reference" else "B.Fab",
                    new_uuid()))

    def rect(layer, hw, y0, y1, w):
        """Closed rectangle outline as four lines."""
        for (x1, yy1, x2, yy2) in (
                (-hw, y0, hw, y0), (hw, y0, hw, y1),
                (hw, y1, -hw, y1), (-hw, y1, -hw, y0)):
            L.append('\t\t(fp_line\n\t\t\t(start %.4f %.4f)\n\t\t\t(end %.4f %.4f)\n'
                     '\t\t\t(stroke (width %.2f) (type default))\n\t\t\t(fill none)\n'
                     '\t\t\t(layer "%s")\n\t\t\t(uuid "%s")\n\t\t)'
                     % (x1, yy1, x2, yy2, w, layer, new_uuid()))

    rect("F.CrtYd", 6.9, -4.0, 4.0, 0.05)     # official courtyard half-width
    rect("F.SilkS", 6.11, -3.0, 3.4, 0.12)    # official silkscreen half-width
    rect("F.Fab",   6.0,  -2.9, 3.3, 0.10)    # official fab half-width

    for i in range(npins):
        L.append('\t\t(pad "%d" smd roundrect (at %.3f -2) (size 0.6 1.55)\n'
                 '\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
                 '\t\t\t(roundrect_rratio 0.25)\n'
                 '\t\t\t(pinfunction "Pin_%d")\n\t\t\t(pintype "passive")\n'
                 '\t\t\t(uuid "%s")\n\t\t)'
                 % (i + 1, (i - half) * 1.0, i + 1, new_uuid()))
    for sx in (-1, 1):
        L.append('\t\t(pad "MP" smd roundrect (at %.3f 1.875) (size 1.2 1.8)\n'
                 '\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
                 '\t\t\t(roundrect_rratio 0.208333)\n'
                 '\t\t\t(pinfunction "MountPin")\n\t\t\t(pintype "passive")\n'
                 '\t\t\t(uuid "%s")\n\t\t)' % (sx * 5.8, new_uuid()))
    L.append('\t)')
    return "\n".join(L)


def flip_to_back(blk):
    """Move every F.* layer reference in a footprint to B.*.

    Needed because a footprint's own pads name their layers explicitly.  Placing
    the footprint on B.Cu while its pads still say F.Cu leaves copper on one side
    and mask on the other -- KiCad flags that as an invalid padstack.
    """
    def fix_list(m):
        names = re.findall(r'"([^"]*)"', m.group(0))
        out = []
        for n in names:
            out.append("B." + n[2:] if n.startswith("F.") else n)
        return "(layers " + " ".join('"%s"' % n for n in out) + ")"

    blk = re.sub(r'\(layers "[^"]*"(?:\s+"[^"]*")*\)', fix_list, blk)
    blk = re.sub(r'\(layer "(F\.[A-Za-z]+)"\)', lambda m: f'(layer "B.{m.group(1)[2:]}")', blk)
    return blk





# Net indices.  KiCad reserves 0 for "no net"; real nets start at 1.  Built once
# from the same lists the generator uses, so the table and the pad attributes can
# never disagree.
def power_section(t, add, nets):
    """Fuse, USB ESD clamp, both LDOs and their input/output bulk capacitance.

    Values and connectivity are HE60's, read out of work/HE60.net; only the
    copper is ours.  HE60's parts, referenced by its own designators:

        F1  BSMD0805-050-24V    VBUS -> +5V, in series ahead of everything
        U1  USBLC6-2SC6         USB_D+/USB_D- clamp, drawn from +5V
        U2  TLV75733PDBV        +5V -> +3.3VA   (analog, feeds the muxes)
        U3  XC6206P332MR-G      +5V -> +3.3V    (digital, feeds the MCU)
        C148, C150 2.2u on +5V     C151 2.2u, C157 10u on +3.3V
        C149 2.2u, C159 1u on +3.3VA

    Deliberately NOT here: the per-rail decoupling that sits next to the load
    (HE60's C154/C155/C156 100n on +3.3V, C158 100n on +3.3VA, C152/C153 30p
    crystal caps).  Those belong to the decoupling pass.

    Placement: the band's free area, x 132..190, y 192..214.  Nothing else is
    there -- the mux rows stop at x 77.75, the MCU spans x 101.3..114.7, and J1
    starts at y 226.5.

    The F1 pin-2 / 5V tie is the one deliberate simplification: the tile's own
    J1 brings the raw input in on VBUS, and F1 links VBUS to 5V, which is
    electrically the in-series arrangement.  It is not a pin-for-pin clone of
    HE60, which feeds VBUS from a separate connector the tile does not have.
    """
    def emit(key, ref, x, y, value, nets_for_part):
        add(emit_footprint(t[key]["blk"], t[key]["lib"], ref, x, y, 0, "B.Cu",
                           value=value, nets=nets_for_part))

    # --- row 1, y 195.5: the fuse and the three active parts ---------------
    emit("fuse", "F1", 133.0, 195.6, "BSMD0805-050-24V", {"1": "VBUS", "2": "5V"})
    emit("usbesd", "U5", 143.0, 195.5, "USBLC6-2SC6",
         {"1": "USB_PLUG_D-", "2": "GND", "3": "USB_D-",
          "4": "USB_D+", "5": "5V", "6": "USB_PLUG_D+"})
    emit("ldo_analog", "U2", 152.0, 195.5, "TLV75733PDBV",
         # pad 3 is /EN and must be tied high or the analog rail never comes up;
         # HE60 ties it to +5V.  Pad 4 is NC and is left unnetted, as in HE60.
         {"1": "5V", "2": "GND", "3": "5V", "5": "+3V3A"})
    emit("ldo_digital", "U3", 161.0, 195.5, "XC6206P332MR-G",
         {"1": "GND", "2": "+3V3", "3": "5V"})

    # --- row 2, y 203.9: +5V bulk, then +3V3 bulk -------------------------
    for ref, x in (("C141", 133.0), ("C142", 137.5),
                   ("C143", 142.0), ("C144", 146.5)):
        emit("cap0603", ref, x, 203.9, "2.2u", {"1": "5V", "2": "GND"})
    emit("cap0603", "C145", 154.0, 203.9, "10u", {"1": "+3V3", "2": "GND"})

    # --- row 3, y 209.0: +3V3A bulk ---------------------------------------
    emit("cap0603", "C146", 152.0, 209.0, "2.2u", {"1": "+3V3A", "2": "GND"})
    emit("cap0603", "C147", 156.5, 209.0, "1u", {"1": "+3V3A", "2": "GND"})

    # Keep ref_pin_nets() agreeing with what was just emitted, so a later call
    # cannot overwrite these pads with some other net.
    nets["F1"] = {"1": "VBUS", "2": "5V"}
    nets["U5"] = {"1": "USB_PLUG_D-", "2": "GND", "3": "USB_D-",
                  "4": "USB_D+", "5": "5V", "6": "USB_PLUG_D+"}
    nets["U2"] = {"1": "5V", "2": "GND", "3": "5V", "5": "+3V3A"}
    nets["U3"] = {"1": "GND", "2": "+3V3", "3": "5V"}
    for ref in ("C141", "C142", "C143", "C144"):
        nets[ref] = {"1": "5V", "2": "GND"}
    nets["C145"] = {"1": "+3V3", "2": "GND"}
    nets["C146"] = {"1": "+3V3A", "2": "GND"}
    nets["C147"] = {"1": "+3V3A", "2": "GND"}


# ---------------------------------------------------------------- decoupling ---
# Values, footprints and connectivity are HE60's, read out of work/HE60.net.
#
# PLACEMENT.  The sensor caps flank the sensor in Y, rotated 90 deg, NOT beside
# it in X.  The X axis is where the switch's locating pegs are: the MX-1U
# template carries two NPTH pads at (+/-5.08, 0) with 1.75 mm drills, and a 0402
# placed at x +/- 4.0 puts its pad inside that hole's 0.25 mm clearance ring --
# 136 hole_clearance and 136 solder_mask_bridge violations, i.e. one pair per
# key-field cap.  Along Y there is nothing in the switch at all, and the nearest
# neighbour is the next row, 25 mm away.
#
# HE60's own offsets were measured and are not copied either: its caps sit
# 2.6 mm from the sensor, where the two courtyards overlap.  HE60 runs no
# courtyard check; this board keeps courtyards_overlap at 0.  See TILE95.md.
DECOUPLING_DY = 4.0          # sensor caps, above and below the sensor centre
DECOUPLING_MUX_DY = 6.0      # mux cap, below its own mux
DECOUPLING_MCU_Y = 212.0     # the MCU's three caps, in a row below it
POWER_CAP_REFS = {f"C{i}" for i in range(141, 148)}   # owned by power_section()


def decoupling_refs(n):
    """n free C designators, skipping the ones the power section already owns."""
    out, i = [], 1
    while len(out) < n:
        ref = f"C{i}"
        i += 1
        if ref not in POWER_CAP_REFS:
            out.append(ref)
    return out


def decoupling_parts(keys, n_mux):
    """[(ref, value, template_key, x, y, rot, {pad: net})] for the whole pass.

    Both the board emitter and write_netlist() consume THIS list, so the copper
    and the netlist cannot drift apart.  rot is a DELTA from the template's own
    authored rotation, as emit_footprint() defines it.

    The pattern is HE60's, from work/HE60.net:
      69 x 4.7n 0402, pad 1 -> HE<n>,  pad 2 -> GND   one per Hall sensor, on its output
      79 x 100n 0402, pad 1 -> +3.3VA, pad 2 -> GND   one per sensor and one per mux,
                                                      plus C158 next to the MCU
       3 x 100n 0402, pad 1 -> +3.3V,  pad 2 -> GND   one per MCU VDD pin (1, 36, 64)

    So this tile gets one 4.7n AND one 100n per sensor, one 100n per mux, and
    three 100n for the MCU's three VDD pins -- 148 caps.  That is the count the
    design calls for, not a mistake: the sensors are the loads the analog rail
    feeds, and HE60 decouples each of them individually.
    """
    refs = iter(decoupling_refs(2 * len(keys) + n_mux + 3))
    out = []

    # one per sensor, across its own output.  The sensor's HE pad is the lower
    # one (the sensor is at 180 deg), so the 4.7n goes below and the 100n above.
    # rot 270 / 90 respectively is not decoration: the caps' Reference fields sit
    # 3.8 mm from the part, and at these two angles each text points AWAY from the
    # sensor.  Pointed the other way they land on the sensor's own silkscreen --
    # 58 silk_overlap warnings, one per 4.7n (measured, see TILE95.md).
    for idx, x, y in keys:
        out.append((next(refs), "4.7n", "cap100n", x, y + DECOUPLING_DY, 270,
                    {"1": f"HE{idx + 1}", "2": "GND"}))

    # one per sensor, on its supply
    for _idx, x, y in keys:
        out.append((next(refs), "100n", "cap100n", x, y - DECOUPLING_DY, 90,
                    {"1": "+3V3A", "2": "GND"}))

    # one per mux, on its supply
    for m in range(n_mux):
        xx, yy = mux_xy(m)
        out.append((next(refs), "100n", "cap100n", xx, yy + DECOUPLING_MUX_DY, 0,
                    {"1": "+3V3A", "2": "GND"}))

    # one per MCU supply pin, on the digital rail
    for dx in (-4.0, 0.0, 4.0):
        out.append((next(refs), "100n", "cap100n", MCU_X + dx, DECOUPLING_MCU_Y, 0,
                    {"1": "+3V3", "2": "GND"}))
    return out


def decoupling_section(t, add, nets, keys, n_mux):
    """Emit the decoupling pass.  Placement rationale lives in decoupling_parts().

    All caps are on B.Cu -- the same side as every part they serve, and the same
    side as the muxes and MCU, so nothing has to reach across the board.
    """
    for ref, value, key, x, y, rot, padnets in decoupling_parts(keys, n_mux):
        add(emit_footprint(t[key]["blk"], t[key]["lib"], ref, x, y, rot, "B.Cu",
                           value=value, nets=padnets))
        nets[ref] = padnets


def build_netno(nkeys, n_mux):
    d, n = {}, 0
    for nm in ("VBUS", "5V", "GND", "+3V3", "+3V3A", "USB_D+", "USB_D-",
               "USB_PLUG_D+", "USB_PLUG_D-",
               "MUX_SELECT_0", "MUX_SELECT_1", "MUX_SELECT_2"):
        n += 1; d[nm] = n
    for i in range(n_mux):
        n += 1; d[f"MUX_OUT_{i + 1}"] = n
    for i in range(nkeys):
        n += 1; d[f"HE{i + 1}"] = n
    n += 1; d["NRST"] = n
    return d


def ref_pin_nets(keys, n_mux):
    """ref -> {pad name: net name} for the whole tile."""
    chan_pads = ["1", "2", "4", "5", "15", "14", "13", "12"]
    m = {}

    def put(ref, pad, netname):
        m.setdefault(ref, {})[pad] = netname

    for idx, _x, _y in keys:
        he = f"HE{idx + 1}"
        put(he, "1", "+3V3A"); put(he, "2", he); put(he, "3", "GND")
    for i in range(n_mux):
        ref = f"AM{i + 1}"
        put(ref, "16", "+3V3A")
        for pin in ("6", "7", "8"):
            put(ref, pin, "GND")
        put(ref, "11", "MUX_SELECT_0"); put(ref, "10", "MUX_SELECT_1")
        put(ref, "9", "MUX_SELECT_2"); put(ref, "3", f"MUX_OUT_{i + 1}")
        for ch, pad in enumerate(chan_pads):
            k = i * 8 + ch
            put(ref, pad, f"HE{k + 1}" if k < len(keys) else "GND")
    put("U1", "1", "+3V3"); put("U1", "36", "+3V3"); put("U1", "64", "+3V3")
    for pin in ("12", "31", "63"):
        put("U1", pin, "GND")
    put("U1", "13", "+3V3A")
    for i in range(n_mux):
        put("U1", ADC_PINS[i], f"MUX_OUT_{i + 1}")
    for pin, nm in zip(MUX_SELECT_PINS, ("MUX_SELECT_0", "MUX_SELECT_1", "MUX_SELECT_2")):
        put("U1", pin, nm)
    put("U1", "34", "USB_D-"); put("U1", "35", "USB_D+"); put("U1", "7", "NRST")
    for pin, nm in CONN_PINS.items():
        put("J1", pin, nm)
    return m


def authored_rotation(blk):
    """The placement rotation the template's pad angles were authored for."""
    m = re.search(r'\n\s*\(at (-?[\d.]+) (-?[\d.]+)(?: (-?[\d.]+))?\)', sexp_at(blk, 0))
    return float(m.group(3) or 0) if m else 0.0


def emit_footprint(t, lib, ref, x, y, rot, layer, value=None, nets=None):
    """Place a template footprint.

    `rot` here means a DELTA from the angle the template was authored at.  The
    pads carry their own angles that only make sense at the authored placement
    rotation, so we add the delta to the printed placement rather than trying to
    rewrite every pad.  Rewriting them is what produced overlapping copper: the
    mux lands are 1.475 x 0.4 mm on a 0.65 mm pitch, so getting the pad angle
    wrong by 90 degrees lays each land across its neighbour and shorts every
    channel.  Passing delta=0 keeps the part exactly as authored and is the
    configuration that DRC accepts.
    """
    blk = re_sub_nets(sexp_at(t, 0))
    base = authored_rotation(blk)
    if layer == "B.Cu":
        blk = flip_to_back(blk)
    # NOTE: the replacement must re-emit the CLOSING PAREN of the property
    # group.  Dropping it silently unbalances the s-expression: sexp_at() then
    # returns None, the pad parser finds nothing, and every net is skipped
    # without a single error being raised.  That is what happened here.
    #
    # NOTE 2: KiCad writes these properties across several lines, with nested
    # groups inside:
    #     (property "Reference" "MX36"
    #         (at 0 3.175 0)
    #         (layer "Dwgs.User")
    #         (effects (font (size 1 1) (thickness 0.15)))
    #     )
    # Matching only the single-line form replaced nothing at all, and the
    # template's own designators shipped to the board: 68 switches all named
    # MX36, 68 sensors all named HE38, nine muxes named AM1, and J1 ended up
    # with no Reference property at all.  KiCad loads such a board happily, so
    # nothing downstream complained.
    #
    # Swap ONLY the value token.  Replacing the whole group is the obvious
    # alternative and it is wrong: the group carries the field's own (at ...),
    # (layer ...) and font, so collapsing it to (property "Reference" "SW1")
    # silently drops every designator's position and layer and KiCad falls back
    # to defaults.  The balanced-paren scan finds the group; the value token is
    # the one quoted string right after the kind.
    def rename(kind, new):
        j = blk.find(f'(property "{kind}" "')
        if j < 0:
            raise AssertionError(f'template has no {kind} property to rename')
        grp = sexp_at(blk, j)
        assert grp is not None, f"unbalanced s-expression at the {kind} property"
        m = re.compile(r'\(property "' + kind + r'" "([^"]*)"').match(grp)
        assert m, f"could not read the {kind} value out of {grp[:60]!r}"
        vs, ve = j + m.start(1), j + m.end(1)
        return blk[:vs] + new + blk[ve:]

    blk = rename("Reference", ref)
    blk = rename("Value", value or ref)
    blk = re.sub(r'\n\s*\(at -?[\d.]+ -?[\d.]+(?: -?[\d.]+)?\)', "", blk, count=1)
    blk = re.sub(r'\(layer "(?:F|B)\.Cu"\)', f'(layer "{layer}")', blk, count=1)
    blk = re.sub(r'\(uuid "[^"]*"\)', lambda m: f'(uuid "{new_uuid()}")', blk)
    abs_rot = (base + rot) % 360
    at = f'\n\t\t(at {x:.4f} {y:.4f}' + (f' {abs_rot:g}' if abs_rot else '') + ')'
    blk = re.sub(r'(\(footprint "[^"]+")', lambda m: m.group(1) + at, blk, count=1)

    # Write each pad's net.  KiCad 9 wants (net N "NAME") AFTER the pad's
    # (layers ...) clause; immediately after the pad name it parses but produces
    # broken connectivity.  Walk the block's pads ONCE, collecting spans, then
    # rewrite back to front so earlier offsets stay valid.  Searching for each
    # pad by name is not safe: '(pad "1"' also matches '(pad "10"'.
    if nets:
        spans, k = [], 0
        while True:
            j = blk.find("(pad ", k)
            if j < 0:
                break
            pad = sexp_at(blk, j)
            if pad is None:
                break
            spans.append((j, pad))
            k = j + len(pad)
        for j, pad in reversed(spans):
            nm = re.search(r'\(pad "([^"]*)"', pad)
            if not nm or nm.group(1) not in nets:
                continue
            netname = nets[nm.group(1)]
            lm = re.search(r'\(layers(?:\s+"[^"]*")+\)', pad)
            if not lm:
                continue
            newpad = (pad[:lm.end()]
                      + f'\n\t\t\t(net {_NETNO[netname]} "{netname}")'
                      + pad[lm.end():])
            blk = blk[:j] + newpad + blk[j + len(pad):]
    return "\t" + blk


# ---------------------------------------------------------------- generation ---
def generate(outdir, name):
    t = json.load(open(TEMPLATES))
    keys = tile_layout()
    globals()["_NETNO"] = build_netno(len(keys), math.ceil(len(keys) / 8))

    n_mux = math.ceil(KEYS_PER_TILE / 8)          # 9
    X0 = Y0 = 0.0

    out = []
    add = out.append
    add('(kicad_pcb\n\t(version 20241229)\n\t(generator "gen_tile.py")')
    add('\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)')
    add('\t(paper "A4")')
    add("""\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(25 "Edge.Cuts" user)
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)""")
    add('\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t\t(allow_soldermask_bridges_in_footprints no)\n\t)')
    # Net table.  KiCad reserves index 0 for "no net", so real nets start at 1.
    # Every pad's (net N "NAME") must refer to an index declared here or the
    # board fails to load or loads with broken connectivity.
    add('\t(net 0 "")')
    for nm, idx in sorted(globals()["_NETNO"].items(), key=lambda kv: kv[1]):
        add(f'\t(net {idx} "{nm}")')

    # ---- board outline -----------------------------------------------------
    add(f'\t(gr_rect\n\t\t(start {X0 + EDGE_INSET:.3f} {Y0 + EDGE_INSET:.3f})\n'
        f'\t\t(end {X0 + BOARD_W - EDGE_INSET:.3f} {Y0 + BOARD_H - EDGE_INSET:.3f})\n'
        f'\t\t(stroke (width 0.05) (type default))\n\t\t(fill none)\n'
        f'\t\t(layer "Edge.Cuts")\n\t\t(uuid "{new_uuid()}")\n\t)')

    # ---- switches + sensors, top of the board ------------------------------
    nets = ref_pin_nets(keys, n_mux)
    for idx, x, y in keys:
        add(emit_footprint(t["switch"]["blk"], "HE60:MX-1U", f"SW{idx + 1}", x, y, 0, "F.Cu",
                           value="MX-1U"))
        add(emit_footprint(t["sensor"]["blk"], "Package_TO_SOT_SMD:SOT-23-3",
                           f"HE{idx + 1}", x, y, -90, "B.Cu", value="GH39FKSW",
                           nets=nets.get(f"HE{idx + 1}")))

    # ---- muxes: 6 + 6 in the band -----------------------------------------
    chan_pads = ["1", "2", "4", "5", "15", "14", "13", "12"]
    for m in range(n_mux):
        xx, yy = mux_xy(m)
        add(emit_footprint(t["mux"]["blk"], "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
                           f"AM{m + 1}", xx, yy, 0, "B.Cu", value="SN74LV4051A",
                           nets=nets.get(f"AM{m + 1}")))

    # ---- MCU --------------------------------------------------------------
    add(emit_footprint(t["mcu"]["blk"], MCU_LIB, "U1", MCU_X, MCU_Y, 0, "B.Cu",
                       value="AT32F405RCT7", nets=nets.get("U1")))

    # ---- connector, bottom edge, in the band -------------------------------
    # Placed on B.Cu to match HE60's known-good board.  Note the switches are on
    # F.Cu and the connector on B.Cu -- they are on OPPOSITE sides.  The connector
    # belongs on the board's solder side, underneath, where the cable can leave
    # through the case.
    conn = make_connector_footprint(10)
    add(emit_footprint(conn, CONN_LIB, "J1", 165.0, BOARD_H - MARGIN + 3.0, 0, "B.Cu",
                       value="SM10B-SRSS-TB", nets=nets.get("J1")))

    # ---- power section -----------------------------------------------------
    # Emitted after every other part so its ref_pin_nets() entries are the last
    # word on those pads; the footprint writer walks the board in text order.
    power_section(t, add, nets)

    # ---- decoupling --------------------------------------------------------
    # Placed last, after the parts it serves, so its position formulas are read
    # against a band that is already fully populated.
    decoupling_section(t, add, nets, keys, n_mux)

    add(")")

    # Inject net assignments into the emitted board so it is self-contained.
    # Doing it here (on the finished text) rather than in emit_footprint keeps
    # one place responsible for the format, and lets us verify the result.
    board = "\n".join(out) + "\n"
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{name}.kicad_pcb")
    open(path, "w").write(board)
    write_netlist(os.path.join(outdir, f"{name}.net"), keys, n_mux)
    return path, dict(keys=len(keys), muxes=n_mux,
                      board_w=BOARD_W, board_h=BOARD_H,
                      col_pitch=COL_PITCH, field_h=FIELD_H, seam_offset=COL_PITCH / 2)


def assign_nets(board, keys, n_mux):
    """Write (net N "NAME") into every pad of the finished board text.

    KiCad 9 wants the attribute AFTER the pad's (layers ...) clause and before
    (pinfunction ...).  Putting it straight after the pad name parses but yields
    a corrupt netlist.  Pads carry no net of their own here -- templates are
    stripped with re_sub_nets() -- so exactly one attribute is written per pad.
    """
    netmap = {}
    n = 0
    for nm in ("5V", "GND", "+3V3", "+3V3A", "USB_D+", "USB_D-",
               "MUX_SELECT_0", "MUX_SELECT_1", "MUX_SELECT_2"):
        n += 1; netmap[nm] = n
    for i in range(n_mux):
        n += 1; netmap[f"MUX_OUT_{i + 1}"] = n
    for i in range(len(keys)):
        n += 1; netmap[f"HE{i + 1}"] = n

    chan_pads = ["1", "2", "4", "5", "15", "14", "13", "12"]
    per_ref = {}                       # ref -> {pad: netname}

    def put(ref, pad, netname):
        per_ref.setdefault(ref, {})[pad] = netname

    for idx, _x, _y in keys:
        he = f"HE{idx + 1}"
        put(he, "1", "+3V3A"); put(he, "2", he); put(he, "3", "GND")
    for m in range(n_mux):
        ref = f"AM{m + 1}"
        put(ref, "16", "+3V3A")
        for pin in ("6", "7", "8"):
            put(ref, pin, "GND")
        put(ref, "11", "MUX_SELECT_0"); put(ref, "10", "MUX_SELECT_1")
        put(ref, "9", "MUX_SELECT_2"); put(ref, "3", f"MUX_OUT_{m + 1}")
        for ch, pad in enumerate(chan_pads):
            k = m * 8 + ch
            put(ref, pad, f"HE{k + 1}" if k < len(keys) else "GND")
    put("U1", "1", "+3V3"); put("U1", "36", "+3V3"); put("U1", "64", "+3V3")
    for pin in ("12", "31", "63"):
        put("U1", pin, "GND")
    put("U1", "13", "+3V3A")
    for i in range(n_mux):
        put("U1", ADC_PINS[i], f"MUX_OUT_{i + 1}")
    for pin, nm in zip(MUX_SELECT_PINS, ("MUX_SELECT_0", "MUX_SELECT_1", "MUX_SELECT_2")):
        put("U1", pin, nm)
    put("U1", "34", "USB_D-"); put("U1", "35", "USB_D+"); put("U1", "7", "NRST")
    for pin, nm in CONN_PINS.items():
        put("J1", pin, nm)

    # operate on the finished text, one footprint at a time
    out = []
    pos = 0
    for m in re.finditer(r'\t\(footprint "', board):
        out.append(board[pos:m.start()])
        raw = sexp_at(board, m.start())
        if raw is None:
            continue
        blk = raw
        rm = re.search(r'\(property "Reference" "([^"]+)"', blk)
        if not rm:
            out.append(blk); pos = m.start() + len(raw); continue
        ref = rm.group(1)
        pins = per_ref.get(ref, {})
        for pname, netname in pins.items():
            idx = board.find(f'(pad "{pname}"', m.start(), m.start() + len(raw))
            if idx < 0:
                continue
            pad = sexp_at(board, idx)
            lay = re.search(r'\(layers(?:\s+"[^"]*")+\)', pad)
            if not lay:
                continue
            cut = pad.find('\n', lay.end())
            if cut < 0:
                cut = lay.end()
            newpad = (pad[:cut] + f'\n\t\t\t(net {netmap[netname]} "{netname}")'
                      + pad[cut:])
            blk = blk.replace(pad, newpad, 1)
        out.append(blk)
        pos = m.start() + len(raw)
    out.append(board[pos:])
    return "".join(out)



def write_netlist(path, keys, n_mux):
    comps, nets = [], {}

    def add_net(name, ref, pin):
        nets.setdefault(name, []).append((ref, pin))

    def comp(ref, val, fp):
        comps.append((ref, val, fp))

    for idx, _x, _y in keys:
        he = f"HE{idx + 1}"
        comp(f"SW{idx + 1}", "MX-1U", "HE60:MX-1U")
        comp(he, "GH39FKSW", "Package_TO_SOT_SMD:SOT-23-3")
        add_net("+3V3A", he, "1")
        add_net(he, he, "2")
        add_net("GND", he, "3")

    chan_pads = ["1", "2", "4", "5", "15", "14", "13", "12"]
    for m in range(n_mux):
        ref = f"AM{m + 1}"
        comp(ref, "SN74LV4051A", "Package_SO:TSSOP-16_4.4x5mm_P0.65mm")
        add_net("+3V3A", ref, "16")
        for pin in ("6", "7", "8"):
            add_net("GND", ref, pin)
        add_net("MUX_SELECT_0", ref, "11")
        add_net("MUX_SELECT_1", ref, "10")
        add_net("MUX_SELECT_2", ref, "9")
        add_net(f"MUX_OUT_{m + 1}", ref, "3")
        for ch, pad in enumerate(chan_pads):
            k = m * 8 + ch
            add_net(f"HE{k + 1}" if k < len(keys) else "GND", ref, pad)

    comp("U1", "AT32F405RCT7", MCU_LIB)
    add_net("+3V3", "U1", "1")
    add_net("+3V3", "U1", "36")
    add_net("+3V3", "U1", "64")
    for p in ("12", "31", "63"):
        add_net("GND", "U1", p)
    add_net("+3V3A", "U1", "13")
    for i in range(n_mux):
        add_net(f"MUX_OUT_{i + 1}", "U1", ADC_PINS[i])
    for pin, netname in zip(MUX_SELECT_PINS, ("MUX_SELECT_0", "MUX_SELECT_1", "MUX_SELECT_2")):
        add_net(netname, "U1", pin)
    add_net("USB_D-", "U1", "34")
    add_net("USB_D+", "U1", "35")
    add_net("NRST", "U1", "7")

    comp("J1", "SM10B-SRSS-TB", CONN_LIB)
    for pin, netname in CONN_PINS.items():
        add_net(netname, "J1", pin)

    # Power section, mirroring power_section().  The .net has to agree with the
    # board: J1 now brings the raw input in on VBUS, F1 links VBUS to 5V, and
    # the two LDOs drive +3V3 and +3V3A off it.
    comp("F1", "BSMD0805-050-24V", "Fuse:Fuse_0805_2012Metric")
    add_net("VBUS", "F1", "1")
    add_net("5V", "F1", "2")
    comp("U5", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6")
    add_net("USB_PLUG_D-", "U5", "1")
    add_net("GND", "U5", "2")
    add_net("USB_D-", "U5", "3")
    add_net("USB_D+", "U5", "4")
    add_net("5V", "U5", "5")
    add_net("USB_PLUG_D+", "U5", "6")
    comp("U2", "TLV75733PDBV", "Package_TO_SOT_SMD:SOT-23-5")
    add_net("5V", "U2", "1")
    add_net("GND", "U2", "2")
    add_net("5V", "U2", "3")
    add_net("+3V3A", "U2", "5")
    comp("U3", "XC6206P332MR-G", "Package_TO_SOT_SMD:SOT-23-3")
    add_net("GND", "U3", "1")
    add_net("+3V3", "U3", "2")
    add_net("5V", "U3", "3")
    for ref, val, netname in (("C141", "2.2u", "5V"), ("C142", "2.2u", "5V"),
                              ("C143", "2.2u", "5V"), ("C144", "2.2u", "5V"),
                              ("C145", "10u", "+3V3"), ("C146", "2.2u", "+3V3A"),
                              ("C147", "1u", "+3V3A")):
        comp(ref, val, "Capacitor_SMD:C_0603_1608Metric")
        add_net(netname, ref, "1")
        add_net("GND", ref, "2")

    # Decoupling pass, mirroring decoupling_section().  Same single source list,
    # so the .net cannot disagree with the board about which cap goes where.
    for ref, value, _key, _x, _y, _rot, padnets in decoupling_parts(keys, n_mux):
        comp(ref, value, "Capacitor_SMD:C_0402_1005Metric")
        for pad, netname in padnets.items():
            add_net(netname, ref, pad)

    L = ['(export (version "E")', '  (design',
         f'    (source "{os.path.basename(path).replace(".net", ".kicad_pcb")}")',
         '    (date "generated")', '    (tool "gen_tile.py")', '  )', '  (components']
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
    ap.add_argument("--name", default="tile95")
    a = ap.parse_args()
    p, info = generate(os.path.join(HERE, "kicad"), a.name)
    print(f"wrote {p}")
    for k, v in info.items():
        print(f"  {k}: {v}")
