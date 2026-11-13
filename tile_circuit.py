"""The tile's circuit: every part and every connection, as data.

gen_sch.py draws it as a KiCad schematic and gen_pcb.py places it on the
board; both read only this file and tilegeom.py.  Connections name the pin
by number AND by the name the symbol gives it, e.g. ("U2", "3", "IN"), and the
generator refuses to run if the symbol disagrees -- a wrong pin number fails
loudly instead of shipping.

Sources for the choices below (verified against datasheets, 2026-10-11):
  AT32F405 pins / UART muxing   Artery DS_AT32F405_402 V2.02 Tables 5 and 9, RM V2.02 Tables 6-1..6-5
  AP63201 buck                  Diodes DS41326 Rev 3-2, Figures 1/20/21, Tables 1 and 3
  MT9102ET supply current       MagnTek MT910X datasheet sec. 10: 6.0 mA typ at 3.3 V
  everything else               copied from HE60 (refs/HE60), a fabricated, working board

Blocks
  keys     40 x (Hall sensor + 100n supply cap + 4.7n output cap)
  muxes    5 x 8:1 analog mux, one per key row, sharing 3 select lines
  mcu      AT32F405 + crystal + decoupling + reset/boot + programming pads
  ports    4 x JST GH 6-pin, one per tile edge, each on its own UART
  power    7-20 V in (passed between tiles) -> 3.7 V buck -> 3.3 V LDO; a
           ferrite splits off the analog rail (VDDA + sensors)

No LEDs (removed 2026-10-20): a lit keyboard of 10 tiles would need far more
power than one port's 2 A can bring in, and the keys alone fit easily.
"""

import tilegeom as g

TITLE = "Hex keyboard tile %dx%d" % (g.COLS, g.ROWS)

# Footprint libraries: KiCad's standard ones by nickname, plus the project's
# own "tile" library (tile.pretty) for the switch.
FP_C0402 = "Capacitor_SMD:C_0402_1005Metric"
FP_C0603 = "Capacitor_SMD:C_0603_1608Metric"
FP_C0805 = "Capacitor_SMD:C_0805_2012Metric"
FP_C1206 = "Capacitor_SMD:C_1206_3216Metric"
FP_R0402 = "Resistor_SMD:R_0402_1005Metric"


class Part:
    def __init__(self, ref, symbol, value, footprint, block, *, lcsc="", mpn="",
                 dnp=False, unit=1):
        self.ref, self.symbol, self.value, self.footprint = ref, symbol, value, footprint
        self.block, self.lcsc, self.mpn, self.dnp, self.unit = block, lcsc, mpn, dnp, unit
        self.pins = {}          # pin number -> net
        self.names = {}         # pin number -> expected pin name (checked)


PARTS = {}
_counters = {}


def part(prefix, symbol, value, footprint, block, **kw):
    _counters[prefix] = _counters.get(prefix, 0) + 1
    p = Part("%s%d" % (prefix, _counters[prefix]), symbol, value, footprint, block, **kw)
    PARTS[p.ref] = p
    return p


def connect(net, *pins):
    """connect("GND", ("U1", "31", "VSS"), ("C3", "2")) -- name optional."""
    for pin in pins:
        ref, num = pin[0], str(pin[1])
        p = PARTS[ref]
        assert num not in p.pins, "%s.%s connected twice (%s, %s)" % (ref, num, p.pins[num], net)
        p.pins[num] = net
        if len(pin) > 2:
            p.names[num] = pin[2]


def cap(value, net1, net2, block, footprint=FP_C0402, **kw):
    c = part("C", "Device:C", value, footprint, block, **kw)
    connect(net1, (c.ref, 1))
    connect(net2, (c.ref, 2))
    return c


def res(value, net1, net2, block, **kw):
    r = part("R", "Device:R", value, FP_R0402, block, **kw)
    connect(net1, (r.ref, 1))
    connect(net2, (r.ref, 2))
    return r


# --------------------------------------------------------------------- keys ---
# Key n (1-based) is tilegeom.keys()[n-1]: column by column, bottom to top.
# Capacitor values carry their voltage rating, so JLC can't fit a part rated
# too low for its rail ("10u/50V" on the 20 V input, not just "10u").
KEYS = g.keys()
for i, c, r, x, y in KEYS:
    n = i + 1
    he = part("HE", "tile:MT9102ET", "MT9102ET", "Package_TO_SOT_SMD:SOT-23-3", "keys")
    connect("+3.3VA", (he.ref, 1, "VCC"))
    connect("HE%d" % n, (he.ref, 2, "OUT"))
    connect("GND", (he.ref, 3, "GND"))
    cap("100n/16V", "+3.3VA", "GND", "keys")         # sensor supply
    cap("4.7n/50V", "HE%d" % n, "GND", "keys")       # sensor output, as HE60

