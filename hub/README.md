# Hub PCB

The hub hangs on a tile's east edge and plugs into that tile's E port. It
has two USB-C sockets:

- **USB**, to the computer: USB MIDI and a serial console, and the hub's own
  3.3 V.
- **PWR**, from a USB-C charger: the hub asks it for 15 V, for keyboards too
  big for the computer's port to feed.

Like the tile, it is made by scripts, not drawn by hand, and it is fully
routed: KiCad's DRC reports 0 errors and 0 unrouted connections. **No person
has reviewed it, nothing is ordered, and no hub exists yet.**

| file | made by | what |
|---|---|---|
| `../hub_circuit.py` | you | every part and connection, as data |
| `../hubgeom.py` | you | every position the board and the printed case share |
| `hub.kicad_sym`, `hub.pretty/` | `gen_hub_lib.py` | the MCU, eFuse and PD chip symbols and the eFuse's footprint |
| `hub.kicad_sch` | `gen_hub_sch.py` | the schematic |
| `hub.kicad_pcb`, `hub.kicad_pro` | `gen_hub_pcb.py`, then `route_hub.py` | every part placed, the outline, the ground pours, track widths per net (`gen_hub_pcb.py`); every track and via (`route_hub.py`) |
| `../route_hub.py`, `../pcbroute.py` | you | the routing as a script, and the small router it uses |
| `../gen_hub_case.py` | you | the printed case; see the [main README](../README.md) |

Unlike the tile's board, this one can be made again from nothing:

```
./kpython.sh gen_hub_lib.py && ./kpython.sh gen_hub_sch.py     # after a change to hub_circuit.py
REGEN_ROUTED_BOARD=1 ./kpython.sh gen_hub_pcb.py               # place every part: this throws the copper away
./kpython.sh route_hub.py                                      # draw the copper again, about 4 minutes
python3 hubgeom.py && python3 gen_hub_case.py                  # the case, after a change to hubgeom.py
```

Run them from the repository's root. Without `REGEN_ROUTED_BOARD=1` the
placement script refuses to touch a routed board, and `route_hub.py` refuses
a board that already has tracks. A change made by hand in `hub.kicad_sch`
or `hub.kicad_pcb` is lost the next time its script runs. `route_hub.py`
names pins and coordinates, so a moved part or a new one means adapting the
stage that routes it.

## What each run proves

- **Schematic:** KiCad exports its own netlist of the generated file, and it
  must equal `hub_circuit.nets()` net for net (57 nets). Any ERC error stops
  the run. Reruns keep every symbol's UUID, so the board stays linked to its
  schematic.
- **Pins:** every IC connection names the pin it expects
  (`(u.ref, 5, "IN")`), and generation stops if the symbol disagrees.
- **Placement:** every part has a place and the nets the circuit gives it;
  no courtyards overlap; nothing stands where a cover post presses, where
  the cable lies, or on the legs of the lights and buttons on the other
  side; and each chip reads back the way round it was meant to be.
- **Routing:** `route_hub.py` checks every track and via against the net
  classes' clearances as it adds them, then runs DRC with schematic parity
  in a fresh process. Result: 0 errors, 0 parity issues, 0 unrouted
  connections, and 12 silkscreen warnings (reference texts in tight spots).
- **Case:** `gen_hub_case.py` proves on the solids that a board of this
  outline, with the sockets and the cable header where `hubgeom.py` puts
  them, fits the printed case. The placement script takes those places from
  the same file.

What none of this proves: that the circuit is right. The symbols and the
eFuse's footprint were drawn from the datasheets named in `hub_circuit.py`
and `gen_hub_lib.py`, and nobody has checked them a second time. The tile's
board had such a review; this one has not.

## The board

39.3 x 78.0 mm, 2 layers, 1.6 mm. 74 parts, two of them pads that stay
empty (SWD and a spare serial port).

- **Back:** every part but four. North, the two sockets, opening north
  through the case's wall: under USB its protection, the 3.3 V regulator
  and its eFuse; under PWR the PD chip and its eFuse. Across the middle the
  cable channel, bare, with the tile port at its east end. South of it the
  MCU, its crystal and the ADC's dividers. At the south end the debug pads.
- **Front (the plate side):** two 3 mm LEDs (STATUS, POWER) and two push
  buttons (USER, BOOT), through-hole, in a row near the south end. They
  reach through holes in the case's plate. BOOT ends flush with the plate,
  so only a pen tip presses it; USER stands 2.3 mm proud.
