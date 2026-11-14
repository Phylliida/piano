# Firmware

Two programs for the AT32F405RCT7: one for a tile (40 keys, four ports) and
one for the hub (USB MIDI and a serial console, the tiles' power).
`DESIGN.md` says what they do and what they say to each other.

**Nothing here has run on hardware.** No board exists yet. The portable part
(`core/`) is tested on a PC, alone and as whole simulated keyboards. The part
that touches the chip (`port/`, `tile/main.c`, `hub/main.c`, `hub/usb.c`)
compiles and links and has never been executed. "What is untested" below
lists what that leaves open.

```
core/    portable C11: frames, links, the tree, keys, notes, power, console
port/    the chip: clock, time, UARTs, flash, I2C, restarting
tile/    the tile's pins, key scan and main loop
hub/     the hub's pins, measuring, USB and main loop
sim/     tiles, a hub and cables on a PC
tests/   unit tests and scenario tests
tools/   fetch_deps.sh, gen_lut.py, flash_tile.py
```

## Test

Needs a C compiler with the address and undefined-behaviour sanitizers (gcc
or clang) and GNU make. Nothing else, no downloads.

```
make test
```

builds `core/` and `sim/` for the PC and runs the nine test programs. Each
prints one line; the last line is `all tests passed` or `TESTS FAILED`.

```
make test-tools
```

tests `tools/flash_tile.py`, the script that flashes a tile through the hub,
and needs Python 3.8 as well (about 40 s). Its scenarios run twice: against
the hub's own program with one tile in the simulator (`tests/hubsim.c`, the
hub behind a pipe), and against an imitation of the hub written in Python,
which can also stage what the real program never does. The tile's chip, its
flash and its ROM bootloader, is an imitation both times, written from
Artery's manual. `make` alone does not run this.

## Build

The ARM builds need two outside libraries, which are not kept in this
repository:

```
bash tools/fetch_deps.sh
```

downloads one tarball each of Artery's AT32F402_405 firmware library
(v2.1.5) and TinyUSB (0.21.0) at pinned commits, checks them against the
sha256 sums recorded in the script, and unpacks only the directories the
build reads into `deps/` (git-ignored). Nothing in `deps/` is ever run: our
Makefile compiles their C files by path.

Then, with `arm-none-eabi-gcc` (tried with 14.3):

```
make tile CROSS=/path/to/bin/arm-none-eabi-     # build/tile/tile.elf .bin .map
make hub  CROSS=/path/to/bin/arm-none-eabi-     # build/hub/hub.elf .bin .map
```

`CROSS` can be left out when the tools are on the PATH. `make` alone does
the tests and both builds; `make clean` removes `build/`.

Both programs are linked for 0x08000000 and leave the last 2 KB of flash
(0x0803F800) alone: that is where settings and calibration are kept. A mass
erase clears them; flashing the program does not.

`tools/gen_lut.py` makes `core/lut_table.c`, the table from sensor reading
to key travel. The file is committed, so the build needs no Python. Its
numbers are guesses until a real switch is measured (the script says how).

## Flash

None of this has been tried.

**The hub, over USB (DFU).** Hold the BOOT button while plugging the hub's
USB socket into the computer. BOOT0 is then high at reset and the chip
starts its ROM bootloader, which is a USB DFU device on the same socket.
With the hub's firmware already running, `bootloader hub` on the console
does the same without the button. Then write `build/hub/hub.bin` to
0x08000000 with Artery's ICP/ISP programmer, or with dfu-util:

```
dfu-util -a 0 -d 2e3c:df11 -s 0x08000000:leave -D build/hub/hub.bin
```

(2e3c:df11 is what Artery's ROM DFU is known to report on other AT32 chips;
check with `dfu-util -l`.) Unplug and plug in again without the button.

**Either board, over SWD.** Both have a row of six pads on the back: 3.3 V,
BOOT0, SWCLK, reset, SWDIO, ground. Any probe and program that knows the
AT32F405 will do: Artery's AT-Link with their ICP programmer, or their
OpenOCD fork. Load the `.bin` at 0x08000000 or the `.elf`.

**A tile, through the hub.** A tile has no USB socket. Its ROM bootloader
takes a program over a UART, and `tools/flash_tile.py` gives it one through
the hub, for the tile that the hub's cable is plugged into:

