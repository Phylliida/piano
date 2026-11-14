# Firmware design

Two programs for the same MCU (AT32F405RCT7): one for a tile, one for the
hub. This file says what they do and what they say to each other. Where the
code and this file disagree, one of them has a bug.

Nothing here has run on hardware yet: no board has been made.

## Parts

```
core/    portable C11, no hardware access, no allocation. Everything below
         "Link", "Network", "Keys", "Hub", "Firmware update" lives here and
         runs on a PC too.
port/    AT32F405: clock, time, UARTs, flash, I2C, restarting.
boot/    the bootloader: the first 8 KB of a tile's flash and of the hub's.
         It chooses the program to start.
tile/    the tile program: its pins, the key scan, the main loop that joins
         core and port.
hub/     the hub program: its pins, measuring, USB (TinyUSB), the main loop.
sim/     a PC program: any arrangement of simulated tiles and a hub, wired
         by simulated cables (straight or reversed, lossy on request), each
         with its program flash.
tests/   unit tests of core/, scenario tests on sim/, tests of the tools.
tools/   fetch the two outside libraries; make the travel table; make
         program images; send new programs to every tile and the hub; put a
         tile's first program in through its ROM.
```

The core never calls the port. The port (or the simulator) calls the core:
"here are bytes from port 2", "here is a scan of 40 ADC values", "it is now
t", and the core answers with bytes to send and events.

`core/proto.h` holds every number of this file that both programs must agree
on.

## Link: one cable

- UART, 8N1, 1 Mbaud, idle high. Each side enables a pull-up on its receive
  pin.
- A frame on the wire is `COBS(body) 0x00`. `body = type, payload, crc`.
  The CRC is CRC-16/CCITT-FALSE (polynomial 0x1021, start 0xFFFF) over type
  and payload, low byte first. Payload is at most 60 bytes. A frame that
  fails COBS, length or CRC is dropped and counted.
- A frame is sent in one piece. Bytes of a frame that stopped half way and
  were followed by 3 ms of silence are thrown away, and so is a half frame
  when the port swaps its pins: they are the tail of something cut off.
- Multi-byte fields are little-endian.

### Which way round the cable is

A port's pins are `A` (pin 3) and `B` (pin 4). A port starts *straight*:
it sends on A and listens on B. A cable either crosses A and B, and then two
straight ports hear each other, or it does not, and then both send into each
other (harmless: 220 ohm on each side) and hear nothing.

