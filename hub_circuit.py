"""The hub's circuit: every part and every connection, as data.

The hub snaps onto a tile's east edge and plugs into that tile's east port.
It has two USB-C sockets and does three jobs:

  USB   to the computer: USB MIDI and a serial console (the keyboard's only
        data connection), and the hub's own 3.3 V
  PWR   from a USB-C charger: asks it for 15 V, for keyboards too big for
        the computer's port to feed
  LINK  to the tile: the same UART link the tiles use among themselves, and
        the tiles' supply (VIN), switched and current-limited here

gen_hub_sch.py draws this as a schematic and gen_hub_pcb.py places and routes
it; both read only this file and hubgeom.py.

Sources (datasheets read 2026-11-12):
  AT32F405 pins                 Artery DS_AT32F405_402 V2.02, Tables 5 and 9
  TPS259470A eFuse              TI SLVSFC9C: pin table, sec. 6.3, 7.3, 8
  CH224A USB PD sink            WCH CH224 manual V2.1: tables 4-1, 5-1, sec. 5.2.3, 6
  USBLC6-2, Type-C Rd           ST DS4491; USB Type-C spec 4.5.1.2.1 (Rd = 5.1k)
  MCU support parts             as on the tile (tile_circuit.py), which copies HE60

Blocks
  mcu     AT32F405 + crystal + decoupling + reset/boot + SWD pads + 3.3 V LDO
  usb     USB socket to the computer: ESD clamp, CC pull-downs, CC sensing
  pd      PWR socket + CH224A, which negotiates 15 V
  power   two eFuses that switch USB 5 V or PWR 15 V onto VIN; measuring
  link    the tile port, and console pads
  ui      two LEDs, two buttons

Power rules built into the circuit
  - The MCU runs from the computer's USB only.  With no computer there is
    nobody to send notes to, so the tiles stay off: both eFuses' enables are
    pulled low until firmware raises them.
  - Each eFuse blocks reverse current (TPS25947 sec. 7.3.7), so the two inputs
    can never feed each other, whatever the firmware does.
  - The PWR path cuts off above 17.8 V, so a charger that offers only 20 V
    can't overrun the tiles' 20 V limit.
"""

from circuit import Circuit, FP_C0402, FP_C0603, FP_C1206, FP_R0402

TITLE = "Hex keyboard hub"

_circuit = Circuit()
PARTS = _circuit.parts
part, connect, cap, res, nets = (_circuit.part, _circuit.connect, _circuit.cap, _circuit.res,
                                 _circuit.nets)

FP_USBC = "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12"
FP_EFUSE = "hub:TI_RPW0010A_QFN-10_2x2mm"

# ---------------------------------------------------------------------- mcu ---
# Every MCU signal: net -> (pin number, pin name).  firmware/hub/board.h is a
# copy of this table.
PINS = {
    "USB_DM": ("44", "PA11"), "USB_DP": ("45", "PA12"),          # OTGFS1, also the ROM's USB DFU
    "LINK_TX": ("16", "PA2"), "LINK_RX": ("17", "PA3"),          # USART2
    "AUX_TX": ("51", "PC10"), "AUX_RX": ("52", "PC11"),          # USART3, a ROM bootloader UART
    "PD_SCL": ("58", "PB6"), "PD_SDA": ("59", "PB7"),            # I2C1 to the CH224A
    "PD_PG": ("57", "PB5"),                                      # low = the charger agreed
    "EN_PD": ("30", "PB12"), "EN_HOST": ("32", "PB13"),          # high = eFuse on
    "ILIM_HI": ("28", "PB2"),                                    # open drain; low = 1.43 A from USB
    "FLT_PD": ("61", "PB8"), "FLT_HOST": ("62", "PB9"),          # low = fault
    "VIN_SENSE": ("8", "PC0"), "VPD_SENSE": ("9", "PC1"),        # ADC1_IN10, IN11
    "VBUS_SENSE": ("10", "PC2"), "IMON_PD": ("11", "PC3"),       # ADC1_IN12, IN13
    "IMON_HOST": ("14", "PA0"),                                  # ADC1_IN0
    "CC1_SENSE": ("15", "PA1"), "CC2_SENSE": ("20", "PA4"),      # ADC1_IN1, IN4
    "LED_STATUS": ("37", "PC6"), "LED_POWER": ("38", "PC7"),     # high = lit
    "BTN_USER": ("2", "PC13"),                                   # low = pressed
    "SWDIO": ("46", "PA13"), "SWCLK": ("49", "PA14"),
    "BOOT0": ("60", "PF11/BOOT0"),
}
# What the ADC sees, for the firmware.
VIN_DIVIDER = (100e3, 15e3)         # VIN and V_PD: top, bottom
VBUS_DIVIDER = (100e3, 100e3)
IMON_GAIN = 182e-6                  # A per A out of the eFuse's ILM pin (GIMON)
R_ILM_PD = 1650.0                   # ohm: limit 3334 / R = 2.02 A
R_ILM_HOST = 6650.0                 # ohm: 0.50 A
R_ILM_HOST_HI = 3600.0              # ohm, in parallel when ILIM_HI is low: 1.43 A