```
python3 tools/flash_tile.py            flashes build/tile/tile.bin
python3 tools/flash_tile.py other.bin
python3 tools/flash_tile.py --check    only looks: the hub, the tile, the file
python3 tools/flash_tile.py --rom      for a tile you put into its ROM by hand
python3 tools/flash_tile.py --help     the other options
```

It needs Python 3.8 and nothing else on Linux and macOS; on Windows it
needs pyserial, and that path has never run. On Linux it finds the hub by
its USB ID; elsewhere it does so only with pyserial installed, and
`--port` names the serial port otherwise. On the console it types

```
bootloader <tile> <ms>       the tile restarts into its ROM; every other
                             tile and the hub keep off the cables that long
bridge 115200 e straight     the console becomes a plain UART on the hub's
                             cable (even parity); `swapped` if nothing
                             answers
```

and then speaks the ROM's UART protocol (Artery's PM0005) through the
bridge: it erases the 2 KB sectors the program covers, writes the program,
reads it back and compares, and restarts the tile. It ends the bridge by
letting go of DTR and waits until the hub hears the tile again. For the
tile's program, 15 KB, the script's own guess is 7 s of flashing at 115200
baud, and it asks for 13 s of quiet; the tile is back when those are over.

It never erases or writes the last 2 KB of flash (settings and
calibration), never erases the chip as a whole, and leaves the chip's
protection settings alone. It refuses a file that is not a program for this
chip, and the hub's program.

