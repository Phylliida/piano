# Tile PCB (8 x 5 keys)

Generated, not hand-drawn. Edit the Python, then regenerate. Don't edit
`tile.kicad_sch` by hand: the next run overwrites it. Tracks you draw in
`tile.kicad_pcb` are also lost if you rerun `gen_pcb.py`, so save copies
while routing.

| file | made by | what |
|---|---|---|
| `../tile_circuit.py` | you | every part and connection, as data |
| `../tilegeom.py` | you | every position the board and the printed case share |
| `tile.kicad_sym`, `tile.pretty/` | `gen_lib.py` | the MCU and sensor symbols and the switch footprint (plus LED ones, unused since the LEDs were removed) |
| `tile.kicad_sch` | `gen_sch.py` | the schematic |
| `tile.kicad_pcb`, `tile.kicad_pro` | `gen_pcb.py` | every part placed, all nets assigned, ground planes, track widths per net |

```
../kpython.sh ../gen_lib.py && ../kpython.sh ../gen_sch.py && ../kpython.sh ../gen_pcb.py
```

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
- **Board:** DRC with schematic parity. Any error other than an unrouted
  connection stops the run. Expected result on a fresh board: 261 unrouted
  connections, about 60 silkscreen warnings, and one "isolated copper" warning. That last one is
  the front ground fill, which touches no pads until you add vias.
- **Audit:** an independent review on 2026-10-11 checked every IC's
  pin-to-pad mapping against its datasheet (all correct), plus the circuit,
  part heights and the fit with the case. Its findings are fixed below,
  except the ones listed under "Before ordering".

## The board

- **2 layers** (cheaper). This works because every part sits on the back, so
  the front is almost entirely free. HE60 routes 67 keys on 2 layers.
  - **B.Cu (bottom):** every part, the short connections, and a ground fill
    around them.
  - **F.Cu (top, switch side):** no parts. Long runs (power rails, mux
    buses) plus a ground fill.
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
    cable runs straight from one to the other, through both tiles' slots, in
    the 5 mm under the boards. It passes under one key on the way, between
    or beside its switch pegs.
  - **Cable length:** "50 mm" should mean 50 mm between the two plugs'
    fronts. Some listings measure the wire alone (a little slack, fine) or
    the whole cable end to end (about 5 mm short). Check the listing's
    drawing.
  - **Pinout:** `VIN, GND, A, B, GND, VIN`. It's symmetric, so a reversed
    cable cannot short power, and a ground wire separates each data wire
    from VIN. The MCU's `TRPSWAP` bit fixes swapped data pins in firmware.
  - **Plugging in:** pop both tiles' covers and plug in the cable. Straight
    or reversed both work.

## Routing it: a first-timer's order

In KiCad, open `tile.kicad_pro`, then the PCB editor. The thin lines are the
*ratsnest*: connections still to draw. Press **X** to route a track, **V**
while routing to drop a via to another layer, **B** to refill zones, and run
**Inspect > Design Rules Checker** often. Track width follows the net class
(VIN 1.0 mm, power 0.5 mm, signals 0.2 mm), so you rarely set it by hand.

1. **Power, before the fills fill up.**
   - Keep the buck converter's loop short and fat on B.Cu. That's U2, its
     input cap (already straddling U2's VIN and GND pins), L1, and the 22u
     output caps. It's the noisiest part of the board.
   - Keep the feedback divider's trace to U2 pin 1 short, and away from L1.
   - Route VIN between the four ports and the buck at 1 mm.
   - Run `+3.3VA` (sensors) as a wide trunk on F.Cu, along the channels
     between key columns, with short drops to each part.
2. **Ground.**
   - Press B to refill.
   - Wherever a ground pad's ratsnest line remains, add a via to the other
     side's fill.
   - Then sprinkle *stitching vias* (vias on GND, not connected to any trace)
     every 10–15 mm, so the two fills act as one ground. Without an inner
     plane, this is what keeps the analog readings quiet.
3. **MCU basics.**
   - Crystal Y1 and its two 30p capacitors: as short as possible, nothing
     else running under them.
   - Then each decoupling capacitor to its pin.
4. **Analog.** Each sensor output goes to its mux, the five mux outputs go
   to the MCU, and the three select lines are shared by all five muxes. Keep
   these away from the buck.
5. **Ports.** UART through the 220R resistors.

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
  - The factory bootloader listens on USART1 (the E port), USART3 (the N
    port), SPI1 (PA4–PA7, which are the mux outputs) and I2C1. Unplug the
    neighbouring tiles when flashing through a port.
- **Flashing without a probe** (plan, untested): bridge J1 pad 1 (3V3) to
  pad 6 (BOOT0) while the tile powers up, and the factory bootloader waits on
  the E port; the hub sends the firmware down that cable. Only the first
  flash needs the bridge: after that, firmware can enter the bootloader on
  the hub's command. Not yet checked: whether a blank chip starts the
  bootloader by itself.
