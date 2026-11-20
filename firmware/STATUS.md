# Firmware status

Work in progress. Nothing here has run on hardware. Delete this file once
the "Missing" list is empty.

## Done and tested on a PC (`make test`, address + undefined sanitizers)

`make test` prints 10 programs, 164 tests, 4 058 035 checks, 0 failed.

| file | what | test |
|---|---|---|
| `core/proto.h` | frame types, sizes, times | |
| `core/util`, `core/frame` | byte order, ID hash, CRCs, COBS, frames, receiver | `tests/test_frame.c` |
| `core/link` | one port: HELLO, swap search, up/down, neighbour, the cable's rate (1 or 4 Mbaud) | `tests/test_link.c` |
| `core/node` | beacon tree, forwarding, queues and their drop order, quiet, pause | `tests/test_node.c` |
| `core/keys`, `core/lut` | ADC values to notes, calibration, parameters, depth | `tests/test_keys.c` |
| `core/store`, `core/tile` | flash records; the tile program (NOTE, STATE, INFO, POS, RAW, commands) | `tests/test_tile.c` |
| `core/notes` | lattice, notes, velocity curve | `tests/test_notes.c` |
| `core/power`, `core/ch224` | the hub's power policy; the PD chip's registers | `tests/test_power.c` |
| `core/hub`, `core/grid`, `core/console` | the tile table, the layout, MIDI, command jobs, the console, the bridge | `tests/test_hub.c` |
| `core/fw` | firmware update: the two slots and their states, what the bootloader chooses, receiving an image, confirming it | `tests/test_fw.c` |
| `sim/` | whole keyboards on a PC: real tile and hub programs, simulated cables, keys, flash, time; each chip's program flash (`sim/flashsim`), the bootloader's choice at every start, the watchdog, the tiles' power through the hub; time in steps of 0.1 ms, cables at either rate, programs from before the two rates | `tests/test_sim.c` (32 scenarios) |
| `tools/gen_lut.py`, `tools/fetch_deps.sh` | the table; the two libraries | ran |

`make test-tools` (Python 3.8) prints 38, 111 and 78 tests, OK:

| file | what | test |
|---|---|---|
| `tools/mkimage.py` | fills in an image's length and checksum; puts the factory file together; shows what a file is | `tests/test_mkimage.py` (38), with `tests/fwcheck.c`: `core/fw.c` reads what the script wrote |
| `tools/update.py` | new programs to every tile and the hub, through the hub's console | `tests/test_update.py` (78) |
| `tools/flash_tile.py` | a tile's first program: `bootloader`, `bridge`, then the ROM bootloader's UART protocol (Artery's PM0005) | `tests/test_flash_tile.py` (111) |
| `tests/hubsim.c` | the hub's program and its tiles in the simulator, behind a pipe, for those tests; the test says when cables, power and flash fail | |

`update.py`'s tests run the script against the tiles' and the hub's own
programs, with images that `mkimage.py` made. They were checked once by
breaking the script in 87 places, one at a time: the tests noticed 82. The
five they did not notice were looked at and left: a second way out of a
loop that has another, a comparison that cannot differ where it stands, a
refusal the console never gives, the length of a wait, and a clock that
starts a moment earlier.

The images that `make tile hub` builds went through the simulator once as
well: `update.py` sent them to one tile and the hub, and to four tiles and
the hub, and every node ran the new program afterwards. That is not part of
`make test-tools`, which needs no ARM compiler.

`flash_tile.py`'s scenarios run against the hub's own program (`hubsim`)
and against an imitation of the hub; the tile's ROM bootloader and flash
are an imitation both times, written from the manual. Running it against
the real hub program found four faults in the script, which are mended: it
gave up on a hub that had only just got its power; it took a hub that was
still inside an earlier `quiet` for a hub without a tile; it did not wait
for the 20 ms the hub goes on speaking after `quiet`; and it took what the
hub remembered of the tile from before the flashing for the tile having
come back.
The bytes of each ROM command the script sends (sync, Get Version, Get
Device ID, Read Memory, Write Memory, Erase, Jump, Reset Device) were
compared once more with PM0005 V2.0.4 on 2026-11-13 and agree with it. That
is the script against the manual, not against a chip.
`hubsim.c` holds copies of two loops of `hub/main.c` (`console_in`,
`bridge_run`); when those change, the copies must follow by hand.

The cables' two rates (`DESIGN.md`, "How fast") were tried in the simulator
with: nine tiles and a hub starting together (thirteen cables at 4 Mbaud
after 0.4 s, no bad frame); a cable that carries 1 Mbaud and nothing faster
(four tries, then 1 Mbaud for good, no note lost); a tile with the program
from before the two rates between new ones; an asking and a GO that are
lost; an asked tile that stands still for 40 ms; `bootloader <tile>` and
`bridge` in between; a keyboard of old programs that takes the new ones
through `update.py` and old ones again (`tests/test_sim.c`,
`tests/test_update.py`). The unit tests of `core/link` run two ends
against each other and lose each `HELLO` of the exchange in turn.

`DESIGN.md` describes this code as it is. `README.md` says how to build,
test and flash, and lists what is untested.

## Written, compiles and links, never run

`make boot CROSS=<prefix>` builds `build/boot/boot.{elf,bin,map}`:
text 2192, data 0, bss 4096 bytes, of the 8 KB it may take.

`make tile CROSS=<prefix>` builds `build/tile/tile_a` and `tile_b`
(`.elf`, `.bin`, `.map`, `.img`) and `tile_factory.bin`:
text 18532, data 4, bss 79564 bytes; an image is 18 540 bytes. Of the
bss, 64 KB are the four ports' receive rings; about 24 KB of the 102 KB of
RAM are left for the stack.

`make hub CROSS=<prefix>` builds the same for the hub:
text 44960, data 40, bss 38824 bytes; an image is 45 004 bytes.

A slot takes an image of up to 122 880 bytes (120 KB; the slot's last
sector is kept for what the bootloader remembers of it). A factory file is
258 048 bytes.

(arm-none-eabi-gcc 14.3, Artery library v2.1.5, TinyUSB 0.21.0.)

| file | what |
|---|---|
| `port/at32f405/port` | clock (216 MHz from 12 MHz), ms time, pins, chip ID, reboot, the watchdog, the word that asks the bootloader for the ROM |
| `port/at32f405/uart` | a UART with DMA both ways, the pin swap and the change of speed |
| `port/at32f405/flash` | the record in the last 2 KB sector; erasing and writing the slot that is not running |
| `port/at32f405/i2c` | I2C1 at 10 kHz for the CH224A (`i2c1_start`, `i2c1_xfer`) |
| `port/at32f405/sections.ld`, `slot_a.ld`, `slot_b.ld`, `boot.ld` | where things are linked: one program for each slot, and the bootloader |
| `boot/main.c` | the bootloader: chooses a slot with `core/fw.c`, starts the watchdog for a program on trial, jumps to the program or to the ROM |
| `tile/board.h`, `tile/main.c` | the tile's pins (copy of `tile_circuit.py`), scan, four ports, main loop |
| `hub/board.h` | the hub's pins and constants (copy of `hub_circuit.py`) |
| `hub/tusb_config.h`, `hub/usb.c` | TinyUSB as one serial port and one MIDI port, 0x1209:0x0001 |
| `hub/main.c` | measuring, the power pins, the cable, console, MIDI, bridge, LEDs, button |
| `Makefile` | `make test`, `make test-tools`, `make boot`, `make tile`, `make hub`, `make clean` |

What exactly is unproven in these is listed in `README.md`, "What is
untested".

## Missing

- **Everything on hardware.** No board exists; the first one will show
  which of the register-level guesses were wrong.
- **4 Mbaud on a cable.** The agreement is tested on a PC; the UART at
  4 Mbaud, the change of its speed between two frames and the cable itself
  are not. `DESIGN.md`, "What is left for later", lists what to look at on
  the first boards. If 4 Mbaud turns out badly, `RATE_MAX` in
  `core/proto.h` set to `RATE_1M` gives programs that stay at 1 Mbaud, and
  they go in through the hub.
- **An update through the hub, on hardware.** `tools/update.py`, the
  bootloader and `core/fw.c` are tested as far as a PC goes. Not known
  until a hub and some tiles exist: whether the bootloader starts a program
  at all; whether the watchdog's four seconds are four; how long an update
  takes and what the erases do to the cables and to USB meanwhile; whether
  every restart of the hub takes the tiles' power away (the script handles
  both, the simulator has both). The script's Windows path (pyserial) has
  never run at all, and the hub's USB and UART code is not part of the
  test.
- **A tile's first program through the hub, on hardware.**
  `tools/flash_tile.py` has never met a real hub or a real ROM bootloader.
  Not known until it has: whether the jump into the ROM works; the order of
  the chip ID's bytes in Get Device ID (the script takes either); whether
  this ROM knows Reset Device (the script falls back to Jump); whether the
  ROM, once it has heard the hub, ignores its other UARTs as PM0007 says;
  whether the times are long enough (they are guesses, the 2 s for a hub
  that has only just got its power among them). Until it has worked once,
  tiles get their first program over SWD.
- **A blank tile's first program without touching the tile.** The ROM
  listens on USART2 and USART3 (a tile's W and S ports), the hub's box
  hangs on a tile's E edge (UART8), the ROM does not pass anything on to
  the next tile, and a blank chip wants BOOT0 high, which the cable does
  not carry. So the first program means, once for each tile, the hub's
  cable in its W or S port and its BOOT0 pads bridged, or SWD. Changing
  that is the hardware's decision, not made here. Every later program
  comes through the hub.
- **A new bootloader through the hub.** There is no way. It is 2 KB of
  code and is meant to stay as it is.
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
- The flash layout is in `core/fw.h` and again in `boot.ld`, `slot_a.ld`,
  `slot_b.ld` and `tools/mkimage.py`. `tests/test_mkimage.py` compares all
  of them with `fw.h`.
- An update is the old hub sending and the new hub confirming. A change to
  the frames or to the `fw` console lines has to keep that working across
  one version, or say in its notes how to get across. Version 0002 (the
  two rates) kept it: `HELLO` and `INFO` grew at their ends, and 0001 reads
  what it knows of them.
- `tests/test_update.py` makes its builds at versions 0102 and 0103, away
  from `FW_VERSION`, so the next change of that number does not meet them.