# -------------------------------------------------------------------- muxes ---
# Mux m reads key row m.  The three select lines are shared by every mux, so
# one select value reads 5 keys at once.  Which channel reads which column is
# chosen for the copper: every key's track then runs into its mux pin without
# crossing another (route_board.py).  AM4 is turned a quarter turn, because U1
# and the W and E ports crowd its band, so it has an order of its own.  The
# firmware reads key (column c, row m) at select value CHANNEL_OF_COL[m][c].
CHANNEL_PINS = ["13", "14", "15", "12", "1", "5", "2", "4"]   # 4051 A0..A7
CHANNEL_OF_COL = {m: [6, 7, 4, 5, 2, 3, 1, 0] for m in range(g.ROWS)}
CHANNEL_OF_COL[3] = [0, 1, 3, 2, 5, 4, 7, 6]
assert g.COLS <= 8 and all(sorted(v) == list(range(8)) for v in CHANNEL_OF_COL.values())
for m in range(g.ROWS):
    am = part("AM", "74xx:74HC4051", "SN74LV4051A", "Package_SO:TSSOP-16_4.4x5mm_P0.65mm", "muxes",
              mpn="SN74LV4051APWR")
    connect("+3.3VA", (am.ref, 16, "VCC"))
    connect("GND", (am.ref, 8, "GND"), (am.ref, 7, "VEE"), (am.ref, 6, "~{E}"))
    connect("MUX_S0", (am.ref, 11, "S0"))
    connect("MUX_S1", (am.ref, 10, "S1"))
    connect("MUX_S2", (am.ref, 9, "S2"))
    connect("MUX_OUT%d" % (m + 1), (am.ref, 3, "A"))
    for c in range(g.COLS):
        key = next(k for k in KEYS if k[1] == c and k[2] == m)
        ch = CHANNEL_OF_COL[m][c]
        connect("HE%d" % (key[0] + 1), (am.ref, CHANNEL_PINS[ch], "A%d" % ch))
    cap("100n/16V", "+3.3VA", "GND", "muxes")

# ---------------------------------------------------------------------- mcu ---
# Lock the exact part: an AT32F402 fits the same footprint with different pins.
U1 = part("U", "tile:AT32F405RxT7", "AT32F405RCT7", "Package_QFP:LQFP-64_10x10mm_P0.5mm", "mcu",
          mpn="AT32F405RCT7")
connect("+3V3", (U1.ref, 1, "VDD"), (U1.ref, 36, "VDD"), (U1.ref, 64, "VDD"))
connect("GND", (U1.ref, 31, "VSS"), (U1.ref, 63, "VSS"), (U1.ref, 12, "VSSA"))
# VDDA must sit within 50 mV of VDD (DS Tables 10 and 15), so both come from
# one regulator; VDDA and the sensors take it through a ferrite (see power).
# The sensors share VDDA, so readings stay ratiometric, as on HE60.
connect("+3.3VA", (U1.ref, 13, "VDDA"))
connect("HEXT_IN", (U1.ref, 5, "PF0/HEXT_IN"))
connect("HEXT_OUT", (U1.ref, 6, "PF1/HEXT_OUT"))
connect("NRST", (U1.ref, 7, "NRST"))
connect("OTGHS_R", (U1.ref, 33, "OTGHS1_R"))     # DS Table 9 note 5: 12k 1% to GND
# Signal pins are picked for the copper (route_board.py), among the pins that
# can do the job (DS Table 9).  The three selects and MUX_OUT4 leave U1's top
# side side by side, straight into AM4 next to it; the other four mux outputs
# come up the middle of the board on the front and drop onto PA4-PA7 under U1.
SELECT_PINS = [("26", "PB0"), ("25", "PC5"), ("24", "PC4")]         # MUX_S0, S1, S2
for i, pin in enumerate(SELECT_PINS):
    connect("MUX_S%d" % i, (U1.ref,) + pin)
# MUX_OUT1..5 -> ADC1_IN4, IN5, IN6, IN9, IN7 (IN0-7 = PA0-7, IN8/9 = PB0/1, IN10-15 = PC0-5)
ADC_PINS = [("20", "PA4"), ("21", "PA5"), ("22", "PA6"), ("27", "PB1"), ("23", "PA7")]
ADC_CHANNEL = [4, 5, 6, 9, 7]
for m in range(g.ROWS):
    connect("MUX_OUT%d" % (m + 1), (U1.ref,) + ADC_PINS[m])