So a port that has heard no valid frame for a while swaps its two pins (the
USART's `TRPSWAP` bit) and tries again:

- Every port sends `HELLO` every 100 ms.
- A port that has heard no valid frame for its dwell time swaps, says
  `HELLO` at once, and draws a new dwell time: random, 250 to 500 ms. The
  random source is seeded from the chip's unique ID, so two tiles do not
  swap in step for long.
- A port is *up* while it has heard a valid frame in the last 350 ms. An up
  port does not swap.
- The port code swaps the pins only while the transmitter is idle.

### HELLO, type 0x01, never forwarded

| bytes | field |
|---|---|
| 1 | protocol version (1) |
| 1 | kind: 0 tile, 1 hub |
| 4 | sender's ID |
| 1 | sender's port: 0 W, 1 N, 2 E, 3 S; the hub's port is 0 |

A node's ID is the 32-bit FNV-1a hash of its 96-bit chip ID. 0 and
0xFFFFFFFF are not used as IDs.

From the HELLOs, each tile knows who is on each of its ports, and tells the
hub (`INFO`). Tiles know nothing about coordinates.

## Network: many tiles, one hub

Tiles on a grid form loops, so frames are not flooded freely. The hub grows
a tree instead.

### BEACON, type 0x10, hub to everyone

| bytes | field |
|---|---|
| 4 | hub's ID |
| 2 | sequence number, +1 per beacon |
| 1 | hops: 0 from the hub, +1 per tile |
| 1 | flags, 0 for now |

The hub sends one every 50 ms; its first one 750 ms after it started, when
every tile has forgotten the hub that was there before. A tile keeps one
*parent* port:

- A beacon from the parent port with a newer sequence number than the last
  one forwarded: forward it on every other port with hops + 1.
- A beacon from another port, newer than the last one forwarded, and either
  the tile has no parent or the beacon's hops are lower than the parent's:
  that port becomes the parent; forward as above.
- Anything else: remember the port's hops, do not forward. A beacon with
  255 hops is never forwarded.
- No beacon from the parent for 200 ms: the tile has no parent. After
  500 ms without a parent it forgets the hub and the last sequence number,
  so a restarted hub is accepted.
- Sequence numbers compare as signed 16-bit differences.

"Newer than the last one forwarded" is what keeps the tree free of loops: a
tile's own descendants can only offer it sequence numbers it has already
sent them.

A tile follows the hub it found first and ignores beacons with another hub
ID until it has no parent. Two hubs on one keyboard are not supported, but
must not break anything.

### Upstream, types 0x20 to 0x2F, tile to hub

A tile sends its own upstream frames out of the parent port. An upstream
frame that arrives on any other port is forwarded out of the parent port
unchanged. One that arrives on the parent port, or while there is no parent,
or that carries the tile's own ID, is dropped. Every upstream payload starts
with the 4-byte ID of the tile that wrote it.

Each port has a send queue of 24 frames. On a port, a `HELLO` that is due
goes first, then the newest `BEACON` (an older one that has not left is
replaced), then the queue in order. When the queue is full, the oldest of
the least important frames in it makes room, unless the new frame is less
important still, and then that one is dropped. From least to most important:
`RAW` and `LOG`; `POS`; `INFO` and `STATE`; `NOTE` and `REPLY`; `CMD`. Both
kinds of drop are counted.

When the parent changes, the upstream frames still queued move to the new
parent's queue. When the parent is lost, they are dropped.

| type | name | payload after the ID | when |
|---|---|---|---|
| 0x20 | NOTE | event counter (1), key (1: bit 7 = on, bits 0-5 = key index), speed (2) | a key goes on or off |
| 0x21 | POS | up to 28 x [key index (1), depth (1)] | every `pos interval` ms while `pos mode` is not 0, only keys whose depth changed |
| 0x22 | STATE | event counter (1), bitmap of keys that are on (5, key 0 = bit 0 of byte 0) | every 100 ms |
| 0x23 | INFO | see below | every second, and at once when a port's neighbour or the parent changes |
| 0x24 | RAW | scan counter (1), first key (1), count (1), count x ADC value (2) | while raw mode is on: every `raw interval` ms one scan, as two frames of 20 keys |
| 0x25 | REPLY | command counter (1), status (1), data | answer to a CMD |
| 0x26 | LOG | text | debugging |

The key index is `5 * column + row`: column 0 is the west one, row 0 the
south one (`pcb/README.md`).

The event counter goes up by one with every note a tile makes, also when
the `NOTE` could not be sent. More than 28 changed depths go out in turn,
over several `POS` frames. Each tile starts its `STATE` and `INFO` timers at
a random moment, so that the tiles of a keyboard do not all send at once.

`INFO`, 33 bytes: firmware version (2), flags (1: bit 0 = every key's bottom
has been measured, bit 1 = calibrating now, bit 2 = raw mode, bit 3 = the
program runs from slot B, bit 4 = it is on trial, bit 5 = a new program
waits for the next restart; see Firmware update), columns (1),
rows (1), parent port (1, 0xFF = none), hops (1, 0xFF = none), then for each
port W, N, E, S: the neighbour's ID (4, 0 = nobody) and the neighbour's port
(1); then seconds since start (2, stops at 65535), frames dropped for a bad
CRC (2), frames dropped from a full queue (2).

`STATE` is what makes lost frames harmless: the hub compares each bitmap
with what it believes. A key the hub holds on that two bitmaps in a row
show off is released; a key it holds off that two in a row show on is
started, at a middle velocity. A `STATE` whose event counter is older than
the last `NOTE` heard was overtaken on the way and is ignored; two such in a
row mean the tile started again, and its counter is taken over. A `NOTE`
whose counter is not newer than the last is ignored and counted; a gap in
the counters is counted as notes lost.

A tile not heard for 500 ms has all its keys released. One not heard for
3 s is dropped from the hub's table.

### Downstream, type 0x11 CMD, hub to tiles

| bytes | field |
|---|---|
| 4 | target ID, 0xFFFFFFFF = every tile |
| 1 | command counter |
| 1 | operation |
| ... | arguments |

A tile accepts a CMD only from its parent port and forwards it on every
other up port, so each tile gets it once.

- **To one tile.** The tile runs it if the counter differs from the last
  targeted command it ran, and answers with `REPLY`. A repeated counter gets
  the same reply again, without running twice. The hub repeats the command
  up to five times, 100 ms apart, until the reply comes; then it says the
  tile does not answer.
- **To every tile.** Nobody answers, so the hub sends it three times, 5 ms
  apart, with the same counter; a tile runs it once. Tiles remember the
  last counter of each kind separately.
- A tile forgets both counters when it forgets the hub. The hub starts its
  counter at a random value and never gives a tile the counter it gave it
  last.

`REPLY` status: 0 ok, 1 no such operation or parameter, 2 bad argument,
3 failed, 4 busy.

| op | name | arguments | does |
|---|---|---|---|
| 1 | PING | | reply |
| 2 | GET | parameter (1) | reply with its value (4) |
| 3 | SET | parameter (1), value (4) | set it, in RAM |
| 4 | SAVE | | write parameters and calibration to flash; the reply comes when that is done (ok or failed); a second SAVE meanwhile: busy |
| 5 | CAL | 0 stop, 1 start, 2 forget | see Keys; the reply to stop carries the number of keys measured (1) |
| 6 | RAW | 0 off, 1 on | stream ADC values |
| 7 | REBOOT | | restart, 250 ms later; busy from a program on trial in its first second |
| 8 | BOOTLOADER | | restart into the ROM bootloader, 250 ms later |
| 9 | QUIET | milliseconds (2) | from 20 ms after it arrives: send nothing on any port, and hear nothing, for that long |
| 10 | FW_INFO | | reply with what the tile runs and what its other slot holds (18) |
| 11 | FW_BEGIN | length (4) | a new program of that length is coming |
| 12 | FW_WRITE | offset (4), 4 to 48 bytes | the next piece of it; the reply carries the offset wanted next (4) |
| 13 | FW_COMMIT | | it is all there: check it, and start it at the next restart; the reply carries the offset (4) |
| 14 | FW_CONFIRM | | the program that runs is to stay |

The 250 ms let the reply out, and let a repeat of the command still find
the tile there. The 20 ms let what is queued leave. When a quiet begins the
queues are emptied and the parent is forgotten; when it ends every link
starts again (pins the way round they were, `HELLO` at once).

Operations 10 to 14 are for one tile at a time: sent to every tile, they
are not run. Firmware update, below, says what they do. A `REPLY` carries at
most 18 bytes of data.

## Keys (tile)

Scanning, in `tile/main.c`: a timer starts the ADC 16 000 times a second.
Each time, the ADC converts the five mux outputs in one sequence into memory
by DMA; the interrupt at the end files the five values under their keys and
sets the next select value, which has the rest of the step to settle. 8
steps make one scan: 2000 scans a second. The core gets each finished scan
as 40 values in key-index order, with the scan's number. A gap in the
numbers (scans are lost while flash is written) counts as that much time.

For every key, every scan, the core does:

1. **Filter:** `f = (f + raw) / 2`.
2. **Rest and swing.** `rest` is the value with the key up; `swing` is how
   far the value moves from there to the bottom. A sensor's output may rise
   or fall as the key goes down; the sign of the swing says which.
   - At start, `rest` is the mean of the first 64 scans. Afterwards it
     follows the filtered value, and only while the key is idle and the tile
     is not calibrating: slowly (a couple of seconds) while the value is
     within 1/32 of the swing, faster (an eighth of a second) when the value
     is beyond rest on the up side, which means rest was wrong.
   - The swing comes from flash. Without one, it is the `nominal swing`
     parameter and grows whenever a press goes further.
   - Calibration (`CAL start`) forgets the measured swings and records the
     furthest value of each key, either way, until `CAL stop`. A key that
     moved at least 64 ADC counts is then *measured*: its swing is fixed and
     its travel is clamped to it. `CAL forget` goes back to the nominal
     swing. `SAVE` keeps what was measured.
3. **Travel:** `s = (f - rest) / swing`, clamped to 0..1, then through a
   table that undoes the magnet's curve, giving travel `x` from 0 (up) to 1
   (down). `tools/gen_lut.py` makes the table from a model of the field; it
   has to be measured on a real switch before the numbers mean millimetres.
4. **Notes.** Four levels, all parameters, in travel: `lo` 0.15, `hi` 0.60,
   `release` 0.50, `retrigger` 0.10.

   ```
   IDLE  --x >= lo-->  DOWN      remember when x crossed lo
   DOWN  --x >= hi-->  HELD      NOTE on; speed from the time between lo and hi
   DOWN  --x < lo - 0.02-->  IDLE   (a little hysteresis; lo / 2 if lo <= 0.04)
   HELD  --x <  release-->  UP   remember when; follow the lowest x since
   UP    --x <  lo-->  IDLE      NOTE off; speed from the time between release and lo
   UP    --x >= hi-->  HELD      if x had come up to `retrigger` or more below
                                 `release`: NOTE off (speed from release to the
                                 turning point), NOTE on (from the turning point
                                 to hi); else nothing, the key is still held
   ```

   Crossing times are interpolated between the two scans either side, so the
   speed is finer than the scan rate. Speed is in hundredths of full travel
   per second, 1 to 65535 (never 0).
5. **Depth.** For `POS`: 0 to 255. With `pos mode` 1, the travel past `hi`
   of keys that are on (for aftertouch; a key on the floor says 255); with
   2, the whole travel of every key; with 0, nothing is sent.

Tile parameters (GET / SET), all integers, with their defaults and ranges:

| | name | default | allowed |
|---|---|---|---|
| 1 | `lo` (thousandths of travel) | 150 | 10 to below `release` |
| 2 | `hi` | 600 | `release` to 990 |
| 3 | `release` | 500 | above `lo`, up to `hi` |
| 4 | `retrigger` | 100 | 0 to 1000 |
| 5 | `pos mode` | 0 | 0, 1, 2 |
| 6 | `pos interval` (ms) | 5 | 1 to 1000 |
| 7 | `raw interval` (ms) | 20 | 2 to 1000 |
| 8 | `nominal swing` (ADC counts) | 600 | 16 to 4095, or -16 to -4095 for a sensor whose output falls |

Flash: the last 2 KB sector holds one record: magic (4), version (2), flags
(2, 0), the contents, CRC-32 (4). A tile's contents are the eight parameters
(4 each) and 40 x (rest, bottom) (2 each); the hub's are its settings. A
record with the wrong magic, version, length or CRC means defaults, and so
does a tile record with a parameter out of range (none is taken). Of each
(rest, bottom) pair only the difference is used: where rest is today is
measured at every start. `rest == bottom` means the key was never measured.
Writing takes about 18 ms, during which the chip stands still; if it fails,
the old record is gone too.

## Hub

### Tiles and where they are

The hub keeps a table of up to 32 tiles: ID, last heard, the `INFO`
contents, the keys that are on and the note each started. From the neighbour
lists it lays the tiles on a grid: the tile on the hub's cable is at (0, 0);
a tile on another's E port is one to the right, on its N port one up, W
left, S down. Then it shifts everything so the smallest `u` and `v` are 0.
Links that disagree with the grid (an E port cabled to an N port, two tiles
wanting the same place) are reported by `grid` and ignored for the layout.
A tile that cannot be reached from the root has no place and makes no notes.

### Notes

Tile `(u, v)`, key `(c, r)` is lattice point
`a = 8u + c`, `b = 5v + r - floor(a / 2)` (`tilegeom.axial`). One step in
`a` is the key up and to the right, one step in `b` the key straight up.

`note = base + a * step_a + b * step_b`, on one MIDI channel; notes outside
0..127 are not sent. Presets set the two steps: `harmonic` (4, 7), `janko`
(1, 2), `fifths` (7, 12), and `custom`.

Two keys can be the same note. The hub counts the keys held on each note:
a second key on a note that sounds sends note off and note on again; the
note off goes out when the last key lets go. A key ends the note it
started, whatever the settings say by then.

Speed becomes MIDI velocity through a curve with three numbers: the speeds
that give velocity 1 and 127, and a bend. Between them the velocity follows
the logarithm of the speed.

`POS` becomes polyphonic aftertouch (depth / 2) for keys that are on, when
the hub's `aftertouch` setting is 1. The tiles send `POS` only when their
`pos mode` is 1, so both have to be set.

### USB

One device, two functions (TinyUSB, port 0 = the full-speed port on PA11 and
PA12), IDs 0x1209:0x0001 (a test ID, see the README). The chip's VBUS pin is
not wired; the controller is told not to wait for it.

- **MIDI:** note on, note off, polyphonic aftertouch. If no program takes
  the messages for a quarter of a second they are dropped and counted
  instead of piling up; when one takes them again it first gets "all notes
  off".
- **Serial port (CDC):** a console of text lines. Each command answers with
  lines and then `ok` or `error: ...`, and the console says nothing unasked.
  It lists tiles and their grid, shows and sets the hub's settings and the
  tiles' parameters, starts and stops calibration, saves, shows the power
  state, and switches on streams of lines: every key event with its tile,
  key, speed, note and velocity (`events on`), or one tile's ADC values
  (`adc <tile> on`). `firmware/README.md` lists the commands.
- `bridge <baud> [n|e|o] [straight|swapped]` turns the serial port into a
  plain UART on the hub's cable, for talking to a tile's ROM bootloader.
  All notes are let go and the hub says nothing on the cable meanwhile. The
  bridge ends when the program on the computer closes the serial port (lets
  go of DTR) after at least one byte went through, or with the USER button.
- `bootloader <tile> [ms]` sends that tile into its ROM and then tells every
  other tile, and the hub itself, to keep quiet for that long (20 s unless
  told), so that nothing but the bridge speaks on the cables. A bridge that
  begins inside such a quiet does not end it: when the bridge is over and
  the quiet is not, the hub keeps silent until the quiet's time is up.
- `fw <tile|hub> ...` shows what a node runs and gives it a new program, 48
  bytes to the line. For a tile each line is one `CMD`; for the hub the
  console calls the same code. See Firmware update.

### Power

The hub board has two inputs and one output, `VIN`, to the tiles:

- The PD port: a CH224A asks the charger for a voltage. The hub tells it
  which over I2C (about 10 kHz, and only while the charger's voltage is
  there: the chip is powered from it) and reads back what was agreed.
- The USB port to the computer: 5 V. Its CC pins say how much the computer
  offers (0.5, 1.5 or 3 A).
- Each input reaches `VIN` through an eFuse (TPS259470A) that the firmware
  switches. They limit the current, block reverse current, and report
  faults. The hub measures `VIN`, both inputs, both currents and both CC
  pins, a thousand times a second.

The policy is one state machine in the core (`core/power.h`), fed with
millivolts, milliamps and pin states, answering with the two enables, the
current limit select and the voltage to ask for:

- The hub asks for the `pd` setting: 15 V unless set to 12, 9 or 5. If the
  charger does not settle on it within 1.5 s the hub asks one step lower
  (15, 12, 9), and after 9 it takes what there is.
- PD input ready and at most 17 V: PD path on, host path off. Ready means
  8 to 17 V and steady for 50 ms, or about 5 V when 5 V is what was asked
  for or all that is left. 20 V is never asked for and never switched
  through.
- Only the computer's 5 V: host path on; off while the bus is suspended,
  and off until the computer has configured the device. The eFuse's limit
  is 0.5 A; it is raised to 1.43 A while the CC pins have shown 1.5 A or
  more for 100 ms, and lowered at once when they stop.
- Going from the computer's 5 V to the charger, both paths are on for 20 ms
  so the tiles do not lose power.
- A fault (the eFuse's flag for 20 ms with `VIN` below 95 % of the source,
  or `VIN` below 80 % of the source for 10 ms once the path has been on for
  100 ms): off, wait a second, try again; after three faults in a row, stay
  off until a source is unplugged or the console says `power retry`. Ten
  seconds without a fault clear the count.

### LEDs and buttons

- POWER: lit while the tiles are fed, blinking fast while the hub waits to
  try again after a fault, slowly once it has given up, else dark.
- STATUS: lit with at least one tile on the cable, blinking slowly with
  none, flickering during a bridge, dark while the computer has not set USB
  up.
- USER button: every note off (`panic`); during a bridge, ends the bridge.
- BOOT button, held while the hub gets power: the ROM's USB DFU.

## Firmware update

Every tile and the hub take a new program through the hub's USB: a tile
over the cables, wherever among the tiles it is, and the hub from its own
console. `core/fw.h` and `core/fw.c` are the code, the same in both programs
and in the bootloader. `tools/update.py` is the program on the computer.

### What is in the flash

256 KB in 2 KB sectors, a tile's and the hub's alike:

| address | size | holds |
|---|---|---|
| 0x08000000 | 8 KB | the bootloader (`boot/`) |
| 0x08002000 | 122 KB | slot A |
| 0x08020800 | 122 KB | slot B |
| 0x0803F000 | 2 KB | nothing |
| 0x0803F800 | 2 KB | the settings record (Keys, "Flash") |

A slot holds one program image and, in its last 16 bytes, a *trailer*. The
program is linked twice, once for each slot's address, and a node is sent
the image for the slot it does not run from. Nothing is ever copied from
slot to slot, and the program that runs is never the one being written.

An image is the program's binary. 0x200 bytes in, behind the vector table,
is its header: magic "PIMG" (4), kind (1: 0 tile, 1 hub), 0 (1), version
(2), the address it was linked for (4), its length (4). Its last four bytes
are the CRC-32 of everything before them. An image is *whole* when the
header fits the slot it is in, the checksum is right, its first word is a
stack pointer in RAM and its second a start address inside the image.
`tools/mkimage.py` fills in the length and the checksum after the link.

The trailer is four words: magic "PTRL", sequence number, tried, ok. A word
of flash can be written once after an erase, so a trailer only ever gains
words, and the words it has say how far its image got:

| state | trailer | means |
|---|---|---|
| NONE | erased | the image was put there from outside (ROM, SWD) |
| PARTIAL | sequence number, no magic | being received, or that was cut short |
| NEW | and the magic | received and checked, never started |
| TRIED | and tried | the bootloader started it once |
| GOOD | and ok | it ran and was confirmed |

The sequence number is the running image's plus one: of two images, the
higher is the newer. A word that is not erased counts as written whatever
it reads, since the power may have gone while it was written.

### The bootloader

The chip starts in the bootloader after every reset. It looks which slots
hold a whole image and starts the first of these:

1. A NEW image, the newer of two, after writing its `tried` word. That
   start is the image's *trial*, and the bootloader starts the watchdog
   (about 4 s) before it: a program that hangs is restarted. If the word
   cannot be written, the image is not started.
2. A GOOD image, the newer of two.
3. An image without a trailer.
4. A TRIED image: nobody confirmed it, but it is better than no program.
   (It still counts itself on trial, and restarts every minute until it is
   confirmed. The network cannot bring a chip here; a programmer can.)
5. The chip's ROM bootloader.

A PARTIAL slot is never started, whatever is in it. So a new program gets
one start before somebody confirms it: any restart during its trial, be it
its own, its watchdog's or the power's, ends in the program that ran before.

The bootloader has no pins and no clock but the 8 MHz the chip starts with.
That one word is all it writes. It is not replaced through the hub.

### Receiving a program

The running program writes the other slot. For a tile these are the `CMD`
operations 10 to 14; for the hub the console calls the same functions.

- **Begin**, with the image's length. Erases the other slot's first sector
  and its last, and writes the new sequence number into the trailer: from
  here on the slot is PARTIAL. A program on trial answers busy: the other
  slot is then the only program known to work.
- **Write**, with an offset and 4 to 48 bytes, a multiple of 4. The offset
  has to be the one the node wants next, and every reply says which that
  is, so a sender that missed a reply knows where to go on. The slot's
  other sectors are erased as the writes reach them. What was written is
  read back. After an erase that failed the same write may be tried again;
  after a write that failed the image is closed (the next offset is 0) and
  has to be begun again.
- **Commit.** The image is checked as a whole, and its kind and length
  with it. Then the trailer's magic is written, the last word of all: the
  image is NEW, and the bootloader starts it at the next restart. A commit
  said twice answers ok.
- **Confirm.** A running program that is on trial becomes GOOD. Any other
  answers ok and changes nothing.
- **Info**, 18 bytes: the running slot (1: 0 A, 1 B), its state (1: 0 NONE,
  1 NEW, 2 TRIED, 3 GOOD, 4 PARTIAL), the other slot's state (1), flags (1:
  bit 0 = the other slot holds a whole image, bit 1 = receiving, bit 2 = an
  image was committed since this program started), the running image's
  version (2) and CRC (4), the other image's CRC (4, 0 without a whole
  image), and while receiving the offset wanted next (4).