U1 = part("U", "hub:AT32F405RxT7", "AT32F405RCT7", "Package_QFP:LQFP-64_10x10mm_P0.5mm", "mcu",
          mpn="AT32F405RCT7")
connect("+3V3", (U1.ref, 1, "VDD"), (U1.ref, 36, "VDD"), (U1.ref, 64, "VDD"))
connect("GND", (U1.ref, 31, "VSS"), (U1.ref, 63, "VSS"), (U1.ref, 12, "VSSA"))
connect("+3.3VA", (U1.ref, 13, "VDDA"))
connect("HEXT_IN", (U1.ref, 5, "PF0/HEXT_IN"))
connect("HEXT_OUT", (U1.ref, 6, "PF1/HEXT_OUT"))
connect("NRST", (U1.ref, 7, "NRST"))
connect("OTGHS_R", (U1.ref, 33, "OTGHS1_R"))     # DS Table 9 note 5: 12k 1% to GND
for net, pin in PINS.items():
    connect(net, (U1.ref,) + pin)
for _ in range(3):
    cap("100n/16V", "+3V3", "GND", "mcu")            # one per VDD pin
cap("4.7u/10V", "+3V3", "GND", "mcu", footprint=FP_C0603)
cap("100n/16V", "+3.3VA", "GND", "mcu")              # VDDA
cap("1u/16V", "+3.3VA", "GND", "mcu")
cap("100n/16V", "NRST", "GND", "mcu")                # DS Fig. 19
res("3.3k", "BOOT0", "GND", "mcu")                   # DS: pull-down <= 3.3k
res("12k 1%", "OTGHS_R", "GND", "mcu")               # USB PHY bias, even with the HS port unused
Y1 = part("Y", "Device:Crystal_GND24", "12MHz", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "mcu",
          mpn="X322512MSB4SI")
connect("HEXT_IN", (Y1.ref, 1))
connect("HEXT_OUT", (Y1.ref, 3))
connect("GND", (Y1.ref, 2), (Y1.ref, 4))
cap("30p/50V", "HEXT_IN", "GND", "mcu")              # the tile's values; crystal CL = 20 pF
cap("30p/50V", "HEXT_OUT", "GND", "mcu")
# 3.3 V from the computer's 5 V.  The hub draws about 40 mA, so the LDO burns
# 70 mW.  USB allows at most 10 uF across VBUS at plug-in (USB 2.0 sec.
# 7.2.4.1): 1 uF here, 1 uF at the eFuse, and the LDO limits what it puts
# into its output capacitors -- so those are smaller than the tile's.
U2 = part("U", "Regulator_Linear:TLV75733PDBV", "TLV75733PDBV", "Package_TO_SOT_SMD:SOT-23-5", "mcu")
connect("VBUS", (U2.ref, 1, "IN"), (U2.ref, 3, "EN"))
connect("GND", (U2.ref, 2, "GND"))
connect("+3V3", (U2.ref, 5, "OUT"))
cap("1u/16V", "VBUS", "GND", "mcu")
cap("2.2u/10V", "+3V3", "GND", "mcu", footprint=FP_C0603)
# VDDA through a ferrite, as on the tile: the ADC only measures supply
# voltages and currents here, but VDDA must stay within 50 mV of VDD.
FB1 = part("FB", "Device:FerriteBead", "BLM18PG221SN1D", "Inductor_SMD:L_0603_1608Metric", "mcu",
           mpn="BLM18PG221SN1D")
connect("+3V3", (FB1.ref, 1))
connect("+3.3VA", (FB1.ref, 2))
# SWD pads on the back, as on the tile, in the same order.
DBG = part("J", "Connector_Generic:Conn_01x06", "SWD",
           "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical_SMD_Pin1Left", "mcu", dnp=True)