# One UART per edge, (TX, RX), each on the side of U1 its port is on.  W and S
# are also UARTs the ROM bootloader listens on (DS Table 5: USART2 PA2/PA3,
# USART3 PC10/PC11).  The IOMUX number of each pin is in RM chapter 6.
UARTS = {"W": (("16", "PA2"), ("17", "PA3")),       # USART2
         "N": (("37", "PC6"), ("38", "PC7")),       # USART6
         "E": (("39", "PC8"), ("40", "PC9")),       # UART8
         "S": (("51", "PC10"), ("52", "PC11"))}     # USART3
for edge, (tx, rx) in UARTS.items():
    connect("%s_TX" % edge, (U1.ref,) + tx)
    connect("%s_RX" % edge, (U1.ref,) + rx)
connect("SWDIO", (U1.ref, 46, "PA13"))
connect("SWCLK", (U1.ref, 49, "PA14"))
connect("BOOT0", (U1.ref, 60, "PF11/BOOT0"))
connect("PA11_PD", (U1.ref, 44, "PA11"))
connect("PA12_PD", (U1.ref, 45, "PA12"))
for _ in range(3):
    cap("100n/16V", "+3V3", "GND", "mcu")            # one per VDD pin
cap("4.7u/10V", "+3V3", "GND", "mcu", footprint=FP_C0603)
cap("100n/16V", "+3.3VA", "GND", "mcu")              # VDDA
cap("1u/16V", "+3.3VA", "GND", "mcu")
cap("100n/16V", "NRST", "GND", "mcu")                # DS Fig. 19
res("3.3k", "BOOT0", "GND", "mcu")                   # DS: pull-down <= 3.3k
res("100k", "PA11_PD", "GND", "mcu")                 # errata ES0011 1.5.2: PA11/12
res("100k", "PA12_PD", "GND", "mcu")                 # float high otherwise
res("12k 1%", "OTGHS_R", "GND", "mcu")               # USB PHY bias, even unused
Y1 = part("Y", "Device:Crystal_GND24", "12MHz", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "mcu",
          mpn="X322512MSB4SI")
connect("HEXT_IN", (Y1.ref, 1))
connect("HEXT_OUT", (Y1.ref, 3))
connect("GND", (Y1.ref, 2), (Y1.ref, 4))
cap("30p/50V", "HEXT_IN", "GND", "mcu")              # HE60's values; crystal CL = 20 pF
cap("30p/50V", "HEXT_OUT", "GND", "mcu")
# Programming: SWD plus BOOT0 (bridge to 3V3 to start the UART bootloader).
# SMD pads on the back, reached with the cover off: a through-hole header
# would sit under the switches and is taller than the 5 mm under the board.
# The pads are in the order their tracks leave U1, so none of them cross.
DBG = part("J", "Connector_Generic:Conn_01x06", "SWD",
           "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical_SMD_Pin1Left", "mcu", dnp=True)
for pin, net in enumerate(["+3V3", "BOOT0", "SWCLK", "NRST", "SWDIO", "GND"], 1):
    connect(net, (DBG.ref, pin))

# -------------------------------------------------------------------- ports ---
# JST GH 6-pin: 1 VIN, 2 GND, 3 data A, 4 data B, 5 GND, 6 VIN.  Power is
# symmetric, so even a pin-reversed cable can never short VIN to GND, and a
# GND wire separates each data wire from VIN, so a crushed cable can only short
# data to ground.  A reversed cable swaps the data pins, which the USART's
# TRPSWAP bit undoes in firmware; the 220R series resistors limit the current
# while two TX pins briefly face each other during that detection.
PORT_PINS = {"1": "VIN", "2": "GND", "5": "GND", "6": "VIN", "MP": "GND"}
PORT = {}
for edge in ("E", "N", "W", "S"):
    j = part("J", "Connector_Generic_MountingPin:Conn_01x06_MountingPin", "PORT_%s" % edge,
             "Connector_JST:JST_GH_SM06B-GHS-TB_1x06-1MP_P1.25mm_Horizontal", "ports",
             mpn="SM06B-GHS-TB")
    PORT[edge] = j.ref
    for pin, net in PORT_PINS.items():
        connect(net, (j.ref, pin))
    connect("%s_A" % edge, (j.ref, 3))
    connect("%s_B" % edge, (j.ref, 4))
    res("220", "%s_TX" % edge, "%s_A" % edge, "ports")
    res("220", "%s_RX" % edge, "%s_B" % edge, "ports")

