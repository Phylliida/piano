#!/usr/bin/env python3
"""Cost model: DIY hexagonal isomorphic keyboard (Lumatone-style) on magnetic
Hall-effect switches.  All prices USD.

Source tags:
  [JLC]  verified via POST https://jlcpcb.com/api/overseas-pcb-order/v1/
         shoppingCart/smtGood/selectSmtComponentList  (price at the given qty break)
  [JLCF] read off https://jlcpcb.com/help/article/pcb-assembly-price
  [LUM]  lumatone.io published specification
  [EST]  engineering estimate -- NOT a quote
"""

from math import sqrt, ceil

# ---------------------------------------------------------------- geometry ---
KEY_PITCH = 24.15    # mm centre-to-centre                          [LUM]
KEY_WIDTH = 21.0     # mm across flats                              [LUM]
KEY_AREA = KEY_PITCH ** 2 * sqrt(3) / 2 / 100.0      # 5.05 cm^2 of board per key
OVERHEAD = 1.10      # bezel + gaps + interconnect

# ------------------------------------------------------------------ prices ---
#              price   qty   source
P = {
 "switch":  (0.4284,  70, "Wooting Lekker L60 v2.5, 70-pack $29.99; 4 packs = 280 = $119.96 shipped free [wooting.io cart]"),
 "switch_cheap": (0.3182, 110, "Gateron Aurora Dual Rail 110-pack $35; 50g/70g heavy [mechanicalkeyboards.com]"),
 "switch_floor": (0.27,   1, "TTC KTEK New Star Magnetic, in stock [mechanicalkeyboards.com]"),
 "hall":    (0.0847, 500, "GH39FKSW SOT-23-3L, HE60-compatible, inverted [JLC]"),
 "hall_ti": (0.318,  500, "DRV5053VAQDBZR hall sensor SOT-23 [JLC]"),
 "mux":     (0.106,  500, "TMUX1308PWR 16-channel analog mux [JLC]"),
 "mcu_fe":  (0.948,  100, "STM32G031C6T6, 1x 12-bit ADC / 19 channels [JLC]"),
 "clk":     (0.112,  200, "12 MHz crystal HC-49S [JLC]"),
 "hub":     (0.350,  100, "CH334R 4-port USB 2.0 hub [JLC]"),
 "leddrv":  (0.962,  100, "IS31FL3731 16x9 LED matrix driver, 144 LEDs [JLC]"),
 "led":     (0.070,  200, "[EST] RGB 2020 SMD"),
 "passive": (0.004, 5000, "[EST] 0402 R/C"),
 "usbc":    (0.031,  100, "USB-C 6P receptacle [JLC]"),
 "pcb":     (0.02035,  1, "[JLC live quote] 250x250mm 4-layer qty5: board $12.72 each at $0.02035/cm^2 (625cm^2)"),
 "psu":     (14.00,    1, "[EST] 12 V 5 A brick [LUM: 5 A @ 12 V]"),
 "misc":    (30.0,     1, "[EST] screws, feet, USB, cables"),
 "pcb_eng": (25.0,     1, "[JLC live quote] engineering fee, charged PER PANEL"),
 "ship":    (37.16,    1, "[JLC live quote] shipping on a qty-5 order, US destination"),
}

# ------------------------------------------------------------- 3D printing ---
# Everything mechanical gets printed, so mechanical cost == filament cost and the
# real currency is machine hours.  Print time is estimated from part volume at an
# effective deposition rate -- on a 0.4 mm nozzle at 0.2 mm layers a 2.2 cm^3
# keycap measures ~11 min, which is an effective ~4.5 mm^3/s once walls, infill
# and travel are all accounted for.
PRINT_MM3_PER_S = 4.5
FILAMENT_G_CM3  = 1.24      # PLA / PETG bulk density
FILAMENT_G      = 0.025     # $/g for commodity filament

KEYCAP_CM3 = 2.2            # hollow hex cap, 21 mm across flats, ~8 mm tall
CASE_CM3   = 8.0            # per key: frame + base plate, scaled by board area