- **Rule areas on the back:** an octagon where each of the case's four
  cover posts presses, and a strip where the cable lies. DRC keeps parts
  off them.

### Power

Two inputs, one output: `VIN`, the tiles' supply, on the cable.

- Each input reaches `VIN` through its own eFuse (TPS259470A), which
  switches it, ramps it up slowly, limits its current and reports that
  current to the ADC.
  - PWR: up to 2.02 A, the most a tile port's two contacts carry. Cut off
    above 17.8 V, so a charger that offers only 20 V cannot overrun the
    tiles' 20 V limit.
  - USB: 0.5 A, what any USB port may be asked for; 1.43 A while the
    socket's CC pins say the port offers 1.5 A or more (the firmware pulls
    `ILIM_HI` low).
- Each eFuse blocks reverse current, so the two inputs can never feed each
  other, whatever the firmware does.
- Both eFuses' enables are pulled low: the tiles stay off until the
  firmware switches one on.
- The MCU runs from the computer's USB only, through a TLV75733. With a
  charger alone the hub is off, and so are the tiles.
- The PD chip (CH224A) asks for 15 V by its `CFG1` resistor (56k) as soon
  as a charger is there. The firmware reads over I2C what was agreed and
  can ask for less.
- A SMAJ20A on `V_PD` and one on `VIN`, as on the tiles.

What the firmware does with all this is in
[`../firmware/DESIGN.md`](../firmware/DESIGN.md), "Power".

### The tile port

The same JST GH header, pinout and 220R series resistors as a tile's own
ports ([`../pcb/README.md`](../pcb/README.md)), so the hub looks like one
more tile to the cable. The header's opening is 50 mm from the opening of
the tile's E header when the hub hangs on that tile: the same 50 mm cable.

## How it is routed

`route_hub.py` works in stages. The first eight name their tracks'
coordinates; `signals` searches for its own.

| stage | what it adds |
|---|---|
| `fine` | The eFuses: 0.2 mm tracks out of their side pins and short necks out of their power strips, which stand 0.2 mm from their neighbours. |
| `usb` | The USB socket: the CC resistors, and the data pair through its protection and down to the MCU. |
| `pd` | The charger's socket and the PD chip under it. |
| `power` | VBUS, V_PD and VIN, up to 1 mm wide, on the back: from the sockets to the eFuses, and VIN along the cable channel's north edge to the tile port. The regulator and the 3.3 V trunk. |
| `mcu` | The MCU's supply and ground pins and their capacitors. |
| `near` | What joins neighbours: the LEDs, the buttons, the crystal, the three dividers beside the MCU, the two lines to the tile port. |
| `escapes` | A short track and a via at each pad that the back's copper shuts in, for the long runs to start and end on. |
| `gnd` | A via to the front's pour at every ground pad. |
| `signals` | The 23 nets left, searched net by net, the short ones first, on whichever side has room. A net that finds no way makes room by taking one neighbour out and laying both again. About three minutes. |
| `ties` | Vias between the two pours where the tracks cut either into pieces, and a way through to any piece of ground still on its own. |

The result: 0.99 m of track on the back, 0.59 m on the front, 179 vias, 95
of them ground.

Weak spots, accepted for now:

- **The front is not an unbroken ground plane.** The back is full round the
  chips, so the searched runs go over the front wherever they have to cross
  something. Round the MCU they cut the front's pour into one large part
  and a few small ones, which `ties` joins through the back's.
- **The searched runs are valid, not tidy.** They keep every clearance and
  DRC passes, but they wander. Eleven other settings of the search (a
  dearer front, other via costs) did no better. A tidier board needs
  explicit routes for the long runs, or a different placement.
- **The USB data pair is not impedance controlled:** two 0.2 mm tracks, 44
  and 47 mm long, one of them with a 4 mm hop over the front, because the
  socket's pads are laid out so that one cannot stay on the back. This is
  full-speed USB (12 Mbit/s), where runs this short are usual practice; it
  has not been measured.

Two places differ from the first placement, both so that ground pads get
their vias: the tile port's two series resistors stand 1.1 mm further east
(`LINK_R_X`), and the three divider rows are 2.2 mm apart instead of 1.5.

## Pin map, for the firmware

`hub_circuit.py` is the truth (`PINS` and the constants under it);
`../firmware/hub/board.h` and this table are copies.

