# Firmware status

Work in progress. Nothing here has run on hardware. Delete this file once
the "Missing" list is empty.

## Done and tested on a PC (`make test`, address + undefined sanitizers)

`make test` prints 9 programs, 119 tests, 3 872 626 checks, 0 failed.

| file | what | test |
|---|---|---|
| `core/proto.h` | frame types, sizes, times | |
| `core/util`, `core/frame` | byte order, ID hash, CRCs, COBS, frames, receiver | `tests/test_frame.c` |
| `core/link` | one port: HELLO, swap search, up/down, neighbour | `tests/test_link.c` |
| `core/node` | beacon tree, forwarding, queues and their drop order, quiet, pause | `tests/test_node.c` |
| `core/keys`, `core/lut` | ADC values to notes, calibration, parameters, depth | `tests/test_keys.c` |
| `core/store`, `core/tile` | flash records; the tile program (NOTE, STATE, INFO, POS, RAW, commands) | `tests/test_tile.c` |
| `core/notes` | lattice, notes, velocity curve | `tests/test_notes.c` |
| `core/power`, `core/ch224` | the hub's power policy; the PD chip's registers | `tests/test_power.c` |
| `core/hub`, `core/grid`, `core/console` | the tile table, the layout, MIDI, command jobs, the console, the bridge | `tests/test_hub.c` |
| `sim/` | whole keyboards on a PC: real tile and hub programs, simulated cables, keys, flash, time | `tests/test_sim.c` (19 scenarios) |
| `tools/gen_lut.py`, `tools/fetch_deps.sh` | the table; the two libraries | ran |

`make test-tools` (Python 3.8) prints 87 tests, OK:

| file | what | test |
|---|---|---|
| `tools/flash_tile.py` | flashes the tile on the hub's cable: `bootloader`, `bridge`, then the ROM bootloader's UART protocol (Artery's PM0005) | `tests/test_flash_tile.py` |
| `tests/hubsim.c` | the hub's program and one tile in the simulator, behind a pipe, for that test | |

The script's scenarios run against the hub's own program (`hubsim`) and
against an imitation of the hub; the tile's ROM bootloader and flash are an
imitation both times, written from the manual. Running it against the real
hub program found four faults in the script, which are mended: it gave up
on a hub that had only just got its power; it took a hub that was still
inside an earlier `quiet` for a hub without a tile; it did not wait for the
20 ms the hub goes on speaking after `quiet`; and it took what the hub
remembered of the tile from before the flashing for the tile having come
back.
The bytes of each ROM command the script sends (sync, Get Version, Get
Device ID, Read Memory, Write Memory, Erase, Jump, Reset Device) were
compared once more with PM0005 V2.0.4 on 2026-11-13 and agree with it. That
is the script against the manual, not against a chip.
`hubsim.c` holds copies of two loops of `hub/main.c` (`console_in`,
`bridge_run`); when those change, the copies must follow by hand.

`DESIGN.md` describes this code as it is. `README.md` says how to build,
test and flash, and lists what is untested.

## Written, compiles and links, never run

`make tile CROSS=<prefix>` builds `build/tile/tile.{elf,bin,map}`:
text 15116, data 4, bss 30220 bytes.

`make hub CROSS=<prefix>` builds `build/hub/hub.{elf,bin,map}`:
text 39636, data 40, bss 26128 bytes.

(arm-none-eabi-gcc 14.3, Artery library v2.1.5, TinyUSB 0.21.0.)

| file | what |
|---|---|
| `port/at32f405/port` | clock (216 MHz from 12 MHz), ms time, pins, chip ID, reboot, jump to the ROM bootloader |
| `port/at32f405/uart` | a UART with DMA both ways and the pin swap |
| `port/at32f405/flash` | the record in the last 2 KB sector |
| `port/at32f405/i2c` | I2C1 at 10 kHz for the CH224A (`i2c1_start`, `i2c1_xfer`) |
| `tile/board.h`, `tile/main.c` | the tile's pins (copy of `tile_circuit.py`), scan, four ports, main loop |
| `hub/board.h` | the hub's pins and constants (copy of `hub_circuit.py`) |
| `hub/tusb_config.h`, `hub/usb.c` | TinyUSB as one serial port and one MIDI port, 0x1209:0x0001 |
| `hub/main.c` | measuring, the power pins, the cable, console, MIDI, bridge, LEDs, button |
| `Makefile` | `make test`, `make test-tools`, `make tile`, `make hub`, `make clean` |

What exactly is unproven in these is listed in `README.md`, "What is
untested".

## Missing

- **Everything on hardware.** No board exists; the first one will show
  which of the register-level guesses were wrong.
- **Flashing a tile through the hub, on hardware.** `tools/flash_tile.py`
  is written and tested as far as a PC goes, and has never met a real hub
  or a real ROM bootloader. Not known until it has: whether the firmware's
  jump into the ROM works; the order of the chip ID's bytes in Get Device
  ID (the script takes either); whether this ROM knows Reset Device (the
  script falls back to Jump); whether the ROM, once it has heard the hub,
  ignores its other UARTs as PM0007 says; whether the times are long
  enough (they are guesses, the 2 s for a hub that has only just got its
  power among them: the simulator has no power sequencing). The script's
  Windows path (pyserial) has never run at all, and the hub's USB and UART
  code is not part of the test. Until it has worked once, tiles are
  flashed over SWD.
- **The ROM bootloader and the hub's place.** The ROM listens on USART2 and
  USART3 (a tile's W and S ports), and the hub's box hangs on a tile's E
  edge (UART8). Flashing through the hub needs the cable moved, or the
  hardware changed; that is the hardware's decision, not made here. The
  script stops, with nothing changed, when the cable is in N or E.
- **The USB DFU command line** in the README (`dfu-util`, 2e3c:df11) is
  from other AT32 chips and has not been tried on this one.
- **A real USB product ID** (0x1209:0x0001 is pid.codes' test ID).
- **The travel table** (`tools/gen_lut.py`): its numbers are guesses until
  a switch is measured.
- From `DESIGN.md`, "What is left for later": more than one hub on one
  keyboard, MPE.

## For whoever continues

- The hub's pin map and constants live in `hub_circuit.py`.
  `firmware/hub/board.h` must be a copy of them (checked line by line on
  2026-11-13).
- `refs/libhmk` is GPL-3.0: do not copy from it. The two libraries in
  `deps/` are untrusted downloads: compile their C by path, run nothing.