def printing(volume_cm3):
    """-> (hours, grams, $filament) for one printed part of this volume."""
    hours = volume_cm3 * 1000.0 / PRINT_MM3_PER_S / 3600.0
    grams = volume_cm3 * FILAMENT_G_CM3
    return hours, grams, grams * FILAMENT_G


# ------------------------------------------------ JLCPCB PCBA fee schedule ---
#           fee, per                                [JLCF]
F_SETUP, F_SOLDER, F_PLACE = 8.18, 0.016, 0.0016     # setup; $/joint; $/joint
F_FEEDER, F_FIXTURE, F_XRAY = 1.53, 16.42, 1.64      # $/unique ext part; per order; $/board
F_LARGE = 57.46                                       # boards > 650 cm^2
F_THT_LABOUR, F_THT_JOINT = 3.50, 0.0173             # $/order; $/joint [JLCF wave solder]


def bom(nkeys, analog=True, curve=False, lit=True, switch="switch"):
    """Materials for one instrument. `curve` = per-key RC curve; `lit` = per-key RGB."""
    rows, printed = [], []
    add = lambda n, q, key, note="": rows.append(
        (n, q, P[key][0], q * P[key][0], P[key][2] + (f" | {note}" if note else "")))

    n_mux  = ceil(nkeys / 16) if analog else 0
    # one front-end MCU per 70 keys: 8 mux channels + overhead at >=2 kHz/key
    n_fe   = max(1, ceil(nkeys / 70))
    n_hub  = 1 if n_fe > 1 else 0        # only needed to merge several MCUs
    n_drv  = max(1, ceil(nkeys / 144)) if lit else 0

    add("Magnetic Hall switches", nkeys, switch)
    if analog:
        add("Hall sensors (SS49E)", nkeys, "hall")
        add("Analog multiplexer 16:1", n_mux, "mux")
    add("Front-end MCU (STM32G031)", n_fe, "mcu_fe")
    add("12 MHz crystals", n_fe, "clk")
    if n_hub:
        add("USB hub (CH334R) + USB-C", n_hub, "hub")
    add("USB-C receptacle", 1, "usbc")
    if lit:
        add("RGB LEDs", nkeys, "led")
        add("LED matrix driver", n_drv, "leddrv")
    add("Passives / decoupling", int(nkeys * 3.0 + 60), "passive")
    if curve:
        add("RC curve caps (per key)", nkeys, "passive", "alternative to digital sensing")
    # Panels are capped at 250x250mm so the order stays in JLCPCB's cheap
    # Economic assembly tier and under both 650 cm^2 surcharges.  Each panel
    # carries its own engineering fee.
    n_panels = max(1, ceil(nkeys / 96))
    cm2 = nkeys * KEY_AREA * OVERHEAD
    cm2_per_panel = 625.0
    add(f"PCB, 4-layer, {n_panels} panel(s)", round(cm2, 1), "pcb",
        f"{cm2:.0f} cm^2 total, {n_panels} x 250x250mm")
    add("PCB engineering fee", n_panels, "pcb_eng")
    add("Shipping (one order)", 1, "ship")
    add("Power supply + regulators", 1, "psu")

    # printed mechanics: priced at filament cost, tracked separately in hours
    cap_h, cap_g, cap_c = printing(KEYCAP_CM3)
    rows.append(("Keycaps (hex, printed)", nkeys, cap_c, nkeys * cap_c,
                 f"{cap_h*60:.1f} min each, {cap_g:.1f} g PLA/PETG"))
    case_cm3 = nkeys * CASE_CM3 / 280.0 * 8.0
    case_h, case_g, case_c = printing(case_cm3)
    rows.append(("Case (printed frame+base)", 1, case_c, case_c,
                 f"{case_h:.0f} h, {case_g:.0f} g PETG"))
    add("Misc hardware", 1, "misc")
    printed = nkeys * KEYCAP_CM3 + case_cm3       # total cm^3 to print
    return rows, cm2, printed


