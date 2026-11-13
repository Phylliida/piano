#!/usr/bin/env python3
"""Draw hub_circuit.py as a KiCad 9 schematic: hub/hub.kicad_sch.

gen_sch.py does the drawing and the checking (ERC, and KiCad's own netlist of
the result against hub_circuit.nets()); this file only says where on the
sheet each part goes.  Run gen_hub_lib.py first: it makes the hub's symbols.

    ./kpython.sh gen_hub_sch.py
"""

import os

import gen_sch
import hub_circuit as hc

HERE = os.path.dirname(os.path.abspath(__file__))
gen_sch.configure(hc, os.path.join(HERE, "hub"), "hub")

PITCH = 15.24           # between passives in a row
ROW = 45.72             # between rows of them: room for a net label above and below

# Each IC, connector, diode or switch has a place; the resistors and
# capacitors that follow it in hub_circuit.py line up in a row beside it.
#   ref: (its place, where its row starts, how many to a row)
SPOTS = {
    hc.J_USB.ref:  ((40.64, 60.96), (86.36, 60.96), 9),
    hc.U3.ref:     ((109.22, 114.3), None, 0),
    hc.J_PWR.ref:  ((40.64, 190.5), None, 0),
    hc.U4.ref:     ((106.68, 185.42), (81.28, 241.3), 9),
    hc.D_PD.ref:   ((40.64, 241.3), None, 0),
    hc.U1.ref:     ((215.9, 99.06), (287.02, 48.26), 5),
    hc.Y1.ref:     ((292.1, 142.24), (317.5, 142.24), 9),
    hc.U2.ref:     ((365.76, 139.7), (393.7, 142.24), 9),
    hc.FB1.ref:    ((287.02, 185.42), None, 0),
    hc.DBG.ref:    ((330.2, 182.88), None, 0),
    hc.U_PD.ref:   ((187.96, 251.46), (228.6, 251.46), 9),
    hc.U_HOST.ref: ((187.96, 312.42), (228.6, 312.42), 10),
    hc.D_VIN.ref:  ((393.7, 312.42), (381.0, 251.46), 9),
    hc.J_LINK.ref: ((464.82, 50.8), (500.38, 50.8), 9),
    hc.AUX.ref:    ((553.72, 50.8), None, 0),
    hc.LEDS["STATUS"].ref: ((454.66, 127.0), (487.68, 127.0), 9),
    hc.LEDS["POWER"].ref:  ((510.54, 127.0), (543.56, 127.0), 9),
    hc.SW_BOOT.ref: ((457.2, 175.26), None, 0),
    hc.SW_USER.ref: ((502.92, 175.26), (543.56, 175.26), 9),
}

NOTES = [
    ((20.32, 20.32), "USB", "to the computer. 5.1k on each CC pin; the CC voltage tells how much "
                            "current the port offers"),
    ((20.32, 149.86), "PD", "from a charger, which the CH224A asks for 15 V (56k on CFG1)"),
    ((160.02, 20.32), "MCU", "AT32F405, 12 MHz crystal, 3.3 V from the computer's 5 V, SWD pads (not fitted)"),
    ((160.02, 213.36), "POWER", "one eFuse per socket switches it onto VIN, the tiles' supply; "
                                "dividers and current monitors to the ADC"),
    ((441.96, 20.32), "LINK", "the tile port, as on a tile; AUX serial pads (not fitted)"),
    ((441.96, 96.52), "UI", "two LEDs and two buttons, which reach through the case's plate"),
]
FLAGS_AT = (431.8, 312.42)


def layout(sheet):
    for at, title, detail in NOTES:
        sheet.text(title, at)
        sheet.text(detail, (at[0], at[1] + 5.08), size=1.6, bold=False)
    row = None                              # (x, y, per row, how many placed so far)
    for part in hc.PARTS.values():
        if part.ref in SPOTS:
            at, start, per_row = SPOTS[part.ref]
            sheet.place(part, at)
            row = start and [start[0], start[1], per_row, 0]
            continue
        assert row and part.ref[0] in "RC", "%s has no place on the sheet" % part.ref
        x, y, per_row, n = row
        sheet.place(part, (x + (n % per_row) * PITCH, y + (n // per_row) * ROW))
        row[3] += 1
    for i, net in enumerate(hc.PWR_FLAG_NETS):
        sheet.pwr_flag(net, (FLAGS_AT[0] + i * PITCH, FLAGS_AT[1]))


if __name__ == "__main__":
    gen_sch.run(layout, paper="A2")