While flash is erased (18 ms a sector) or written the chip stands still, as
it does for the settings record. A tile's program is nine sectors, and the
trailer's sector makes ten.

A program on trial says so in `INFO` (bit 4), reloads the watchdog every
round of its main loop, and restarts when it has run for 60 s without a
confirm. In its first second it answers `REBOOT` with busy: the hub says a
command up to five times, and that `REBOOT` is the one that started the
trial, said again. Obeyed, it would end the trial at once.

A program that the bootloader did not start from a slot (one from before
there was a bootloader) answers operations 10 to 14 with "no such
operation".

### The power, and the order of restarts

The tiles have their power through the hub's two eFuses, and those are off
while the hub's chip starts: their enables have pull-downs, and the program
switches them on once it has looked at its inputs. So every restart of the
hub is a power cut for every tile, and a tile that gets power starts in its
bootloader:

- a tile that holds a NEW image starts it, on trial;
- a tile on trial is back in the program before;
- a tile that was receiving has a PARTIAL slot, and runs what it ran.

The update is built around this. First every node gets its image, and
nothing restarts. Then, if the hub got one, the hub restarts, once, and
every tile with it: all the trials begin together. Only when the hub got
none are the tiles told to restart. A node on trial is never restarted on
purpose.

The same cut is the way back for the whole keyboard. A hub program that
nobody confirms restarts after a minute (after 4 s, if it hangs) into the
old one, and that restart ends every tile's trial too.