def pcba(nkeys, nboards, unique_parts):
    """One-off JLCPCB assembly charges for a prototype order. [JLCF]

    SMD only.  The switch pins carry no signal -- the switch is a magnet -- so
    they are mechanical hold-down holes marked Do-Not-Populate, not solder
    joints.  THT parts (USB-C, headers) are charged separately by their wave
    soldering rate: $3.5 hand-soldering labour + $0.0173/joint, added once.
    """
    joints = int(nkeys * 1.2 + 200)          # SMD only: sensor/LED/passives/IC pads
    tht_joints = 12                           # USB-C shell + a few connectors
    area_per_board = nkeys * KEY_AREA * OVERHEAD / max(nboards, 1)
    fees = [("PCBA setup", F_SETUP),
            (f"Pre-reflow soldering ({joints} SMD joints)", joints * F_SOLDER),
            (f"SMT placement ({joints} SMD joints)", joints * F_PLACE),
            (f"Feeder loading ({unique_parts} extended parts)", unique_parts * F_FEEDER),
            ("Large-board surcharge", F_LARGE if area_per_board > 650 else 0.0),
            ("SMT fixtures (2)", F_FIXTURE),
            ("X-ray inspection (QFN)", nboards * F_XRAY),
            (f"THT wave-solder labour + {tht_joints} joints",
             F_THT_LABOUR + tht_joints * F_THT_JOINT)]
    return fees, sum(f for _, f in fees)


def report(title, nkeys, analog=True, curve=False, lit=True, volume=25, switch="switch"):
    rows, cm2, printed = bom(nkeys, analog, curve, lit, switch)
    comp = sum(r[3] for r in rows)
    nboards = max(1, ceil(nkeys / 70))
    uniq = 8 + (3 if analog else 0) + (2 if lit else 0)
    fees, lab = pcba(nkeys, nboards, uniq)
    tot_cm3 = printed
    hrs, g, _ = printing(tot_cm3)
    print(f"\n{'='*96}\n{title}\n{nkeys} keys | {cm2:.0f} cm^2 board | {nboards} assembly unit(s), one 462x325mm 4-layer board\n{'='*96}")
    print(f"{'line item':<34}{'qty':>9}{'unit $':>9}{'ext $':>11}   source")
    print("-" * 96)
    for n, q, u, e, s in rows:
        print(f"{n:<34}{q:>9}{u:>9.3f}{e:>11.2f}   {s}")
    print("-" * 96)
    print(f"{'MATERIALS':<34}{'':>9}{'':>9}{comp:>11.2f}")
    for n, f in fees:
        if f:
            print(f"  + {n:<32}{'':>9}{'':>9}{f:>11.2f}")
    print(f"{'ONE-OFF PCBA LABOUR':<34}{'':>9}{'':>9}{lab:>11.2f}")
    print(f"{'FIRST UNIT TOTAL':<34}{'':>9}{'':>9}{comp+lab:>11.2f}")
    print(f"{'  at volume %d, per unit' % volume:<34}{'':>9}{'':>9}{comp+lab/volume:>11.2f}")
    print(f"{'3D PRINTING (not in the $ above)':<34}{'':>9}{'':>9}{hrs:>10.0f}h")
    print(f"  filament already counted above = ${g*FILAMENT_G:.2f}; "
          f"{hrs/24:.1f} days of continuous printing")
    print(f"  cost per key ${(comp+lab/volume)/nkeys:.2f}  |  "
          f"switch+sensor $" + f"{P[switch][0] + (P['hall'][0] if analog else 0):.3f}"
          + f"/key  |  {hrs*60/nkeys:.0f} s print/key")
    return comp + lab / volume


if __name__ == "__main__":
    print(__doc__)
    report("A. 280 keys, Wooting Lekker L60 v2.5 (recommended)", 280)
    report("B. 280 keys, Gateron Aurora dual-rail (cheaper, heavier feel)", 280, switch="switch_cheap")
    report("C. 280 keys, TTC KTEK New Star (cheapest in stock)", 280, switch="switch_floor")
    report("D. 140 keys, Wooting Lekker L60 v2.5", 140)
    report("E.  70 keys, Wooting Lekker L60 v2.5 (one panel)", 70)
