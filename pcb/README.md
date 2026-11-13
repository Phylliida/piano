# Tile PCB (8 x 5 keys)

Made by scripts, not drawn by hand, and fully routed: KiCad's DRC reports
0 errors and 0 unrouted connections.

- **Schematic:** regenerated from `tile_circuit.py` on every run. Don't edit
  `tile.kicad_sch` by hand: the next run overwrites it.
- **Board:** no longer regenerated. `gen_pcb.py` placed the parts once and
  now refuses to run, because it would throw the routing away. Change the
  board in KiCad, or with a script that edits it in place, as
  `place_ports.py`, `remove_leds.py` and `route_board.py` do.

| file | made by | what |
|---|---|---|
| `../tile_circuit.py` | you | every part and connection, as data |
| `../tilegeom.py` | you | every position the board and the printed case share |
| `tile.kicad_sym`, `tile.pretty/` | `gen_lib.py` | the MCU and sensor symbols and the switch footprint (plus LED ones, unused since the LEDs were removed) |
| `tile.kicad_sch` | `gen_sch.py` | the schematic |
| `tile.kicad_pcb`, `tile.kicad_pro` | `gen_pcb.py`, then by hand, then `route_board.py` | every part placed, ground planes, track widths per net (`gen_pcb.py`); each key's two stubs and the power block (by hand); every other track and via (`route_board.py`) |
| `../route_board.py`, `../pcbroute.py` | you | the routing as a script, and the small router it uses |

After changing `tile_circuit.py`:

```
../kpython.sh ../gen_lib.py && ../kpython.sh ../gen_sch.py
```

To route again, after a change to `route_board.py` or to a pin in the
circuit, start from the board as it was routed by hand. The script refuses
a board it has already routed:

```
git show 991aaaf:pcb/tile.kicad_pcb > tile.kicad_pcb
../kpython.sh ../route_board.py
```

Its `nets` stage gives the pads the nets `tile_circuit.py` names. The later
stages name pins and coordinates, so a moved signal means adapting the stage
that routes it, and a new part has to be placed first.

`kpython.sh` and `kcli.sh` run KiCad's Python and `kicad-cli` on one core, at
low priority. Their paths are NixOS store paths, so edit them on another machine.

## What each run proves

- **Schematic:** KiCad exports its own netlist of the generated file, and it
  must equal `tile_circuit.nets()` net for net (81 nets). Any ERC error
  stops the run. Reruns keep every symbol's UUID, so a routed board stays
  linked to its schematic.
- **Pins:** every IC connection names the pin it expects (`("U2", "3", "IN")`),
  and generation stops if the symbol disagrees. This caught the TVS diode's
  pin names on the first run.
- **Board:** `route_board.py` checks every track and via against the
  net classes' clearances as it adds them, then runs DRC with schematic
  parity in a fresh process. Result: 0 errors, 0 parity issues, 0 unrouted
  connections. The 72 silkscreen warnings (reference texts in tight spots)
  were there before routing.
- **Audit:** an independent review on 2026-10-11 checked every IC's
  pin-to-pad mapping against its datasheet (all correct), plus the circuit,
  part heights and the fit with the case. Its findings are fixed below,
  except the ones listed under "Before ordering".

## The board

- **2 layers** (cheaper). This works because every part sits on the back, so
  the front is almost entirely free. HE60 routes 67 keys on 2 layers.
  - **B.Cu (bottom):** every part, each sensor's track to its mux, the
    sensors' supply, and a ground fill around them.
  - **F.Cu (top, switch side):** no parts. It is the ground plane, cut only
    by what has to cross the back's tracks: the mux lines up the middle, VIN,
    `+3V3` to the MCU, the port data lines and eight short supply hops.
  - To switch back to 4 layers (solid inner ground and power planes, easier
    routing, higher cost), set `LAYERS = 4` in `gen_pcb.py`.
- **Under each key:** the sensor is centred under the switch stem. Its 100n
  (supply) and 4.7n (output) capacitors sit beside it. Every key is identical.