for pin, net in enumerate(["+3V3", "BOOT0", "SWCLK", "NRST", "SWDIO", "GND"], 1):
    connect(net, (DBG.ref, pin))

# ---------------------------------------------------------------------- usb ---
# The socket to the computer.  A USB-C device shows 5.1k (Rd) on both CC pins;
# the voltage the host's pull-up leaves across it tells how much current the
# port offers (Type-C spec Table 4-36: under 0.66 V the USB default, up to
# 1.23 V 1.5 A, above that 3 A), which the firmware reads through 10k.
# Pins A6/B6 and A7/B7 are the two rows' copies of D+ and D-, joined here.
J_USB = part("J", "Connector:USB_C_Receptacle_USB2.0_16P", "USB", FP_USBC, "usb",
             mpn="TYPE-C-31-M-12")
for pin in ("A1", "A12", "B1", "B12"):
    connect("GND", (J_USB.ref, pin, "GND"))
connect("GND", (J_USB.ref, "S1", "SHIELD"))
for pin in ("A4", "A9", "B4", "B9"):
    connect("VBUS", (J_USB.ref, pin, "VBUS"))
connect("USB_CC1", (J_USB.ref, "A5", "CC1"))
connect("USB_CC2", (J_USB.ref, "B5", "CC2"))
connect("USB_DP", (J_USB.ref, "A6", "D+"), (J_USB.ref, "B6", "D+"))
connect("USB_DM", (J_USB.ref, "A7", "D-"), (J_USB.ref, "B7", "D-"))
res("5.1k 1%", "USB_CC1", "GND", "usb")
res("5.1k 1%", "USB_CC2", "GND", "usb")
res("10k", "USB_CC1", "CC1_SENSE", "usb")
res("10k", "USB_CC2", "CC2_SENSE", "usb")
# ESD clamp, right behind the socket.  Each data line passes through two pins
# of the package (1 and 6, 3 and 4), which are the same node inside.
U3 = part("U", "Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6", "usb")
connect("USB_DP", (U3.ref, 1, "I/O1"), (U3.ref, 6, "I/O1"))
connect("USB_DM", (U3.ref, 3, "I/O2"), (U3.ref, 4, "I/O2"))
connect("GND", (U3.ref, 2, "GND"))
connect("VBUS", (U3.ref, 5, "VBUS"))

# ----------------------------------------------------------------------- pd ---
# The PWR socket and the CH224A, which talks USB Power Delivery to the charger
# and asks for the voltage its CFG1 resistor names: 56k = 15 V (manual Table
# 5-1).  A charger with no 15 V gives the nearest lower step; one that speaks
# no fast-charge protocol at all stays at 5 V.  In this resistor mode the chip
# also answers on I2C (address 0x22 or 0x23), where the firmware reads what
# was agreed and can ask for another voltage.
# VHV and VBUS are both the socket's supply pin (pin 8 "must be shorted to
# VHV"); both stand 32 V.  The exposed pad is the chip's ground: pad 11 in
# KiCad's footprint.
J_PWR = part("J", "Connector:USB_C_Receptacle_USB2.0_16P", "PWR", FP_USBC, "pd",
             mpn="TYPE-C-31-M-12")
for pin in ("A1", "A12", "B1", "B12"):
    connect("GND", (J_PWR.ref, pin, "GND"))
connect("GND", (J_PWR.ref, "S1", "SHIELD"))
for pin in ("A4", "A9", "B4", "B9"):
    connect("V_PD", (J_PWR.ref, pin, "VBUS"))
connect("PD_CC1", (J_PWR.ref, "A5", "CC1"))
connect("PD_CC2", (J_PWR.ref, "B5", "CC2"))
connect("PD_DP", (J_PWR.ref, "A6", "D+"), (J_PWR.ref, "B6", "D+"))
connect("PD_DM", (J_PWR.ref, "A7", "D-"), (J_PWR.ref, "B7", "D-"))
U4 = part("U", "hub:CH224A", "CH224A", "Package_SO:SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm", "pd",
          lcsc="C42459160", mpn="CH224A")