The simulator plays this when a test sets `hub_feeds`: a hub that restarts,
hangs or sits in its ROM takes every tile down, and they start afresh 300 ms
after its program does.

### tools/update.py

1. Asks the hub for its tiles, and every tile and the hub what it runs.
2. Reads the four images (tile and hub, slot A and slot B). A node gets the
   one for the slot it does not run from; a node that runs this build
   already gets nothing.
3. Confirms what an earlier run left on trial, and takes away a program of
   another build that waits in a slot for the next restart (by beginning a
   new one there). Then sends each image, 48 bytes to a console line, and
   checks that the node holds it as NEW.
4. Restarts: `reboot hub` if the hub got a program, and then waits for the
   hub's serial port and for every tile to be back. A tile that still runs
   its old program with the new one waiting is told to restart (`reboot
   all` when there are several and no tile is on trial, else by name), and
   up to twice again, by name.
5. If every node runs its new program, and every tile that was there
   before is there again, within 45 s of the restart: confirms each one.
   If not, it confirms nothing, and within a minute everything runs what it
   ran before.

A run that was cut off, wherever, is taken up by the next: an image that a
node holds whole is not sent again, and a node that is trying this build is
confirmed.

### What it cannot reach

A chip without the bootloader: a blank one, or one flashed with a plain
program. Its first program is a *factory file* (`mkimage.py factory`): the
bootloader, the image for slot A, and erased flash up to 0x0803F000, so
that slot B and both trailers are empty afterwards and the settings stay.
It goes in through the chip's ROM: the hub's over USB DFU, a tile's with
`tools/flash_tile.py` through the hub's cable, which has to be in the tile's
W or S port, with the tile's BOOT0 pads bridged if the chip is blank. After
that the tile can be anywhere.