- **Power:** VIN (7–20 V) → TVS → U2, an AP63201 buck set to 3.72 V →
  U3, a TLV75733 linear regulator → `+3V3` for the MCU → FB1, a ferrite →
  `+3.3VA` for VDDA, the sensors and the muxes.
  - The 40 sensors draw about 250 mA. Dropping that from 5 V in one linear
    regulator would cost about 0.45 W of heat next to them, so the buck
    takes most of the drop and U3 burns only about 0.15 W.
  - VDD and VDDA come from the same regulator, as the datasheet requires,
    within 15 mV of each other.
- **No LEDs** (removed 2026-10-20, `remove_leds.py` took them off the routed
  board). A tile draws about 1.2 W, so a 400-key build (10 tiles) needs
  12–13 W: about 0.85 A at 15 V, well inside the 2 A one port can bring in.
  Lit keys would have needed several times that.
- **Circles:** the case's cover posts press there. Keep them free of parts.
  Rule areas make DRC enforce this.
- **Ports:** one JST GH header per edge, each opening straight toward its wall
  slot. Their spots are data in `tilegeom.PORT_SPOTS` (found by a search
  over this board), so the case's slots and the board always agree;
  `place_ports.py` moves the parts to match.
  - **Cables:** E and W share a height; N and S share column 5. Partner
    headers' openings are 50 mm apart across the seam, so a 50 mm GH 6-pin
    cable runs straight from one to the other, in the 5 mm under the boards.
    The case leaves that whole run open from below: the cover is cut away
    from each header to its edge and both walls are open down to the floor.
    The cable passes under one key on the way, between or beside its switch
    pegs.
  - **Cable length:** "50 mm" should mean 50 mm between the two plugs'
    fronts. Some listings measure the wire alone (a little slack, fine) or
    the whole cable end to end (about 5 mm short). Check the listing's
    drawing.
  - **Pinout:** `VIN, GND, A, B, GND, VIN`. It's symmetric, so a reversed
    cable cannot short power, and a ground wire separates each data wire
    from VIN. The MCU's `TRPSWAP` bit fixes swapped data pins in firmware.
  - **Plugging in:** snap the tiles together, turn the keyboard over and
    plug the cable in from underneath. The covers stay on. Straight or
    reversed both work.

## How it is routed

`route_board.py` works in stages, each a short function that names its
tracks' coordinates or searches for them:

| stage | what it adds |
|---|---|
| `lanes`, `lanes3` | Each sensor's output to its mux, on the back. A mux lies in the band between two key rows; the lanes run along the band, side by side, into its pins. The channel order was chosen so that none cross. Row 3's mux stands across its band, beside the MCU, and its lanes come round it. |
| `bus` | The three select lines and the five mux outputs, up the middle of the board on the front. |
| `supply` | `+3.3VA` as a ladder on the back, 0.5 mm wide: a spine down the west and east edges, four rungs between the key rows, a short drop to each sensor and mux. The outermost keys at the north and south edges, which no rung reaches, get theirs over the front. |
| `vin` | VIN between the four ports and to the TVS, 1 mm wide: west to east on the front, a stem down the middle to the S port. Two vias wherever it changes layer, because the current of every tile further on passes through. |
| `uarts` | Two data lines per port, through the 220R resistors. |
| `mcu` | `+3V3`, the crystal, reset, BOOT0, the debug pads, the pull-downs. |
| `gnd` | A via to the front plane at every ground pad: 172 of them. Larger ones (0.8 mm) at the ports, the TVS and the regulators' capacitors. |
| `ties` | 182 more vias between the two ground fills. The tracks cut the back's fill into pieces; each piece gets a via wherever it is more than 8 mm from its next one, if one fits. |

The power block keeps its hand routing, and the ports stand where
`place_ports.py` put them.