connect("V_PD", (U4.ref, 1, "VHV"), (U4.ref, 8, "VBUS"))
connect("GND", (U4.ref, 11, "GND"))
connect("CH_SCL", (U4.ref, 2, "CFG2/SCL"))
connect("CH_SDA", (U4.ref, 3, "CFG3/SDA"))
connect("PD_DP", (U4.ref, 4, "DP"))
connect("PD_DM", (U4.ref, 5, "DM"))
connect("PD_CC2", (U4.ref, 6, "CC2"))
connect("PD_CC1", (U4.ref, 7, "CC1"))
connect("PD_CFG1", (U4.ref, 9, "CFG1"))
connect("PD_PG", (U4.ref, 10, "PG"))
cap("1u/50V", "V_PD", "GND", "pd", footprint=FP_C0603)
res("56k 1%", "PD_CFG1", "GND", "pd")                # 15 V
# The chip pulls SCL and SDA up itself.  It and the MCU are powered from
# different sockets, so either can be on with the other off: 1k in each line
# limits what flows into the unpowered one.
res("1k", "PD_SCL", "CH_SCL", "pd")
res("1k", "PD_SDA", "CH_SDA", "pd")
res("100k", "+3V3", "PD_PG", "pd")                   # PG is open drain
# TVS as on the tiles: clamps the plug-in ringing of a long charger cable
# below the eFuse's 28 V absolute maximum.  Pin 1 is the cathode.
D_PD = part("D", "Diode:SMAJ20A", "SMAJ20A", "Diode_SMD:D_SMA", "pd")
connect("V_PD", (D_PD.ref, 1, "A1"))
connect("GND", (D_PD.ref, 2, "A2"))

# -------------------------------------------------------------------- power ---
# Two TPS259470A eFuses, outputs joined on VIN.  Each one switches its input
# on and off (EN), ramps the output up slowly (dVdt), limits the current
# (ILM), blocks current flowing backwards, and reports the current it carries
# as a voltage on ILM: 182 uA per amp into the ILM resistor.
#
#   limit = 3334 / R_ILM (sec. 7.3.3.1)           overload timer 22n: 18 ms
#   ramp  = 2000 / C_dVdt[pF] V/ms: 1n -> 2 V/ms, so charging ten tiles'
#           100 uF takes 0.2 A, far below either limit
#
# AUXOFF (pin 3, open drain) is not used and stays open.
def efuse(name, source, r_ilm, ovlo):
    u = part("U", "hub:TPS259470A", "TPS259470ARPW", FP_EFUSE, "power",
             lcsc="C3662799", mpn="TPS259470ARPWR")
    connect("EN_%s" % name, (u.ref, 1, "EN/UVLO"))
    connect(ovlo, (u.ref, 2, "OVLO"))
    connect("FLT_%s" % name, (u.ref, 4, "~{FLT}"))
    connect(source, (u.ref, 5, "IN"))
    connect("VIN", (u.ref, 6, "OUT"))
    connect("DVDT_%s" % name, (u.ref, 7, "DVDT"))
    connect("GND", (u.ref, 8, "GND"))
    connect("ILM_%s" % name, (u.ref, 9, "ILM"))
    connect("ITIMER_%s" % name, (u.ref, 10, "ITIMER"))
    res("100k", "EN_%s" % name, "GND", "power")          # off until firmware says so
    res("100k", "+3V3", "FLT_%s" % name, "power")        # FLT is open drain
    cap("1n/50V", "DVDT_%s" % name, "GND", "power")      # rated VIN + 5 V (sec. 6.3)
    cap("22n/16V", "ITIMER_%s" % name, "GND", "power")
    res(r_ilm, "ILM_%s" % name, "GND", "power")
    # ILM may carry no more than 50 pF (sec. 7.3.3.1): the ADC pin sits
    # behind 10k and gets no capacitor.
    res("10k", "ILM_%s" % name, "IMON_%s" % name, "power")
    return u


