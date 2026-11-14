# Firmware design

Two programs for the same MCU (AT32F405RCT7): one for a tile, one for the
hub. This file says what they do and what they say to each other. Where the
code and this file disagree, one of them has a bug.

Nothing here has run on hardware yet: no board has been made.

## Parts

```
core/    portable C11, no hardware access, no allocation. Everything below
         "Link", "Network", "Keys", "Hub" lives here and runs on a PC too.
port/    AT32F405: clock, time, UARTs, flash, I2C, restarting.
tile/    the tile program: its pins, the key scan, the main loop that joins
         core and port.
hub/     the hub program: its pins, measuring, USB (TinyUSB), the main loop.
sim/     a PC program: any arrangement of simulated tiles and a hub, wired
         by simulated cables (straight or reversed, lossy on request).
tests/   unit tests of core/ and scenario tests on sim/.
tools/   fetch the two outside libraries; make the travel table.
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
has been measured, bit 1 = calibrating now, bit 2 = raw mode), columns (1),
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
| 7 | REBOOT | | restart, 250 ms later |
| 8 | BOOTLOADER | | restart into the ROM bootloader, 250 ms later |
| 9 | QUIET | milliseconds (2) | from 20 ms after it arrives: send nothing on any port, and hear nothing, for that long |

The 250 ms let the reply out, and let a repeat of the command still find
the tile there. The 20 ms let what is queued leave. When a quiet begins the
queues are emptied and the parent is forgotten; when it ends every link
starts again (pins the way round they were, `HELLO` at once).

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

## The chip

What `port/` and the two `main.c` do that the rest relies on. Every line of
it is untested.

- 216 MHz from the 12 MHz crystal; USB's 48 MHz from the same PLL.
- Time is a millisecond count from SysTick, put right from a free-running
  timer after the chip stood still (a flash write).
- UARTs send and receive by DMA: received bytes land in a ring that the main
  loop empties, so nothing is lost while an interrupt or a flash write holds
  the program up.
- Restarting into the ROM bootloader: leave a mark in RAM that the start-up
  code does not clear, reset, and jump to the boot memory (0x1FFFA400)
  before anything is switched on. This follows Artery's note AN0008; the
  address is from the datasheet and has not been tried.
- Before the hub restarts it unplugs itself from USB for 50 ms. The eFuses
  go off with the reset (their pull-downs): the tiles lose power too.

## What is left for later

- A program for the PC that flashes a tile through `bridge`. The ROM
  bootloader's UART protocol is not in any Artery document found so far.
- Updating a tile through other tiles. The ROM bootloader listens on the
  tile's W and S ports only (USART2, USART3), so the hub's cable has to be
  on one of those (`bootloader <tile>`, then `bridge`), while the hub's box
  hangs on a tile's E edge.
- More than one hub.
- MPE and other tunings than twelve notes to the octave.