`--rom` is for a tile that the console cannot send into its ROM: a blank
one, or one whose program does not start. The script makes the cables quiet
and then asks you to restart the tile with its BOOT0 pad bridged to the
3.3 V pad beside it (the row of six pads on the tile's back). The order
matters: the ROM measures the speed by the first byte it hears.

**The script has never met a real hub or a real ROM**; see `make
test-tools` above for what it has met. Things to know:

- The hub's cable has to be in the tile's **W or S** port. The ROM listens
  on USART2 and USART3, which are those two; the hub's box hangs on a
  tile's E edge, whose UART the ROM does not know. The script asks the
  console which port the cable is in and stops, with nothing changed, if it
  is N or E.
- It flashes one tile, the one on the hub's cable. The bridge reaches no
  further.
- Which of `straight` and `swapped` is right depends on the cable. The
  script tries `straight` first; `--orientation` says which.
- Artery's FAQ0122 says the ROM picks the first of its interfaces that sees
  a signal, and that floating receive pins can make it pick wrongly. On a
  tile PA10 (USART1 RX) is not connected, and neither is the RX pin of a W
  or S port with no cable in it.
- If the flashing is cut off between erasing and the last write, the tile
  has no complete program and sits in its ROM. The script says so, and what
  to type next (`--rom`, pressing Enter twice without touching the tile).
  When the quiet is over the hub speaks on the cable again, at a speed the
  ROM does not expect; should the ROM stop answering because of that, the
  tile has to be restarted into its ROM by hand.
- What only hardware can tell: whether the firmware's jump into the ROM
  works; the order of the bytes of the chip ID (the script takes either);
  whether the ROM knows Reset Device (the script falls back to Jump); and
  the times, which are generous guesses.

Until this has worked on a real tile, count on flashing tiles over SWD.

## Console

The hub's serial port (any terminal program; the speed does not matter).
Lines end with CR or LF. Every command answers with lines and then `ok` or
`error: ...`.

```
help                              this list
version                           "piano hub, firmware 0001, protocol 1, id ..."
tiles | grid | info <tile>        what is plugged in, and where
get [name] | set <name> <n>       hub settings: base stepa stepb channel vel1
                                  vel127 bend aftertouch pd
layout [harmonic|janko|fifths|custom <a> <b>]
tget <tile|all> <name> | tset <tile|all> <name> <n>
                                  tile parameters: lo hi release retrigger
                                  posmode posinterval rawinterval swing
cal start|stop|forget [tile|all]  measure how far the keys go down
save [hub|all|<tile>]             keep settings and calibration in flash
ping [tile|all] | stats
events on|off                     print every key event and tile log line
adc <tile> on|off | adc off       print one tile's ADC values
power [auto|off|retry]            show the power state, or change it
panic                             all notes off
quiet <ms>                        the tiles stop sending for a while
reboot hub|all|<tile>
bootloader hub|<tile> [quiet ms]
bridge <baud> [n|e|o] [straight|swapped]
```

A tile is its number in `tiles`, its place `u,v`, or its 8-digit hex ID.

Settings: `base` -500..500 (the MIDI note of the south-west key, 48),
`stepa`, `stepb` -24..24 (4, 7), `channel` 1..16, `vel1`, `vel127` 1..65535
(the key speeds that give velocity 1 and 127), `bend` -100..100,
`aftertouch` 0 or 1, `pd` 5, 9, 12 or 15 (the voltage asked of the
charger). Tile parameters and their ranges are in `DESIGN.md`, "Keys".

Things that are easy to trip over:

- Polyphonic aftertouch needs both `set aftertouch 1` and
  `tset all posmode 1`.
- To calibrate: `cal start`, press every key to the floor, `cal stop`,
  `save all`.
- `save` stops the key scan of a tile for about 18 ms, and if the write
  fails the old record is gone as well.
- `adc <tile> on` fills that tile's way to the hub; it is for looking at
  one tile, not for playing meanwhile.
- A tile forwards a command as soon as it arrives, so `ping` shows `0 ms`
  for tiles a hop or two away.
- The bridge ends when the program on the computer lets go of DTR after at
  least one byte went through. A terminal that toggles DTR in mid-session
  ends it.

Buttons and lights: USER is "all notes off" (and ends a bridge); BOOT is
for the ROM bootloader, above. POWER is lit while the tiles are fed, blinks
fast while the hub waits to try again after a fault and slowly once it has
given up. STATUS is lit with a tile on the cable, blinks slowly with none,
flickers during a bridge, and is dark until the computer has set USB up.

## USB ID

The hub reports 0x1209:0x0001. That is pid.codes' test ID, meant for the
bench only and shared with everybody else's unfinished devices. Before a
hub leaves the bench, a real product ID has to be requested (pid.codes
gives them to open hardware) and put into `hub/usb.c`.

## What is untested

On hardware: everything. In particular these were taken from Artery's
headers, the reference manual, the datasheet or TinyUSB's board files and
never seen working:

- the clock setup (216 MHz from 12 MHz, USB's 48 MHz from the same PLL);
- USB on PA11/PA12 with no pin setup and with the VBUS pin ignored, and
  whether that setting survives TinyUSB's reset of the controller;
- the UARTs: DMA request routing, receiving into a ring while flash is
  erased, swapping TX and RX (`TRPSWAP`) between frames;
- the key scan: mux settling time, ADC sample times, the timer trigger;
- the hub's ADC through 10 k series resistors, PC13 as an input;
- I2C to the CH224A at about 10 kHz with only that chip's pull-ups, and the
  CH224A's registers (from its datasheet);
- flash: sector size, write and erase, what USB and the cables do during
  the 18 ms the chip stands still;
- the jump into the ROM bootloader (address 0x1FFFA400 from the datasheet,
  method from Artery's AN0008, written for another chip of the family);
- the travel table: its magnet model is a guess.

Known weak spots, by design or by omission:

- The hub declares 500 mA to the computer, and feeds the tiles from it
  through an eFuse limited to 0.5 A; the hub's own few tens of mA come on
  top.
- When the hub restarts (`reboot hub`, `bootloader hub`, a reset) its
  eFuses switch off and the tiles lose power.
- The hub does not go to a low-power mode when USB is suspended; it only
  switches the tiles off.
- If no program reads the MIDI port, messages are dropped after a quarter
  of a second; how the computer's own driver buffers them differs between
  systems and has not been looked at.
- `tools/flash_tile.py` reaches only the tile on the hub's cable, and only
  by that tile's W or S port, while the hub's box is made for a tile's E
  edge. Flashing a whole keyboard through the hub means moving the cable
  from tile to tile, or a change to the hardware.

## Licences

The project's own code is MIT (the repository's licence). The two
downloaded libraries keep their own: Artery's AT32F402_405 firmware library
is BSD-3-Clause, TinyUSB is MIT; `tools/fetch_deps.sh` unpacks each
`LICENSE` beside the code. A binary built from this tree contains both.