# PWR path: up to 2 A, the most a tile port's two contacts carry.  OVLO trips
# at 1.2 V: 470k/34k cuts off above 17.8 V.
U_PD = efuse("PD", "V_PD", "1.65k 1%", "OVLO_PD")
res("470k 1%", "V_PD", "OVLO_PD", "power")
res("34k 1%", "OVLO_PD", "GND", "power")
cap("1u/50V", "V_PD", "GND", "power", footprint=FP_C0603)
# USB path: 0.5 A, what any USB port may be asked for.  When the socket's CC
# voltage says the port offers 1.5 A or more, the firmware pulls ILIM_HI low,
# which puts 3.6k beside the 6.65k: 2.34k, 1.43 A.  OVLO is not needed on 5 V.
U_HOST = efuse("HOST", "VBUS", "6.65k 1%", "GND")
res("3.6k 1%", "ILM_HOST", "ILIM_HI", "power")
cap("1u/16V", "VBUS", "GND", "power")
# VIN: the tiles each bring 10 uF; the TVS also catches the negative kick when
# an eFuse cuts off a current flowing through the cables' inductance.
cap("1u/50V", "VIN", "GND", "power", footprint=FP_C0603)
cap("10u/50V", "VIN", "GND", "power", footprint=FP_C1206)
D_VIN = part("D", "Diode:SMAJ20A", "SMAJ20A", "Diode_SMD:D_SMA", "power")
connect("VIN", (D_VIN.ref, 1, "A1"))
connect("GND", (D_VIN.ref, 2, "A2"))
# Voltage dividers to the ADC, each with 100n so the ADC samples a stiff node.
for net, sense, (top, bottom) in (("VIN", "VIN_SENSE", VIN_DIVIDER), ("V_PD", "VPD_SENSE", VIN_DIVIDER),
                                  ("VBUS", "VBUS_SENSE", VBUS_DIVIDER)):
    res("%dk 1%%" % (top / 1e3), net, sense, "power")
    res("%dk 1%%" % (bottom / 1e3), sense, "GND", "power")
    cap("100n/16V", sense, "GND", "power")

# --------------------------------------------------------------------- link ---
# The tile port: same connector, pinout and series resistors as a tile's own
# (tile_circuit.py), so the hub looks like one more tile to the cable.
J_LINK = part("J", "Connector_Generic_MountingPin:Conn_01x06_MountingPin", "LINK",
              "Connector_JST:JST_GH_SM06B-GHS-TB_1x06-1MP_P1.25mm_Horizontal", "link",
              mpn="SM06B-GHS-TB")
for pin, net in {"1": "VIN", "2": "GND", "5": "GND", "6": "VIN", "MP": "GND"}.items():
    connect(net, (J_LINK.ref, pin))
connect("LINK_A", (J_LINK.ref, 3))
connect("LINK_B", (J_LINK.ref, 4))
res("220", "LINK_TX", "LINK_A", "link")
res("220", "LINK_RX", "LINK_B", "link")
# Console pads (not fitted): a 3.3 V serial adapter here reaches the firmware's
# log, and the ROM bootloader, which listens on this UART too.
AUX = part("J", "Connector_Generic:Conn_01x03", "AUX",
           "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical_SMD_Pin1Left", "link", dnp=True)
for pin, net in enumerate(["GND", "AUX_TX", "AUX_RX"], 1):
    connect(net, (AUX.ref, pin))

# ----------------------------------------------------------------------- ui ---
# Through-hole parts on the top side, the only ones there: they reach up
# through holes in the case's plate.  LEDs: 3 mm, red or green (blue and
# white ones need about 3 V, too much for a 3.3 V pin).  Pad 1 is the cathode.
LEDS = {}
for name in ("STATUS", "POWER"):
    d = part("D", "Device:LED", "LED 3mm", "LED_THT:LED_D3.0mm", "ui")
    LEDS[name] = d
    connect("GND", (d.ref, 1, "K"))
    connect("LED_%s_A" % name, (d.ref, 2, "A"))
    res("470", "LED_%s" % name, "LED_%s_A" % name, "ui")     # about 2.5 mA
# BOOT: held while the USB cable goes in, it starts the ROM bootloader (USB
# DFU).  Its cap ends flush with the plate, so only a pen tip presses it.
SW_BOOT = part("SW", "Switch:SW_Push", "BOOT", "Button_Switch_THT:SW_PUSH_6mm_H5mm", "ui")
connect("+3V3", (SW_BOOT.ref, 1))
connect("BOOT0", (SW_BOOT.ref, 2))
# USER: stands 2.3 mm proud of the plate.
SW_USER = part("SW", "Switch:SW_Push", "USER", "Button_Switch_THT:SW_PUSH_6mm_H7.3mm", "ui")
connect("BTN_USER", (SW_USER.ref, 1))
connect("GND", (SW_USER.ref, 2))
res("10k", "+3V3", "BTN_USER", "ui")

# Nets fed by a connector or through a passive part: KiCad's ERC needs a
# PWR_FLAG to accept that they are powered.
PWR_FLAG_NETS = ["VBUS", "V_PD", "VIN", "GND", "+3.3VA"]


if __name__ == "__main__":
    _circuit.summary(("mcu", "usb", "pd", "power", "link", "ui"))