The bootloader itself: it is 2 KB of code that should never have to change,
and a network update of it could not be undone.

## The chip

What `port/` and the two `main.c` do that the rest relies on. Every line of
it is untested.

- 216 MHz from the 12 MHz crystal; USB's 48 MHz from the same PLL.
- Time is a millisecond count from SysTick, put right from a free-running
  timer after the chip stood still (a flash write).
- UARTs send and receive by DMA: received bytes land in a ring that the main
  loop empties, so nothing is lost while an interrupt or a flash write holds
  the program up.
- A program starts at its slot's address, not at the start of the flash:
  the bootloader loads the stack pointer and the start address from the
  slot's first two words, and the program points the vector table register
  at its own table before it enables an interrupt.
- Restarting into the ROM bootloader: the program leaves a mark in RAM that
  no start-up code clears, and resets. The bootloader finds the mark and
  jumps to the boot memory (0x1FFFA400) before anything is switched on; it
  goes there too when neither slot holds a program. This follows Artery's
  note AN0008; the address is from the datasheet and has not been tried.
- The watchdog runs only once the bootloader has started it for a trial:
  the chip's slow oscillator, about 4 s, give or take a third. Nothing but
  a reset stops it, so both main loops reload it every round.
- Program flash is written through two functions that keep to the two
  slots, and the one that erases refuses the slot the program runs from.
- Before the hub restarts it unplugs itself from USB for 50 ms. The eFuses
  go off with the reset (their pull-downs): the tiles lose power too. See
  Firmware update, "The power, and the order of restarts".

## What is left for later

- All of Firmware update on real hardware. The bootloader, the slots,
  `tools/update.py` and `tools/flash_tile.py` have met the simulator only;
  `README.md`, "Flash", says what that leaves open.
- A blank tile's first program without touching the tile. It goes in
  through the ROM bootloader, which listens on the tile's W and S ports
  only (USART2, USART3) and starts with BOOT0 high, and the cable has no
  BOOT0 wire: the pads have to be bridged once (unless a blank chip starts
  its ROM by itself, which nobody has checked). A way to do that from the
  cable would be a change to the tile's board.
- More than one hub.
- MPE and other tunings than twelve notes to the octave.