# -------------------------------------------------------------------- power ---
# VIN 7-20 V arrives on any port and leaves on the others.  SMAJ20A clamps
# below the buck's 35 V absolute maximum (a 24 V TVS clamps at 38.9 V).
#
# Why two stages for 3.3 V: the 40 sensors draw 6.0 mA each (MT9102 datasheet,
# 3.3 V), 240-280 mA in all.  An LDO straight from 5 V would burn ~0.45 W in a
# SOT-23 next to the sensors, inside a PLA case.  So a buck first steps VIN
# down to 3.7 V, and the LDO only drops 0.4 V (~0.15 W) to a quiet 3.3 V.  The
# AP63201 is the variant that stays in PWM at every load (Diodes DS41326),
# so its ripple sits at a fixed 500 kHz instead of bursting.
D1 = part("D", "Diode:SMAJ20A", "SMAJ20A", "Diode_SMD:D_SMA", "power")
# KiCad names this unidirectional TVS's pins A1/A2.  Pin 1 is the cathode: it
# is the bar side of the symbol drawing, and pad 1 (the band) of D_SMA.
connect("VIN", (D1.ref, 1, "A1"))
connect("GND", (D1.ref, 2, "A2"))
U2 = part("U", "Regulator_Switching:AP63201WU", "AP63201WU-7", "Package_TO_SOT_SMD:TSOT-23-6", "power")
connect("FB", (U2.ref, 1, "FB"))
connect("VIN", (U2.ref, 2, "EN"), (U2.ref, 3, "IN"))
connect("GND", (U2.ref, 4, "GND"))
connect("SW", (U2.ref, 5, "SW"))
connect("BST", (U2.ref, 6, "BST"))
BUCK_CIN = cap("10u/50V", "VIN", "GND", "power", footprint=FP_C1206)
cap("100n/16V", "BST", "SW", "power")
# Vout = 0.8 V x (1 + R1/R2) = 0.8 x (1 + 226/62) = 3.72 V.  R2 = 62k and the
# 100p feed-forward cap across R1 are the datasheet's Table 1 values.
res("226k 1%", "+3V7", "FB", "power")
res("62k 1%", "FB", "GND", "power")
cap("100p/50V", "+3V7", "FB", "power")
L1 = part("L", "Device:L", "10u", "Inductor_SMD:L_Changjiang_FNR4030S", "power",
          mpn="FNR4030S100MT")    # Table 1: 6.8u at 3.3 V, 10u at 5 V; load ~0.4 A
connect("SW", (L1.ref, 1))
connect("+3V7", (L1.ref, 2))
cap("22u/25V", "+3V7", "GND", "power", footprint=FP_C0805)
cap("22u/25V", "+3V7", "GND", "power", footprint=FP_C0805)
U3 = part("U", "Regulator_Linear:TLV75733PDBV", "TLV75733PDBV", "Package_TO_SOT_SMD:SOT-23-5", "power")
connect("+3V7", (U3.ref, 1, "IN"), (U3.ref, 3, "EN"))
connect("GND", (U3.ref, 2, "GND"))
connect("+3V3", (U3.ref, 5, "OUT"))
cap("1u/16V", "+3V7", "GND", "power")
cap("2.2u/10V", "+3V3", "GND", "power", footprint=FP_C0603)
cap("10u/10V", "+3V3", "GND", "power", footprint=FP_C0805)
# The analog rail: the same 3.3 V through a ferrite, so VDDA tracks VDD
# (0.05 ohm x 0.3 A = 15 mV) while the MCU's switching noise stays out.
FB1 = part("FB", "Device:FerriteBead", "BLM18PG221SN1D", "Inductor_SMD:L_0603_1608Metric", "power",
           mpn="BLM18PG221SN1D")
connect("+3V3", (FB1.ref, 1))
connect("+3.3VA", (FB1.ref, 2))
cap("10u/10V", "+3.3VA", "GND", "power", footprint=FP_C0805)

# Nets driven only through a passive part (an inductor, a ferrite) or a
# connector: KiCad's ERC needs a PWR_FLAG to accept that they are powered.
PWR_FLAG_NETS = ["VIN", "GND", "+3V7", "+3.3VA"]

# Switches have no circuit: footprint-only on the board (see gen_pcb.py).


def nets():
    """{net: sorted [(ref, pin)]} -- the single connectivity truth."""
    out = {}
    for p in PARTS.values():
        for pin, net in p.pins.items():
            out.setdefault(net, []).append((p.ref, pin))
    return {n: sorted(v) for n, v in sorted(out.items())}


if __name__ == "__main__":
    ns = nets()
    print("%d parts (%d DNP), %d nets, %d pin connections"
          % (len(PARTS), sum(p.dnp for p in PARTS.values()), len(ns), sum(len(v) for v in ns.values())))
    single = [n for n, v in ns.items() if len(v) < 2]
    assert not single, "nets with one pin: %s" % single
    for blk in ("keys", "muxes", "mcu", "ports", "power"):
        print("  %-6s %d parts" % (blk, sum(p.block == blk for p in PARTS.values())))