One weak spot, accepted: the ground pins of AM4 (row 3's mux) and of two
small parts beside it reach the plane over a 5 mm long, 0.25 mm wide track
to the via of one of the MCU's ground pins, because tracks wall the front in there. A 4051
draws microamps.

## Pin map, for the firmware

`tile_circuit.py` is the truth (`SELECT_PINS`, `ADC_PINS`, `ADC_CHANNEL`,
`CHANNEL_OF_COL`, `UARTS`); this is a copy.

- **Keys:** column 0 is the west one, row 0 the south one. Key
  `HE(5 * column + row + 1)`.
- **Selects**, shared by the five muxes: S0 = PB0, S1 = PC5, S2 = PC4.
  S0 is the lowest bit.
- **Mux outputs:** one select value reads one key of every row.

  | row | mux | MCU pin | ADC1 channel |
  |---|---|---|---|
  | 0 | AM1 | PA4 | 4 |
  | 1 | AM2 | PA5 | 5 |
  | 2 | AM3 | PA6 | 6 |
  | 3 | AM4 | PB1 | 9 |
  | 4 | AM5 | PA7 | 7 |

- **Select value for each column:**

  | column | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
  |---|---|---|---|---|---|---|---|---|
  | rows 0, 1, 2, 4 | 6 | 7 | 4 | 5 | 2 | 3 | 1 | 0 |
  | row 3 | 0 | 1 | 3 | 2 | 5 | 4 | 7 | 6 |

- **Ports** (TX, RX): W = USART2 (PA2, PA3), N = USART6 (PC6, PC7),
  E = UART8 (PC8, PC9), S = USART3 (PC10, PC11). The pins' IOMUX numbers
  are in the reference manual, chapter 6.
- **Debug pads J1**, pad 1 to 6: +3V3, BOOT0, SWCLK, NRST, SWDIO, GND.

## Before ordering: not yet verified

- **LCSC part numbers:**
  - Still to pick:
    - AP63201WU-7
    - SMAJ20A
    - the FNR4030S 10 µH (≥ 1 A) inductor
    - BLM18PG221SN1D
    - MT9102ET
    - SN74LV4051APWR
  - Every capacitor value now carries its voltage rating (`10u/50V`). Keep it
    when choosing parts.
  - Genuine JST GH SM06B-GHS-TB is out of stock. The XYECONN clone
    `C51940119` is stocked, but I haven't read its datasheet.
- **Input voltage:** keep VIN at or below 19–20 V. The SMAJ20A starts
  conducting just above 20 V, which is fine for 12 V and 15 V supplies.
- **Current through a port:** each GH contact is rated 1 A with 26 AWG wire
  (less with the thinner wire most ready-made cables use), so a port carries
  up to 2 A of VIN to every tile beyond it. Use a 15 V supply: 400 keys then
  need about 0.85 A.
- **Not added (by choice for v1):**
  - ESD clamps on the port data lines.
  - A reverse-polarity guard on VIN.
  - The symmetric pinout already makes a reversed cable harmless. Only a
    miswired supply could reverse the polarity, and then the SMAJ20A would
    short it.
- **Firmware notes:**
  - Call `reduce_power_consumption()`, since the unused USB PHY otherwise
    draws 3 mA. Pin 33 now has the 12k it needs for that.
  - PA11 and PA12 have 100k pull-downs, per erratum ES0011 1.5.2.
  - Enable pull-ups on the UART receive pins, since they float with no cable
    plugged in. Put unused GPIOs in analog mode.
  - The factory bootloader listens on USART2 (the W port), USART3 (the S
    port), USART1 (PA9/PA10, not connected here) and USB (PA11/PA12, pulled
    down here): datasheet table 5. Unplug the neighbouring tiles when
    flashing through a port.
- **Flashing without a probe** (plan, untested): bridge J1 pad 1 (3V3) to
  pad 2 (BOOT0), its neighbour, while the tile powers up, and the factory
  bootloader waits on the W and S ports; the hub sends the firmware down a
  cable plugged into one of those. Only the first flash needs the bridge:
  after that, firmware can enter the bootloader on the hub's command. Not
  yet checked: whether a blank chip starts the bootloader by itself.
