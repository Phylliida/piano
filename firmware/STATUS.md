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
| `Makefile` | `make test`, `make tile`, `make hub`, `make clean` |

What exactly is unproven in these is listed in `README.md`, "What is
untested".

## Missing

- **Everything on hardware.** No board exists; the first one will show
  which of the register-level guesses were wrong.
- **The program that flashes a tile through the hub** (`tools/flash_tile.py`
  was the plan). The hub's side is there and tested on a PC (`bootloader
  <tile> [ms]`, `bridge <baud> [n|e|o] [straight|swapped]`). The PC's side
  is not written, because the ROM bootloader's UART commands are in no
  Artery document found so far (the pointer is their "ISP Programmer"
  manual); it would have to assume the usual 0x7F / 0x79 protocol.
  Until then tiles are flashed over SWD.
- **The ROM bootloader and the hub's place.** The ROM listens on USART2 and
  USART3 (a tile's W and S ports), and the hub's box hangs on a tile's E
  edge (UART8). Flashing through the hub needs the cable moved, or the
  hardware changed; that is the hardware's decision, not made here.
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