| net | pin | what |
|---|---|---|
| `USB_DM`, `USB_DP` | 44 PA11, 45 PA12 | OTGFS1, also the ROM bootloader's USB DFU |
| `LINK_TX`, `LINK_RX` | 16 PA2, 17 PA3 | USART2, the cable to the tile: wire A and wire B, each through 220R |
| `AUX_TX`, `AUX_RX` | 51 PC10, 52 PC11 | USART3, on the AUX pads (not fitted); a ROM bootloader UART |
| `PD_SCL`, `PD_SDA` | 58 PB6, 59 PB7 | I2C1 to the CH224A, through 1k; the chip pulls them up itself |
| `PD_PG` | 57 PB5 | low = the charger agreed. Open drain, 100k to 3.3 V |
| `EN_PD`, `EN_HOST` | 30 PB12, 32 PB13 | high = eFuse on. 100k to ground each |
| `ILIM_HI` | 28 PB2 | open drain: low = 1.43 A from USB, open = 0.5 A |
| `FLT_PD`, `FLT_HOST` | 61 PB8, 62 PB9 | low = fault. Open drain, 100k to 3.3 V each |
| `VIN_SENSE` | 8 PC0, ADC1 channel 10 | VIN through 100k / 15k, 100n |
| `VPD_SENSE` | 9 PC1, channel 11 | V_PD through 100k / 15k, 100n |
| `VBUS_SENSE` | 10 PC2, channel 12 | VBUS through 100k / 100k, 100n |
| `IMON_PD` | 11 PC3, channel 13 | the PD eFuse's ILM pin, through 10k |
| `IMON_HOST` | 14 PA0, channel 0 | the USB eFuse's ILM pin, through 10k |
| `CC1_SENSE`, `CC2_SENSE` | 15 PA1, 20 PA4, channels 1 and 4 | the USB socket's CC pins, through 10k |
| `LED_STATUS`, `LED_POWER` | 37 PC6, 38 PC7 | high = lit, through 470R |
| `BTN_USER` | 2 PC13 | low = pressed. 10k to 3.3 V |
| `SWDIO`, `SWCLK` | 46 PA13, 49 PA14 | the debug pads |
| `BOOT0` | 60 PF11 | 3.3k to ground; the BOOT button to 3.3 V |

- **Current:** an eFuse's ILM pin gives 182 µA per amp into its resistor:
  1.65k on the PD path (limit 2.02 A), 6.65k on the USB path (0.50 A), with
  3.6k beside it while `ILIM_HI` is low (1.43 A).
- **Debug pads J1**, pad 1 to 6, as on a tile: +3V3, BOOT0, SWCLK, NRST,
  SWDIO, GND. **AUX pads J5**, pad 1 to 3: GND, AUX_TX, AUX_RX.
- **Crystal:** 12 MHz, as on a tile.

## Before ordering: not yet verified

- **A review by a person.** Start with the two symbols and the footprint
  drawn here: the TPS259470A (pins and the RPW0010A land pattern) and the
  CH224A, against their datasheets. Then the USB-C sockets' pad order, the
  LEDs' polarity, and the two buttons' heights against the case's plate.
- **LCSC part numbers:** only the CH224A (`C42459160`) and the TPS259470A
  (`C3662799`) have one. Still to pick:
  AT32F405RCT7, the 12 MHz crystal, TLV75733PDBV, BLM18PG221SN1D,
  USBLC6-2SC6, SMAJ20A, the two TYPE-C-31-M-12 sockets, the JST GH header
  (see the tile's README on the clone), the 3 mm LEDs, the two 6 mm push
  buttons (5 mm and 7.3 mm tall), and every resistor and capacitor. The
  capacitor values carry their voltage ratings (`1u/50V`): keep them.
- **LED colour:** red or green. Blue and white ones need about 3 V, too
  much for a 3.3 V pin through 470R.
- **Nothing has run on hardware.** The firmware for this board passes its
  tests on a PC ([`../firmware/README.md`](../firmware/README.md)).
- **A blank tile's first program.** The ROM bootloader of a tile listens on
  its W and S ports; the hub's box hangs on an E edge. So a tile's first
  flashing needs the hub's cable in that tile's W or S port, with the hub
  loose beside it, and the tile's BOOT0 pads bridged; or SWD. Every later
  program comes through the hub wherever the tile is. Changing this would
  be a change to the tile's hardware, and is not decided.
