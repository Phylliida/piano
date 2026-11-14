#!/usr/bin/env python3
"""Tests for tools/flash_tile.py.

    python3 tests/test_flash_tile.py        (or: make test-tools)

The script runs here against a hub, the tile on its cable and that tile's
ROM bootloader, none of them real hardware. Every scenario runs twice:

  RealHubTest   the hub is the hub's own program: tests/hubsim.c runs
                core/'s hub and one tile in the simulator of sim/, and
                `SimWorld` here is the pipe to it. The console's words, the
                commands' jobs, the quiet and the hub's table of tiles are
                the real ones. Skipped when build/host/hubsim is not built
                (`make test-tools` builds it).
  ImitationTest the hub is an imitation in Python (`World`), which says what
                core/console.c says and bridges by the rules of hub/main.c.
                It can do what the simulator cannot: a console that chatters,
                a serial port that is not the hub.

In both, the tile's chip is an imitation: its flash, and a ROM bootloader
that follows the device-side flowcharts of Artery's PM0005. Time is the
test's own, a millisecond a step, so a run takes little real time.

Which program starts from that flash is imitated too. Most scenarios flash
a pretend program of the old kind, one that begins at the start of flash
(`make_image`). The ones with a factory file (`make_factory`) have our
bootloader in front, and the slot it would start is found by
`bootloader_starts`, core/fw.c's rules once more. With the real hub the
flash is also handed to hubsim's tile, so that there core/fw.c itself
chooses, and its answer to `fw` is what the script reads; `check_model`
compares the two.

What a real ROM does, and what the hub's USB and UART code does (hub/main.c
is copied into hubsim.c, not run), is not tested here.
"""

import io
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import flash_tile  # noqa: E402
import mkimage  # noqa: E402

FLASH_BASE = 0x08000000
FLASH_SIZE = 0x40000
SECTOR = 2048
RECORD = FLASH_SIZE - SECTOR            # where a tile keeps its settings
ACK, NACK = 0x79, 0x1F
HUBSIM = os.path.join(HERE, "..", "build", "host", "hubsim")


def make_image(version, size=5000):
    """A pretend tile program: stack, start, "FW", version, size, filler,
    and a sum at the end, so that the imitation can tell a whole one."""
    b = bytearray(size)
    struct.pack_into("<II", b, 0, 0x20019800, 0x08000101)
    b[8:10] = b"FW"
    struct.pack_into(">H", b, 10, version)
    struct.pack_into("<I", b, 12, size)
    for i in range(16, size - 4):
        b[i] = (i * 7 + version) & 0xFF         # every byte value, 0x79 and 0x1F too
    struct.pack_into("<I", b, size - 4, sum(b[:size - 4]) & 0xFFFFFFFF)
    return bytes(b)


def make_boot(size=2192):
    """A pretend bootloader: stack, start, filler."""
    b = bytearray((i * 11 + 3) & 0xFF for i in range(size))
    struct.pack_into("<II", b, 0, 0x20019800, 0x08000101)
    b[8:10] = b"BL"
    return bytes(b)


def make_slot_program(version, slot=0, size=5000, kind=0):
    """A pretend program linked for a slot, as the linker leaves one: the
    header's length is not filled in."""
    base = mkimage.SLOT_ADDR[slot]
    b = bytearray((i * 7 + version) & 0xFF for i in range(size - 4))
    struct.pack_into("<II", b, 0, 0x20019800, base + 0x301)
    mkimage.HEADER.pack_into(b, mkimage.HEADER_AT, mkimage.IMAGE_MAGIC, kind, 0, version, base, 0)
    return bytes(b)


def make_slot_image(version, slot=0, size=5000, kind=0):
    """The same as an image of `size` bytes: what tools/update.py sends."""
    return mkimage.make_image(make_slot_program(version, slot, size, kind))


def make_factory(version, size=5000):
    """A pretend factory file: bootloader, the program in slot A, blank up
    to the settings."""
    return mkimage.make_factory(make_boot(), make_slot_image(version, 0, size))


# A slot's last 16 bytes, as core/fw.h has them.
TRAILER = struct.Struct("<4I")          # magic, number, tried, ok
TRAILER_MAGIC = 0x4C525450
ERASED = 0xFFFFFFFF


def slot_at(slot):
    return mkimage.SLOT_ADDR[slot] - FLASH_BASE


def put_trailer(flash, slot, state, seq):
    """The trailer of a slot in this state: "new", "tried" or "good"."""
    marks = {"new": (ERASED, ERASED), "tried": (0, ERASED), "good": (0, 0)}[state]
    TRAILER.pack_into(flash, slot_at(slot) + mkimage.SLOT_LEN - TRAILER.size,
                      TRAILER_MAGIC, seq, *marks)


def slot_of(flash, slot):
    """(state, number, image) of a slot, as core/fw.c reads them: the state
    by the console's name for it, the image as mkimage.Image, or None if
    the slot holds no whole one."""
    base = slot_at(slot)
    words = TRAILER.unpack_from(flash, base + mkimage.SLOT_LEN - TRAILER.size)
    magic, seq, tried, ok = words
    if magic != TRAILER_MAGIC:
        state, seq = ("none" if words == (ERASED,) * 4 else "partial"), 0
    elif ok != ERASED:
        state = "good"
    elif tried != ERASED:
        state = "tried"
    else:
        state = "new"
    try:
        image = mkimage.parse(bytes(flash[base:base + mkimage.IMAGE_MAX]))
        if image.slot != slot:
            image = None
    except mkimage.Bad:
        image = None
    return state, seq, image


def bootloader_starts(flash):
    """The slot the bootloader starts, or None: fw_boot_choose() of
    core/fw.c. Only a whole image; a new one first, then a good one, one
    without a trailer, one that was tried; of two alike the one with the
    higher number, and slot A if that is the same too. (That a new one is
    marked as tried on the way is left out: no test here has a new one.)"""
    order = {"new": 0, "good": 1, "none": 2, "tried": 3}
    found = []
    for slot in (0, 1):
        state, seq, image = slot_of(flash, slot)
        if image is not None and state in order:
            found.append((order[state], -seq, slot))
    return min(found)[2] if found else None


def has_bootloader(flash):
    """Does this flash begin with a bootloader, and not with a pretend
    program of make_image()?"""
    if flash[8:10] == b"FW":
        return False
    boot = bytes(flash[:mkimage.BOOT_LEN]).rstrip(b"\xff")
    try:
        mkimage.check_boot(boot + b"\xff" * (-len(boot) % 4))
    except mkimage.Bad:
        return False
    return True


def program_in(flash):
    """The version of the program that starts from this flash, or None."""
    if has_bootloader(flash):
        slot = bootloader_starts(flash)
        return None if slot is None else slot_of(flash, slot)[2].version
    if flash[8:10] != b"FW":
        return None
    size = struct.unpack_from("<I", flash, 12)[0]
    if not 20 <= size <= RECORD:
        return None
    if struct.unpack_from("<I", flash, size - 4)[0] != sum(flash[:size - 4]) & 0xFFFFFFFF:
        return None
    return struct.unpack_from(">H", flash, 10)[0]


class World:
    """A hub, the tile on its cable, and that tile's ROM bootloader, all
    three imitated."""

    def __init__(self):
        self.ms = 0
        self.events = []                # (when, order, what)
        self.order = 0

        # the hub's serial port
        self.to_pc = bytearray()
        self.typed = bytearray()        # from the computer, not looked at yet
        self.line = bytearray()
        self.dtr = False                # nobody has the port open
        self.closed = False             # hub/usb.c: DTR has fallen
        self.busy_until = 0             # a job or a broadcast is running
        self.quiet_from = 0             # QUIET takes effect 20 ms after it is sent
        self.quiet_until = 0
        self.hub_swapped = False
        self.bridge = None              # {"baud", "parity", "way", "used"}
        self.bridge_soon = None         # the same, while the `ok` leaves
        self.skip_lf = False
        self.typed_lines = []           # every console line the script sent
        self.chatter = False            # `events on`: lines nobody asked for

        # the hub's table
        self.seen = True
        self.heard_at = 0
        self.link_since = -1000
        self.info = {"fw": 1, "up": 2}          # the tile's last INFO, or None

        # the tile
        self.tile_id = 0x1234ABCD
        self.hub_port = "W"             # the tile's port the hub's cable is in
        self.rom_hears = "straight"     # the bridge that reaches the ROM
        self.mode = "app"               # "app", "rom", or "dead": neither runs
        self.app_since = -2500          # not long: see tile_has_been_up()
        self.answers = True             # the tile acknowledges commands
        self.answers_fw = True          # and the question what it runs
        self.jump_works = True          # BOOTLOADER gets it into the ROM
        self.boot0 = False              # BOOT0 pad held at 3.3 V
        self.flash = bytearray(b"\xff" * FLASH_SIZE)
        self.flash[:5000] = make_image(1)
        self.flash[RECORD:] = bytes((i * 13 + 5) & 0xFF for i in range(SECTOR))
        self.settings = bytes(self.flash[RECORD:])

        # the ROM
        self.rom = None
        self.rom_baud = None            # measured from the first 0x7F
        self.rom_deaf = False           # measured from something else
        self.chip_id = bytes([0x32, 0x40, 0x70, 0x05, 0x13])
        self.protected = False
        self.knows_reset = True
        self.corrupt = None             # flash offset whose write goes wrong
        self.dies_after_writes = None
        self.erase_fails = set()        # sectors it says it erased, and did not
        self.erased = []
        self.writes = []                # (offset, length)
        self.reads = 0
        self.mass_erased = False
        self.commands = []              # ROM command codes, in order

        self.interrupt_after_writes = None      # Ctrl-C once the ROM has this many

    # -- time ---------------------------------------------------------------

    def now(self):
        return self.ms / 1000.0

    def sleep(self, seconds):
        self.run(seconds)

    def run(self, seconds, until_output=False):
        steps = max(1, int(seconds * 1000 + 0.999))
        for _ in range(steps):
            self.tick()
            if until_output and self.to_pc:
                break

    def later(self, ms, what):
        self.order += 1
        self.events.append((self.ms + ms, self.order, what))

    def tick(self):
        self.ms += 1
        due = sorted(e for e in self.events if e[0] <= self.ms)
        self.events = [e for e in self.events if e[0] > self.ms]
        for _, _, what in due:
            what()
        self.hub_step()

        # A tile's program talking at 1 Mbaud, as a bridge at another speed
        # hears it.
        if self.bridge and self.mode == "app" and self.ms % 100 == 0:
            self.from_cable(b"\x00\x79\xf8\x1f\x80")

    def hub_step(self):
        """A millisecond of the hub: its console, and its table of tiles."""
        self.console()

        silent = self.hub_silent()
        # The hub's own talk, at 1 Mbaud, is the first thing a ROM hears
        # that has not measured its speed yet.
        if self.mode == "rom" and self.rom_baud is None and not silent and self.hub_port in "WS":
            self.rom_deaf = True

        if self.mode == "app" and not silent:
            if self.link_since is None:
                self.link_since = self.ms
            up = self.ms - self.link_since
            if up >= 300:
                self.seen = True
                self.heard_at = self.ms
            if up >= 900 and (up - 900) % 1000 == 0:        # an INFO
                self.info = {"fw": program_in(self.flash),
                             "up": (self.ms - self.app_since) // 1000}
        else:
            self.link_since = None
            if self.seen and self.ms - self.heard_at > 3000:
                self.seen = False
                self.info = None

    def hub_silent(self):
        return self.quiet_from <= self.ms < self.quiet_until or self.bridging()

    def bridging(self):
        return self.bridge is not None or self.bridge_soon is not None

    def computer_dtr(self, on):
        if self.dtr and not on:
            self.closed = True
        self.dtr = on

    def from_cable(self, data):
        """Bytes on the hub's cable that are no frames: for the bridge."""
        self.to_pc += data

    # -- what a test arranges before the script runs ------------------------

    def tile_is_dead(self):
        """No program runs in the tile, and the hub sees nobody."""
        self.mode = "dead"
        self.seen = False
        self.info = None
        self.tile_changed()

    def make_blank(self):
        self.flash[:RECORD] = b"\xff" * RECORD
        self.tile_is_dead()

    def flash_holds(self, data):
        """What the tile's program flash holds, and so which program runs."""
        self.flash[:RECORD] = b"\xff" * RECORD
        self.flash[:len(data)] = data
        self.info["fw"] = program_in(self.flash)
        self.tile_changed()

    def check_model(self):
        pass

    def just_plugged(self):
        """The hub has only now got its power, and has heard no tile yet."""
        self.seen = False
        self.info = None
        self.link_since = None
        self.app_since = self.ms

    def tile_has_been_up(self, seconds):
        """Unless a test says so, hub and tile got their power 3 s ago:
        then an INFO of the old program and one of the new look alike."""
        self.app_since = self.ms - seconds * 1000
        self.info["up"] = seconds - 1

    def start_quiet(self, ms):
        """Somebody typed `quiet` a moment ago."""
        self.quiet_from = self.ms
        self.quiet_until = self.ms + ms

    def leave_bridge_on(self):
        """A run was cut off before a byte had gone through its bridge."""
        self.bridge = {"baud": 115200, "parity": "e", "way": "straight", "used": False}

    def type_ahead(self, data):
        """Somebody left a line half typed."""
        self.line += data

    def reported(self, version):
        """What the hub calls the firmware of a tile with this program."""
        return "%04x" % version

    def close(self):
        pass

    # -- the console (core/console.c, hub/main.c) ---------------------------

    def say(self, text):
        if self.chatter and not text.startswith("error"):
            self.to_pc += b"ev 0 3 on 100 60 64\r\n"
        self.to_pc += text.encode() + b"\r\n"

    def console(self):
        if self.bridge is not None:
            if self.typed:
                data = bytes(self.typed)
                self.typed.clear()
                if self.skip_lf and data[:1] == b"\n":
                    data = data[1:]
                self.skip_lf = False
                if data:
                    self.bridge["used"] = True
                    self.to_cable(data)
            if self.closed:
                self.closed = False
                if self.bridge["used"]:
                    self.bridge = None
                    self.typed.clear()
                    self.line.clear()
            return
        if self.bridge_soon is not None:
            return
        while self.typed and self.ms >= self.busy_until and self.bridge_soon is None:
            c = self.typed.pop(0)
            if c in (13, 10):
                if self.line:
                    text = self.line.decode()
                    self.line.clear()
                    self.skip_lf = c == 13
                    self.command(text)
            elif c in (8, 127):
                del self.line[-1:]
            elif 0x20 <= c < 0x7F:
                self.line.append(c)

    def tile_named(self, who):
        if self.seen and who in ("0", "0,0", "%08x" % self.tile_id):
            return True
        return False

    def command(self, text):
        self.typed_lines.append(text)
        words = text.split()
        name, args = words[0], words[1:]
        error = None
        if name == "version":
            self.say("piano hub, firmware 0001, protocol 1, id 0badcafe")
        elif name == "grid":
            if self.seen:
                self.say("hub on tile 0")
                self.say("1 x 1")
                self.say("  0")
            else:
                self.say("hub: no tile on its cable")
                self.say("0 x 0")
        elif name == "info":
            if not args:
                error = "usage: info <tile>"
            elif not self.tile_named(args[0]):
                error = "no such tile"
            else:
                self.say("tile 0 id %08x at 0,0" % self.tile_id)
                self.say("heard %d ms ago, 0 keys down" % (self.ms - self.heard_at))
                if self.info is None:
                    self.say("no info yet")
                else:
                    self.say("firmware %04x, 8 x 5 keys, not calibrated" % self.info["fw"])
                    self.say("to the hub: port %s, 0 tiles between" % self.hub_port)
                    for p in "WNES":
                        self.say("%s: %s" % (p, "the hub" if p == self.hub_port else "nobody"))
                    self.say("up %d s, 0 bad frames, 0 frames dropped from a full queue"
                             % self.info["up"])
        elif name == "quiet":
            if len(args) != 1 or not args[0].isdigit() or not 1 <= int(args[0]) <= 65535:
                error = "usage: quiet <ms>, 1 to 65535"
            elif self.ms < self.quiet_until:
                error = "the cable is quiet"
            else:
                self.quiet_from = self.ms + 20
                self.quiet_until = self.quiet_from + int(args[0])
                self.busy_until = self.ms + 10          # three copies, 5 ms apart
        elif name == "ping":
            if self.ms < self.quiet_until:
                error = "the cable is quiet"
            elif not args or not self.tile_named(args[0]):
                error = "no such tile"
            else:
                self.say("tile 0: 0 ms")
        elif name == "bootloader":
            ms = int(args[1]) if len(args) > 1 else 20000
            if not args or args[0] == "all":
                error = "usage: bootloader hub|<tile> [quiet ms]"
            elif self.ms < self.quiet_until:
                error = "the cable is quiet"
            elif not self.tile_named(args[0]):
                error = "no such tile"
            elif self.mode == "app" and self.answers:
                # the tile has it at once and restarts 250 ms later; the hub
                # tells everybody to be quiet and then says ok
                self.later(250, self.enter_rom if self.jump_works else self.start_tile)
                self.quiet_from = self.ms + 23
                self.quiet_until = self.quiet_from + ms
                self.busy_until = self.ms + 13
                self.later(13, lambda: self.say("ok"))
                return
            else:
                self.busy_until = self.ms + 500         # five tries
                self.later(500, lambda: (self.say("tile 0: no answer"),
                                         self.say("error: 1 failed")))
                return
        elif name == "fw":
            if len(args) != 1 or args[0] in ("all", "hub"):
                raise AssertionError("the imitation knows `fw <tile>` and no more: " + text)
            if self.ms < self.quiet_until:
                error = "the cable is quiet"
            elif not self.tile_named(args[0]):
                error = "no such tile"
            elif self.mode == "app" and self.answers and self.answers_fw:
                # a program of the old kind has no such command
                said = self.fw_line() if has_bootloader(self.flash) else None
                self.busy_until = self.ms + 3
                if said:
                    self.later(3, lambda: (self.say(said), self.say("ok")))
                else:
                    self.later(3, lambda: (self.say("tile 0: does not know that"),
                                           self.say("error: 1 failed")))
                return
            else:
                self.busy_until = self.ms + 500
                self.later(500, lambda: (self.say("tile 0: no answer"),
                                         self.say("error: 1 failed")))
                return
        elif name == "bridge":
            b = {"baud": 0, "parity": "e", "used": False,
                 "way": "swapped" if self.hub_swapped else "straight"}
            ok = bool(args) and args[0].isdigit() and 1200 <= int(args[0]) <= 1000000
            for a in args[1:]:
                if a in ("n", "e", "o"):
                    b["parity"] = a
                elif a in ("straight", "swapped"):
                    b["way"] = a
                else:
                    ok = False
            if not ok:
                error = "usage: bridge <baud> [n|e|o] [straight|swapped]"
            else:
                b["baud"] = int(args[0])
                self.bridge_soon = b
                self.later(2, self.bridge_on)
        else:
            error = "no such command (try help)"
        self.say("error: " + error if error else "ok")

    def fw_line(self):
        """core/console.c: show_fw()."""
        slot = bootloader_starts(self.flash)
        state, _, image = slot_of(self.flash, slot)
        other_state, _, other = slot_of(self.flash, 1 - slot)
        return "tile 0: runs %s %s %04x %08x, other %s %s" % (
            "ab"[slot], state, image.version, image.crc, other_state,
            "%08x" % other.crc if other else "-")

    def bridge_on(self):
        self.bridge = self.bridge_soon
        self.bridge_soon = None
        self.closed = False             # a close before now is not about the bridge

    # -- the tile -----------------------------------------------------------

    def start_tile(self):
        """Reset or power-up."""
        self.rom = None
        if self.boot0:
            self.enter_rom()
        elif program_in(self.flash) is not None:
            self.mode = "app"
            self.app_since = self.ms
            self.link_since = None
        elif has_bootloader(self.flash):
            self.enter_rom()            # boot/main.c: no program to start
            return
        else:
            self.mode = "dead"
        self.tile_changed()

    def enter_rom(self):
        self.mode = "rom"
        self.rom_baud = None
        self.rom_deaf = False
        self.rom = self.rom_loop()
        next(self.rom)
        self.tile_changed()

    def tile_changed(self):
        pass

    def reaches_rom(self):
        b = self.bridge
        return (b is not None and self.mode == "rom" and self.hub_port in "WS"
                and b["way"] == self.rom_hears and b["parity"] == "e")

    def to_cable(self, data):
        for byte in data:
            if not self.reaches_rom() or self.rom_deaf:
                continue
            if self.rom_baud is None:
                if byte == 0x7F:
                    self.rom_baud = self.bridge["baud"]
                    self.rom_say(ACK)
                else:
                    self.rom_deaf = True
            elif self.bridge["baud"] == self.rom_baud:
                self.rom.send(byte)

    def rom_say(self, *data, after=1):
        def deliver(data=bytes(data)):
            if self.reaches_rom() and self.bridge["baud"] == self.rom_baud:
                self.from_cable(data)
        self.later(after, deliver)

    # -- the ROM bootloader (PM0005, the device's side) ---------------------

    def rom_address(self):
        raw = []
        for _ in range(4):
            raw.append((yield))
        check = yield
        if flash_tile.xor(raw) != check:
            return None
        return struct.unpack(">I", bytes(raw))[0]

    def rom_loop(self):
        while True:
            code = yield
            inverse = yield
            if code ^ inverse != 0xFF:
                self.rom_say(NACK)
                continue
            self.commands.append(code)
            if code == 0x01:                            # Get Version
                self.rom_say(ACK, 0x10, 0x02, 0x07, ACK)
            elif code == 0x02:                          # Get Device ID
                self.rom_say(ACK, len(self.chip_id) - 1, *self.chip_id, ACK)
            elif code == 0x11:                          # Read Memory
                if self.protected:
                    self.rom_say(NACK)
                    continue
                self.rom_say(ACK)
                address = yield from self.rom_address()
                if address is None or not FLASH_BASE <= address < FLASH_BASE + FLASH_SIZE:
                    self.rom_say(NACK)
                    continue
                self.rom_say(ACK)
                n = yield
                check = yield
                if n ^ check != 0xFF:
                    self.rom_say(NACK)
                    continue
                at = address - FLASH_BASE
                self.reads += 1
                self.rom_say(ACK, *self.flash[at:at + n + 1])
            elif code == 0x31:                          # Write Memory
                if self.protected:
                    self.rom_say(NACK)
                    continue
                self.rom_say(ACK)
                address = yield from self.rom_address()
                if address is None or not FLASH_BASE <= address < FLASH_BASE + FLASH_SIZE:
                    self.rom_say(NACK)
                    continue
                self.rom_say(ACK)
                n = yield
                data = []
                for _ in range(n + 1):
                    data.append((yield))
                check = yield
                if flash_tile.xor([n] + data) != check:
                    self.rom_say(NACK)
                    continue
                at = address - FLASH_BASE
                for i, byte in enumerate(data):
                    self.flash[at + i] &= byte          # flash only loses bits
                    if self.corrupt == at + i:
                        self.flash[at + i] ^= 0x10
                self.writes.append((at, len(data)))
                if self.dies_after_writes is not None and len(self.writes) >= self.dies_after_writes:
                    self.rom_deaf = True
                    continue
                self.rom_say(ACK, after=11)
            elif code == 0x44:                          # Erase
                if self.protected:
                    self.rom_say(NACK)
                    continue
                self.rom_say(ACK)
                high = yield
                low = yield
                n = high << 8 | low
                if n >= 0xFFF0:                         # everything, or a bank
                    check = yield
                    if check != high ^ low:
                        self.rom_say(NACK)
                        continue
                    self.mass_erased = True
                    self.flash[:] = b"\xff" * FLASH_SIZE
                    self.rom_say(ACK, after=10)
                    continue
                raw = [high, low]
                for _ in range(2 * (n + 1)):
                    raw.append((yield))
                check = yield
                sectors = [raw[i] << 8 | raw[i + 1] for i in range(2, len(raw), 2)]
                if flash_tile.xor(raw) != check or any(s >= FLASH_SIZE // SECTOR for s in sectors):
                    self.rom_say(NACK)
                    continue
                for s in sectors:
                    if s not in self.erase_fails:
                        self.flash[s * SECTOR:(s + 1) * SECTOR] = b"\xff" * SECTOR
                    self.erased.append(s)
                self.rom_say(ACK, after=16 * len(sectors))
            elif code == 0x21:                          # Jump
                if self.protected:
                    self.rom_say(NACK)
                    continue
                self.rom_say(ACK)
                address = yield from self.rom_address()
                if address != FLASH_BASE:
                    self.rom_say(NACK)
                    continue
                self.rom_say(ACK)
                self.later(2, self.jump_to_program)
            elif code == 0xD4 and self.knows_reset:     # Reset Device
                self.rom_say(ACK, ACK)
                self.later(2, self.start_tile)
            else:
                self.rom_say(NACK)

    def jump_to_program(self):
        boot0, self.boot0 = self.boot0, False           # a jump does not look at BOOT0
        self.start_tile()
        self.boot0 = boot0


class SimWorld(World):
    """The same, with the hub's own program in it. tests/hubsim.c runs the
    hub and one tile of core/ in the simulator; this is the pipe to it.

    The tile's chip stays here: its flash and its ROM. The tile's program
    in the simulator is core/'s whatever that flash holds, so the firmware
    version the hub reports is always the same one. A flash with a
    bootloader in it is handed to the simulator's tile at every start:
    which slot runs, and what the tile says of its slots, is then core/'s
    doing."""

    warm_up = 3000              # ms the hub has been on when the script starts

    def __init__(self):
        World.__init__(self)
        self.proc = subprocess.Popen([HUBSIM], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True)
        hello = self.proc.stdout.readline().split()
        if len(hello) != 3 or hello[0] != "hubsim":
            raise AssertionError("hubsim began with %r" % hello)
        self.tile_id = int(hello[1], 16)
        self.firmware = hello[2]
        self.begun = False
        self.word = "-"                 # the bridge, as hubsim last told it
        self.frames = 0                 # frames the hub has sent on its cable
        self.cable_out = bytearray()    # from the tile's chip, not sent to hubsim yet

    def pipe(self, line):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            answer = self.proc.stdout.readline()
        except OSError:
            answer = ""
        if not answer:
            raise AssertionError("hubsim stopped at %r (exit code %s)" % (line, self.proc.wait()))
        return answer.rstrip("\n")

    def close(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        code = self.proc.wait()
        if code:
            raise AssertionError("hubsim ended with exit code %d" % code)

    def begin(self):
        """The first millisecond: everything a test arranged is known now."""
        if self.begun:
            return
        self.begun = True
        self.pipe("w " + self.hub_port)
        if self.mode != "app" or has_bootloader(self.flash):
            self.tile_changed()
        self.idle(self.warm_up)

    def idle(self, ms):
        """Time passes with nobody at the serial port; what the hub said."""
        for _ in range(ms):
            self.tick()
        said = bytes(self.to_pc)
        self.to_pc.clear()
        return said

    def hub_step(self):
        self.begin()
        if self.typed:
            data = bytes(self.typed)
            self.typed.clear()
            if self.word == "-":
                self.note_typed(data)
            self.pipe("t " + data.hex())
        if self.cable_out:
            self.pipe("c " + self.cable_out.hex())
            self.cable_out.clear()
        frames, tile, word, pc, cable, _hub, _ran = self.pipe("s").split()

        self.word = word
        if "," in word:
            if self.bridge is None:
                baud, parity, way = word.split(",")
                self.bridge = {"baud": int(baud), "parity": parity, "way": way}
        else:
            self.bridge = None

        # The hub's own talk, at 1 Mbaud, is the first thing a ROM hears
        # that has not measured its speed yet.
        if int(frames) != self.frames:
            self.frames = int(frames)
            if self.mode == "rom" and self.rom_baud is None and self.hub_port in "WS":
                self.rom_deaf = True

        if tile == "rom":               # the tile's program obeyed BOOTLOADER
            if self.jump_works:
                self.enter_rom()
            else:
                self.start_tile()
        if pc != "-":
            self.to_pc += bytes.fromhex(pc)
        if cable != "-":
            self.to_cable(bytes.fromhex(cable))

    def note_typed(self, data):
        """Keep the lines the script types, as the console puts them together."""
        for c in data:
            if c in (13, 10):
                if self.line:
                    text = self.line.decode()
                    self.line.clear()
                    self.typed_lines.append(text)
                    if text.startswith("bootloader") and not self.answers:
                        self.tile_is_dead()     # a moment before it is asked
            elif c in (8, 127):
                del self.line[-1:]
            elif 0x20 <= c < 0x7F:
                self.line.append(c)

    def bridging(self):
        return self.word != "-"

    def computer_dtr(self, on):
        self.begin()
        self.pipe("d %d" % on)

    def from_cable(self, data):
        self.cable_out += data

    def tile_changed(self):
        # hubsim's tile runs core/'s program, or is off: in its ROM, or with
        # nothing in its flash, a tile says nothing either
        if self.begun:
            self.pipe("p 0")
            if self.mode == "app":
                self.load_chip()
                self.pipe("p 1")

    def load_chip(self):
        """Give hubsim's tile this flash, if a bootloader is in it."""
        if not has_bootloader(self.flash):
            return
        self.pipe("f 0 -")
        for at in range(0, RECORD, 4096):
            piece = bytes(self.flash[at:min(at + 4096, RECORD)])
            if piece.strip(b"\xff"):
                self.pipe("f 0 %x %s" % (at, piece.hex()))

    def check_model(self):
        """core/fw.c in hubsim's tile and this file's imitation of it have
        to agree on what the flash holds and which slot runs."""
        if self.mode != "app" or not has_bootloader(self.flash):
            return
        self.run(0.05)                  # a start takes 20 ms
        slot = bootloader_starts(self.flash)
        want = ["ab"[slot], "0", "run"]
        for i in (0, 1):
            state, _, image = slot_of(self.flash, i)
            want.append(state)
            want += ["%04x" % image.version, "%08x" % image.crc] if image else ["-", "-"]
        got = self.pipe("q 0").split()[:len(want)]
        if got != want:
            raise AssertionError("hubsim's tile: %s; the imitation: %s"
                                 % (" ".join(got), " ".join(want)))

    def just_plugged(self):
        self.warm_up = 0

    def tile_has_been_up(self, seconds):
        self.warm_up = seconds * 1000

    def start_quiet(self, ms):
        self.begin()
        self.pipe("t " + (b"quiet %d\r" % ms).hex())
        said = self.idle(30)
        if said != b"ok\r\n":
            raise AssertionError("quiet: the hub said %r" % said)

    def leave_bridge_on(self):
        self.begin()
        self.pipe("d 1")
        self.pipe("t " + b"bridge 115200 e straight\r".hex())
        self.idle(200)
        self.pipe("d 0")
        self.idle(5)
        if self.bridge is None:
            raise AssertionError("the bridge did not stay on")

    def type_ahead(self, data):
        self.begin()
        self.typed += data
        self.idle(5)

    def reported(self, version):
        return self.firmware


class FakePort:
    """The hub's serial port, as flash_tile wants a port to be."""

    def __init__(self, world):
        self.world = world
        self.is_open = True
        self.set_dtr(True)              # as opening a serial port does

    def write(self, data):
        self.world.typed += data

    def read(self, wait):
        w = self.world
        if (w.interrupt_after_writes is not None
                and len(w.writes) >= w.interrupt_after_writes):
            w.interrupt_after_writes = None
            raise KeyboardInterrupt
        if not w.to_pc and wait > 0:
            w.run(wait, until_output=True)
        data = bytes(w.to_pc[:7])                       # in pieces, as USB may
        del w.to_pc[:7]
        return data

    def set_dtr(self, on):
        self.world.computer_dtr(on)

    def close(self):
        self.set_dtr(False)
        self.is_open = False


def make_crc(version, slot):
    """The checksum of make_slot_image(version, slot)."""
    return mkimage.parse_image(make_slot_image(version, slot)).crc


def nobody(text):
    raise AssertionError("the script asked a question: " + text)


class Scenarios:
    """What the script is put through: by ImitationTest and by RealHubTest."""

    def new_world(self):
        raise NotImplementedError

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.world = self.new_world()
        self.addCleanup(self.world.close)
        self.tile = "%08x" % self.world.tile_id
        self.image = make_image(2)
        self.out = ""
        self.port = None

    def file(self, data, name="tile.bin"):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def run_script(self, *args, image=None, prompt=nobody):
        """main() against the world; the exit code. What it printed is in self.out."""
        w = self.world
        out = io.StringIO()
        self.port = FakePort(w)
        path = self.file(self.image if image is None else image)
        with mock.patch.object(flash_tile, "now", w.now), \
                mock.patch.object(flash_tile, "sleep", w.sleep):
            code = flash_tile.main([path, "--port", "fake"] + list(args),
                                   open_port=lambda name: self.port, stream=out, prompt=prompt)
        self.out = out.getvalue()
        return code

    def typed(self, start):
        return [t for t in self.world.typed_lines if t.startswith(start)]

    def check_left_tidy(self):
        """Whatever happened: the bridge is over, the port is closed, the
        console answers, and the tile's settings are as they were."""
        w = self.world
        w.run(0.02)
        self.assertFalse(w.bridging(), self.out)
        self.assertFalse(self.port.is_open)
        w.to_pc.clear()
        w.typed += b"version\r"
        w.run(0.02)
        self.assertIn(b"piano hub, firmware", bytes(w.to_pc), self.out)
        w.to_pc.clear()
        self.assertEqual(bytes(w.flash[RECORD:]), w.settings)
        self.assertFalse(w.mass_erased)
        self.assertNotIn(RECORD // SECTOR, w.erased)
        for at, n in w.writes:
            self.assertLessEqual(at + n, RECORD)
        w.check_model()

    def check_flashed(self, image=None):
        w = self.world
        image = self.image if image is None else image
        padded = image + b"\xff" * (-len(image) % 4)
        self.assertEqual(bytes(w.flash[:len(padded)]), padded)
        self.assertEqual(w.erased, list(range(-(-len(image) // SECTOR))))
        self.check_left_tidy()

    # -- the way it is meant to go ------------------------------------------

    def test_flashes_the_tile(self):
        code = self.run_script()
        self.assertEqual(code, 0, self.out)
        self.check_flashed()
        self.assertEqual(self.typed("bootloader"), ["bootloader %s 10000" % self.tile])
        self.assertEqual(self.typed("bridge"), ["bridge 115200 e straight"])
        self.assertIn("tile %s is on the hub's cable, by its W port; firmware 0001" % self.tile,
                      self.out)
        self.assertIn("chip: AT32F405RCT7 (0x70053240)", self.out)
        self.assertIn("tile %s is back, firmware %s" % (self.tile, self.world.reported(2)),
                      self.out)
        self.assertEqual(self.world.mode, "app")
        # Get Version, Get Device ID, one read to see that reading is
        # allowed, then erase, write, read back, reset: nothing else
        blocks = -(-len(self.image) // 256)
        self.assertEqual(self.world.commands,
                         [0x01, 0x02, 0x11] + [0x44] * 3 + [0x31] * blocks + [0x11] * blocks + [0xD4])
        # a program of the old kind: said so, and not asked what it runs
        self.assertIn("not a factory file", self.out)
        self.assertEqual(self.typed("fw"), [])

    def test_tile_that_has_been_up_for_long(self):
        self.world.tile_has_been_up(40)
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertIn("is back, firmware " + self.world.reported(2), self.out)

    def test_on_the_s_port(self):
        self.world.hub_port = "S"
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertIn("by its S port", self.out)

    def test_rom_hears_only_the_swapped_bridge(self):
        self.world.rom_hears = "swapped"
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.typed("bridge"),
                         ["bridge 115200 e straight", "bridge 115200 e swapped"])
        self.assertIn("bridge swapped", self.out)

    def test_orientation_can_be_told(self):
        self.world.rom_hears = "swapped"
        self.assertEqual(self.run_script("--orientation", "swapped"), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.typed("bridge"), ["bridge 115200 e swapped"])

    def test_other_speed(self):
        self.assertEqual(self.run_script("--baud", "57600"), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.typed("bridge"), ["bridge 57600 e straight"])

    def test_odd_size_is_padded(self):
        self.image = make_image(2, 4099)
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.world.writes[-1], (4096, 4))

    def test_biggest_program_stops_before_the_settings(self):
        self.image = make_image(3, RECORD)
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.world.erased, list(range(127)))
        self.assertIn("is back, firmware " + self.world.reported(3), self.out)
        # the quiet was made long enough for all of it
        quiet = int(self.typed("bootloader")[0].split()[2])
        self.assertGreater(quiet, 60000)

    def test_no_verify(self):
        self.assertEqual(self.run_script("--no-verify"), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.world.reads, 1)           # the look at protection
        self.assertIn("not read back", self.out)

    def test_check_changes_nothing(self):
        before = bytes(self.world.flash)
        self.assertEqual(self.run_script("--check"), 0, self.out)
        self.assertEqual(bytes(self.world.flash), before)
        self.assertEqual(self.world.mode, "app")
        self.assertEqual(sorted(set(t.split()[0] for t in self.world.typed_lines)),
                         ["grid", "info", "ping", "version"])
        self.assertIn("nothing was changed", self.out)
        self.check_left_tidy()

    def test_what_the_hub_remembers_is_not_the_new_program(self):
        # With a quiet this short the hub never forgets the tile, and still
        # shows the old firmware's INFO when the tile is first heard again.
        self.assertEqual(self.run_script("--quiet-ms", "1000"), 0, self.out)
        self.check_flashed()
        self.assertIn("is back, firmware " + self.world.reported(2), self.out)

    def test_jump_instead_of_reset(self):
        self.assertEqual(self.run_script("--go", "jump"), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.world.commands[-1], 0x21)
        self.assertIn("is back, firmware " + self.world.reported(2), self.out)

    def test_rom_without_reset_device(self):
        self.world.knows_reset = False
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.world.commands[-2:], [0xD4, 0x21])
        self.assertIn("is back, firmware " + self.world.reported(2), self.out)

    def test_stay_in_the_rom(self):
        self.assertEqual(self.run_script("--go", "stay"), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.world.mode, "rom")

    def test_a_bridge_left_on(self):
        self.world.leave_bridge_on()
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()

    def test_half_a_line_left_on_the_console(self):
        # If the script's first Enter ran it, the cables would be quiet and
        # `bootloader` refused.
        self.world.type_ahead(b"quiet 60000")
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertEqual(self.typed("quiet"), [])

    def test_waits_for_an_old_quiet(self):
        self.world.start_quiet(20000)
        self.assertEqual(self.run_script(), 0, self.out)
        self.assertIn("quiet from an earlier command", self.out)
        self.check_flashed()

    def test_waits_for_an_old_quiet_that_made_the_hub_forget_the_tile(self):
        self.world.start_quiet(20000)
        self.world.run(6)
        self.assertEqual(self.run_script(), 0, self.out)
        self.assertIn("quiet from an earlier command", self.out)
        self.check_flashed()

    def test_hub_only_just_plugged_in(self):
        self.world.just_plugged()
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()

    # -- a factory file: bootloader, program, and the rest of the chip blank --

    def updated_tile(self):
        """The tile got version 3 through the hub some time ago, into slot
        B, and runs it."""
        w = self.world
        flash = bytearray(make_factory(1))
        put_trailer(flash, 0, "good", 1)
        new = make_slot_image(3, slot=1)
        flash[slot_at(1):slot_at(1) + len(new)] = new
        put_trailer(flash, 1, "good", 2)
        w.flash_holds(flash)
        w.run(0.1)
        w.check_model()
        self.assertEqual(program_in(w.flash), 3)

    def test_factory_file(self):
        w = self.world
        self.image = make_factory(2)
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertEqual(w.erased, list(range(126)))    # all but the free sector and the settings
        # the bootloader is 9 blocks and the program 20; nothing blank is written
        self.assertEqual(len(w.writes), 29)
        for at, n in w.writes:
            self.assertTrue(self.image[at:at + n].strip(b"\xff"), at)
        # read back: the 5 sectors something was written to, 8 blocks each,
        # and both ends of the other 121
        self.assertEqual(w.commands, [0x01, 0x02, 0x11] + [0x44] * 126 + [0x31] * 29
                         + [0x11] * (40 + 242) + [0xD4])
        self.assertIn("a factory file: the bootloader, and in slot A the tile's program, "
                      "version 0002", self.out)
        self.assertIn("is back, firmware " + w.reported(2), self.out)
        self.assertIn("it runs what was flashed: slot A, version 0002, crc %08x"
                      % mkimage.parse_factory(self.image).crc, self.out)
        self.assertEqual(self.typed("fw"), ["fw " + self.tile])
        self.assertEqual(w.mode, "app")

    def test_factory_file_over_a_tile_that_was_updated(self):
        # Slot B holds a newer program than the file's, with a trailer that
        # says it is good. All of it has to go, or it would run again.
        w = self.world
        self.updated_tile()
        self.image = make_factory(2)
        self.assertEqual(self.run_script(), 0, self.out)
        self.assertIn("on the hub's cable, by its W port; firmware " + w.reported(3), self.out)
        self.check_flashed()
        self.assertEqual(bytes(w.flash[slot_at(1):RECORD]), b"\xff" * (RECORD - slot_at(1)))
        self.assertEqual(program_in(w.flash), 2)
        self.assertIn("it runs what was flashed: slot A, version 0002", self.out)

    def test_factory_file_again(self):
        self.world.flash_holds(make_factory(2))
        self.image = make_factory(2)
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertIn("it runs what was flashed: slot A", self.out)

    def test_a_sector_that_was_not_erased_shows(self):
        # the second sector of the program left in slot B: nothing is
        # written there, and the read back still finds it
        self.updated_tile()
        self.world.erase_fails = {66}
        self.image = make_factory(2)
        self.assertEqual(self.run_script(), 1)
        self.assertIn("is not what was written: at 0x08021000 it reads 0x03, the program "
                      "has 0xff", self.out)
        self.assertIn("no complete program", self.out)
        self.check_left_tidy()

    def test_a_trailer_that_was_not_erased_shows(self):
        self.updated_tile()
        self.world.erase_fails = {125}
        self.image = make_factory(2)
        self.assertEqual(self.run_script(), 1)
        self.assertIn("is not what was written: at 0x0803eff0 it reads 0x50, the program "
                      "has 0xff", self.out)
        self.check_left_tidy()

    def test_the_tile_says_if_it_runs_something_else(self):
        # Nothing of slot B is erased and nothing is read back: the tile
        # comes back with the program that was left there, and says so.
        w = self.world
        self.updated_tile()
        w.erase_fails = set(range(65, 126))
        self.image = make_factory(2)
        self.assertEqual(self.run_script("--no-verify"), 1)
        self.assertIn("is back, firmware " + w.reported(3), self.out)
        self.assertIn("it runs another program than the one just flashed", self.out)
        self.assertIn("the one in slot B, version 0003, crc %08x" % make_crc(3, 1), self.out)
        self.assertEqual(program_in(w.flash), 3)
        self.check_left_tidy()

    def test_factory_file_check_changes_nothing(self):
        before = bytes(self.world.flash)
        self.image = make_factory(2)
        self.assertEqual(self.run_script("--check"), 0, self.out)
        self.assertEqual(bytes(self.world.flash), before)
        self.assertIn("a factory file", self.out)
        self.assertIn("nothing was changed", self.out)
        self.assertEqual(self.typed("fw"), [])
        self.check_left_tidy()

    def test_blank_tile_gets_the_factory_file(self):
        self.world.make_blank()
        self.image = make_factory(2)
        asked = []

        def user(text):
            asked.append(text)
            if len(asked) == 2:
                self.world.run(8)               # finding the pads
                self.reset_into_rom()

        self.assertEqual(self.run_script("--rom", prompt=user), 0, self.out)
        self.assertEqual(self.typed("quiet"), ["quiet 30000"])
        self.check_flashed()
        self.assertIn("it runs what was flashed: slot A, version 0002", self.out)

    def test_bootloader_without_a_program_goes_to_the_rom(self):
        # The flashing stops when the bootloader is in and the program is
        # not: restarted, such a tile is in its ROM again, and the second
        # run needs nobody to hold a pad.
        w = self.world
        self.image = make_factory(2)
        w.dies_after_writes = 12
        self.assertEqual(self.run_script(), 1)
        self.assertIn("no complete program", self.out)
        self.check_left_tidy()
        self.assertTrue(has_bootloader(w.flash))
        self.assertIsNone(program_in(w.flash))

        w.dies_after_writes = None
        w.erased = []
        w.run(25)                               # the first run's quiet runs out
        asked = []

        def user(text):
            asked.append(text)
            if len(asked) == 2:
                w.start_tile()                  # power off and on, no pad

        self.assertEqual(self.run_script("--rom", prompt=user), 0, self.out)
        self.check_flashed()
        self.assertIn("it runs what was flashed: slot A, version 0002", self.out)

    def test_the_real_factory_file(self):
        path = os.path.join(HERE, "..", "build", "tile", "tile_factory.bin")
        if not os.path.exists(path):
            self.skipTest("build/tile/tile_factory.bin is not built (`make tile`)")
        with open(path, "rb") as f:
            self.image = f.read()
        program = mkimage.parse_factory(self.image)
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertIn("it runs what was flashed: slot A, version %04x, crc %08x"
                      % (program.version, program.crc), self.out)
        # the quiet was long enough for all of it
        quiet = int(self.typed("bootloader")[0].split()[2])
        self.assertLess(quiet, 60000)
        self.assertNotIn("the cables stay quiet for another 0 s", self.out)

    # -- stopping before anything is changed --------------------------------

    def check_untouched(self):
        w = self.world
        self.assertEqual(self.typed("bootloader"), [])
        self.assertEqual(self.typed("bridge"), [])
        self.assertEqual(self.typed("quiet"), [])
        self.assertEqual(w.commands, [])
        self.assertEqual(program_in(w.flash), 1)
        self.check_left_tidy()

    def test_hub_on_the_e_port(self):
        self.world.hub_port = "E"
        self.assertEqual(self.run_script(), 1)
        self.assertIn("in tile %s's E port" % self.tile, self.out)
        self.assertIn("W or S", self.out)
        self.check_untouched()

    def test_hub_on_the_n_port_with_rom(self):
        self.world.hub_port = "N"
        self.assertEqual(self.run_script("--rom"), 1)
        self.assertIn("N port", self.out)
        self.check_untouched()

    def test_no_tile(self):
        self.world.tile_is_dead()
        self.assertEqual(self.run_script(), 1)
        self.assertIn("sees no tile", self.out)
        self.assertIn("--rom", self.out)
        self.check_untouched()

    def test_tile_does_not_take_the_command(self):
        self.world.answers = False
        self.assertEqual(self.run_script(), 1)
        self.assertIn("could not send the tile into its ROM bootloader", self.out)
        self.assertIn("no answer", self.out)
        self.assertEqual(self.typed("bridge"), [])
        self.assertEqual(self.world.commands, [])
        self.check_left_tidy()

    # -- the ROM does not answer --------------------------------------------

    def test_tile_never_reaches_its_rom(self):
        self.world.jump_works = False           # it restarts into its program
        self.assertEqual(self.run_script(), 1)
        self.assertIn("ROM bootloader does not answer, with the bridge straight or swapped", self.out)
        self.assertEqual(len(self.typed("bridge")), 2)
        self.assertEqual(program_in(self.world.flash), 1)
        self.assertEqual(self.world.erased, [])
        self.check_left_tidy()

    def test_rom_out_of_reach_on_the_e_port(self):
        self.world.hub_port = "E"
        self.assertEqual(self.run_script("--any-port"), 1)
        self.assertIn("does not answer", self.out)
        self.assertIn("may be in its ROM bootloader now", self.out)
        self.assertEqual(self.world.mode, "rom")
        self.assertEqual(self.world.commands, [])
        self.check_left_tidy()

    # -- the ROM answers, and something is wrong ----------------------------

    def test_another_chip(self):
        self.world.chip_id = bytes([0x21, 0xC0, 0x70, 0x04, 0x13])
        self.assertEqual(self.run_script(), 1)
        self.assertIn("not the chip", self.out)
        self.assertIn("21c0700413", self.out)
        self.assertIn("was not changed; the tile was restarted", self.out)
        self.assertEqual(self.world.erased, [])
        self.assertEqual(self.world.writes, [])
        self.assertEqual(self.world.mode, "app")
        self.assertEqual(program_in(self.world.flash), 1)
        self.check_left_tidy()

    def test_another_chip_all_the_same(self):
        self.world.chip_id = bytes([0x21, 0xC0, 0x70, 0x04, 0x13])
        self.assertEqual(self.run_script("--any-chip"), 0, self.out)
        self.check_flashed()

    def test_chip_id_in_plain_order(self):
        self.world.chip_id = bytes([0x70, 0x05, 0x32, 0x42, 0x13])
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertIn("AT32F405RCT7-7", self.out)
        self.assertIn("another order", self.out)

    def test_access_protection(self):
        self.world.protected = True
        self.assertEqual(self.run_script(), 1)
        self.assertIn("access protection is on", self.out)
        self.assertEqual(self.world.erased, [])
        self.assertEqual(self.world.mode, "app")        # restarted, as it was
        self.assertEqual(program_in(self.world.flash), 1)
        self.check_left_tidy()

    def test_read_back_differs_and_the_second_run_mends_it(self):
        self.world.corrupt = 700
        self.assertEqual(self.run_script(), 1)
        self.assertIn("is not what was written: at 0x080002bc", self.out)
        self.assertIn("no complete program", self.out)
        self.assertEqual(self.world.mode, "rom")
        self.check_left_tidy()

        # The tile is still in its ROM, which has its speed already: --rom,
        # and Enter twice without touching the tile.
        self.world.corrupt = None
        self.world.erased = []
        self.world.run(15)                      # the first run's quiet runs out
        asked = []
        self.assertEqual(self.run_script("--rom", prompt=asked.append), 0, self.out)
        self.assertEqual(len(asked), 2)
        self.check_flashed()
        self.assertIn("is back, firmware " + self.world.reported(2), self.out)

    def test_rom_stops_in_the_middle(self):
        self.world.dies_after_writes = 5
        self.assertEqual(self.run_script(), 1)
        self.assertIn("stopped answering (writing 256 bytes at 0x08000400)", self.out)
        self.assertIn("no complete program", self.out)
        self.check_left_tidy()

    def test_ctrl_c_in_the_middle(self):
        self.world.interrupt_after_writes = 5
        self.assertEqual(self.run_script(), 130)
        self.assertIn("stopped", self.out)
        self.assertIn("no complete program", self.out)
        self.assertEqual(len(self.world.writes), 5)
        self.assertEqual(self.world.mode, "rom")
        self.check_left_tidy()

    def test_boot0_still_held(self):
        asked = []

        def user(text):
            asked.append(text)
            if len(asked) == 2:
                self.world.boot0 = True         # and never let go of
                self.world.start_tile()

        self.assertEqual(self.run_script("--rom", prompt=user), 1)
        self.assertIn("was written and read back, but the hub does not see the tile", self.out)
        self.assertIn("BOOT0", self.out)
        self.check_flashed()

    # -- by hand ------------------------------------------------------------

    def reset_into_rom(self):
        w = self.world
        w.boot0 = True
        w.start_tile()
        w.boot0 = False

    def test_blank_tile_by_hand(self):
        self.world.make_blank()
        asked = []

        def user(text):
            asked.append(text)
            if len(asked) == 2:
                self.world.run(8)               # finding the pads
                self.reset_into_rom()

        self.assertEqual(self.run_script("--rom", prompt=user), 0, self.out)
        self.assertEqual(len(asked), 2)
        self.assertEqual(self.typed("quiet"), ["quiet 30000"])
        self.assertEqual(self.typed("bootloader"), [])
        self.check_flashed()
        self.assertIn("sees no tile", self.out)
        self.assertIn("tile %s is back, firmware %s" % (self.tile, self.world.reported(2)),
                      self.out)

    def test_restart_before_the_quiet_is_no_good(self):
        # What the order in --rom is for: a ROM that starts while the hub
        # still talks measures its speed from the hub's talk.
        self.world.make_blank()
        asked = []

        def user(text):
            asked.append(text)
            if len(asked) == 1:
                self.reset_into_rom()
                self.world.run(1)

        self.assertEqual(self.run_script("--rom", prompt=user), 1)
        self.assertIn("does not answer", self.out)
        self.assertTrue(self.world.rom_deaf)
        self.check_left_tidy()

    def test_too_slow_by_hand(self):
        self.world.make_blank()
        asked = []

        def user(text):
            asked.append(text)
            if len(asked) == 2:
                self.world.run(28)              # nearly the whole quiet
            if len(asked) == 3:
                self.reset_into_rom()

        self.assertEqual(self.run_script("--rom", prompt=user), 0, self.out)
        self.assertEqual(len(asked), 3)
        self.assertEqual(len(self.typed("quiet")), 2)
        self.assertIn("took too long", self.out)
        self.check_flashed()

    def test_by_hand_waits_for_an_old_quiet(self):
        self.world.make_blank()
        self.world.start_quiet(5000)
        asked = []

        def user(text):
            asked.append(text)
            if len(asked) == 2:
                self.reset_into_rom()

        self.assertEqual(self.run_script("--rom", prompt=user), 0, self.out)
        self.assertIn("quiet from an earlier command", self.out)
        self.check_flashed()

    def test_by_hand_with_nobody_there(self):
        def gone(text):
            raise EOFError

        self.world.make_blank()
        self.assertEqual(self.run_script("--rom", prompt=gone), 1)
        self.assertIn("somebody at the keyboard", self.out)
        self.check_left_tidy()


class ImitationTest(Scenarios, unittest.TestCase):
    """The scenarios against the imitation hub, and what only it can do."""

    def new_world(self):
        return World()

    def test_chatter_on_the_console(self):
        self.world.chatter = True
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()

    def test_cable_stays_quiet(self):
        # a hub that says so for longer than a quiet can last
        self.world.start_quiet(200000)
        self.assertEqual(self.run_script(), 1)
        self.assertIn("the cables stay quiet", self.out)
        self.check_untouched()

    def test_tile_does_not_say_what_it_runs(self):
        # not the flashing's fault, and not a reason to call it failed
        self.world.answers_fw = False
        self.image = make_factory(2)
        self.assertEqual(self.run_script(), 0, self.out)
        self.check_flashed()
        self.assertIn("which program the tile runs could not be asked: tile 0: no answer",
                      self.out)

    def test_not_the_hub(self):
        self.world.command = lambda text: self.world.say("error: no such command")
        self.assertEqual(self.run_script(), 1)
        self.assertIn("does not answer like the hub's console", self.out)


class RealHubTest(Scenarios, unittest.TestCase):
    """The scenarios against the hub's own program (tests/hubsim.c)."""

    def new_world(self):
        if not os.path.exists(HUBSIM):
            self.skipTest("build/host/hubsim is not built (`make test-tools` builds it)")
        return SimWorld()


class PiecesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def file(self, data, name="tile.bin"):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_files_that_are_not_a_tile_program(self):
        def opened(name):
            raise AssertionError("the port was opened")

        good = make_image(2)
        factory = make_factory(2)
        cases = {
            "too big": (make_image(2, RECORD + 4), "room for"),
            "elf": (b"\x7fELF" + good, "ELF"),
            "hex": (b":020000040800F2\n", "Intel HEX"),
            "short": (b"\x00\x98", "too short"),
            "stack": (struct.pack("<I", 0x08001000) + good[4:], "stack"),
            "start": (good[:4] + struct.pack("<I", 0x08000100) + good[8:], "start address"),
            "far start": (good[:4] + struct.pack("<I", 0x08010001) + good[8:], "start address"),
            "hub": (good + b"piano hub, firmware %04x", "the hub's program"),
            "image": (make_slot_image(2), "tools/update.py"),
            "image for slot b": (make_slot_image(2, slot=1), "tools/update.py"),
            "linked for a slot": (make_slot_program(2), "tools/update.py"),
            "hub's factory file": (mkimage.make_factory(make_boot(), make_slot_image(2, kind=1)),
                                   "the hub's factory file"),
            "broken": (factory[:0x3000] + b"\x00" + factory[0x3001:], "not a whole one"),
            "more than a factory file": (factory[:slot_at(1)] + make_slot_image(3, slot=1)
                                         + factory[slot_at(1) + 5000:], "not a whole one"),
        }
        for name, (data, words) in cases.items():
            out = io.StringIO()
            code = flash_tile.main([self.file(data), "--port", "fake"], open_port=opened,
                                   stream=out, prompt=nobody)
            self.assertEqual(code, 1, name)
            self.assertIn(words, out.getvalue(), name)
        out = io.StringIO()
        missing = os.path.join(self.dir.name, "nothing.bin")
        self.assertEqual(flash_tile.main([missing, "--port", "fake"], open_port=opened,
                                         stream=out, prompt=nobody), 1)
        self.assertIn("cannot read", out.getvalue())

    def test_xor(self):
        self.assertEqual(flash_tile.xor(b""), 0)
        self.assertEqual(flash_tile.xor(bytes([0x08, 0x00, 0x00, 0x00])), 0x08)
        self.assertEqual(flash_tile.xor(bytes([0x00, 0x00, 0x00, 0x7E])), 0x7E)

    def test_which_chip(self):
        self.assertEqual(flash_tile.which_chip(bytes([0x32, 0x40, 0x70, 0x05, 0x13]))[:2],
                         (0x70053240, "AT32F405RCT7"))
        self.assertEqual(flash_tile.which_chip(bytes([0x32, 0x42, 0x70, 0x05, 0x13]))[:2],
                         (0x70053242, "AT32F405RCT7-7"))
        self.assertEqual(flash_tile.which_chip(bytes([0x40, 0x32, 0x05, 0x70, 0x13]))[1],
                         "AT32F405RCT7")
        self.assertIsNone(flash_tile.which_chip(bytes([0x21, 0xC0, 0x70, 0x04, 0x13]))[1])
        self.assertIsNone(flash_tile.which_chip(b"\x04")[1])

    def test_the_rom_object_keeps_off_the_settings(self):
        rom = flash_tile.Rom(None, 115200, None)
        with self.assertRaises(AssertionError):
            rom.erase(127)
        with self.assertRaises(AssertionError):
            rom.erase(0xFFFF)
        with self.assertRaises(AssertionError):
            rom.write(0x0803F7FC, b"\x00" * 8)
        with self.assertRaises(AssertionError):
            rom.write(0x0803F800, b"\x00" * 4)
        with self.assertRaises(AssertionError):
            rom.write(0x1FFFA400, b"\x00" * 4)
        with self.assertRaises(AssertionError):
            rom.write(0x08000000, b"\x00" * 257)

    def test_what_the_tile_says_it_runs(self):
        program = mkimage.parse_factory(make_factory(2))
        crc = "%08x" % program.crc

        def check(*lines):
            hub, out = mock.Mock(), mock.Mock()
            hub.ask.return_value = list(lines)
            flash_tile.check_runs(hub, mock.Mock(id="1234abcd"), program, out)
            hub.ask.assert_called_once_with("fw 1234abcd", 4.0)
            return out.say.call_args[0][0]

        self.assertIn("it runs what was flashed: slot A, version 0002, crc " + crc,
                      check("tile 0: runs a none 0002 %s, other none -" % crc))
        self.assertIn("it runs what was flashed",
                      check("tile 0: runs a good 0002 %s, other new 00000001, staged" % crc))
        for line in ("tile 0: runs b good 0002 %s, other none -" % crc,
                     "tile 0: runs a none 0002 00000001, other none -"):
            with self.assertRaises(flash_tile.Failure) as c:
                check(line)
            self.assertIn("it runs another program", str(c.exception))
        self.assertIn("could not be asked", check())
        self.assertIn("could not be asked", check("tile 0: runs c none 0002 %s, other none -" % crc))

    def test_quiet_grows_with_the_program(self):
        small = flash_tile.flash_time(make_image(2, 15120), 115200)
        self.assertLess(small, 10)
        self.assertGreater(flash_tile.flash_time(make_image(2, 15120), 9600), small)
        self.assertGreater(flash_tile.flash_time(make_image(2, 200000), 115200), small)
        # a factory file is the whole chip, and most of it takes no time
        factory = flash_tile.flash_time(make_factory(2, 15120), 115200)
        self.assertGreater(factory, small)
        self.assertLess(factory, 20)

    def test_plan(self):
        image = make_factory(2)
        sectors, writes, reads = flash_tile.plan(image)
        self.assertEqual(sectors, 126)
        # what is written is the file, less its blank blocks
        made = bytearray(b"\xff" * len(image))
        for at, data in writes:
            self.assertEqual(at % 256, 0)
            self.assertLessEqual(len(data), 256)
            self.assertTrue(data.strip(b"\xff"))
            made[at:at + len(data)] = data
        self.assertEqual(bytes(made), image)
        # every sector is read at both its ends, and all of it if something
        # was written to it
        looked = set()
        for at, n in reads:
            self.assertTrue(1 <= n <= 256)
            self.assertLessEqual(at + n, len(image))
            looked.update(range(at, at + n))
        wrote = {at // SECTOR for at, _ in writes}
        for s in range(sectors):
            self.assertIn(s * SECTOR, looked)
            self.assertIn(s * SECTOR + SECTOR - 1, looked)
            if s in wrote:
                self.assertTrue(set(range(s * SECTOR, (s + 1) * SECTOR)) <= looked, s)
        self.assertEqual(sorted(reads), reads)

        # a file that ends in the middle of a sector, in a blank stretch
        image = make_image(2, 2048) + b"\xff" * 1024 + b"\x00" * 4 + b"\xff" * 1020 + b"\xff" * 20
        sectors, writes, reads = flash_tile.plan(image)
        self.assertEqual(sectors, 3)
        self.assertEqual([at for at, _ in writes], list(range(0, 2048, 256)) + [3072])
        self.assertEqual(reads[-1], (4096, 20))
        self.assertEqual(sum(n for _, n in reads), 2048 + 2048 + 20)

    def test_the_real_tile_program_passes(self):
        def built(*name):
            return os.path.join(HERE, "..", "build", *name)

        path = built("tile", "tile_factory.bin")
        if not os.path.exists(path):
            self.skipTest("build/tile/tile_factory.bin is not built (`make tile`)")
        image = flash_tile.load_image(path)
        self.assertEqual(len(image), mkimage.FACTORY_LEN)
        self.assertEqual(flash_tile.factory_program(image).kind, 0)
        # the longest quiet there is covers the flashing, and with room
        self.assertLess(flash_tile.flash_time(image, 115200), 35)
        for other in (("hub", "hub_factory.bin"), ("hub", "hub_a.img"), ("tile", "tile_a.img"),
                      ("tile", "tile_b.img"), ("tile", "tile_a.bin")):
            if os.path.exists(built(*other)):
                with self.assertRaises(flash_tile.Failure, msg=other):
                    flash_tile.load_image(built(*other))

    def test_find_hub(self):
        with tempfile.TemporaryDirectory() as top:
            def plug(name, vendor, product):
                # as sysfs has it: class/tty/<name> points into the device's
                # directory, and <name>/device back at the USB interface
                usb = os.path.join(top, "devices", "usb-" + name)
                tty = os.path.join(usb, "interface", "tty", name)
                os.makedirs(tty)
                with open(os.path.join(usb, "idVendor"), "w") as f:
                    f.write(vendor + "\n")
                with open(os.path.join(usb, "idProduct"), "w") as f:
                    f.write(product + "\n")
                os.symlink(os.path.join("..", ".."), os.path.join(tty, "device"))
                os.makedirs(os.path.join(top, "class"), exist_ok=True)
                os.symlink(tty, os.path.join(top, "class", name))

            sys_tty = os.path.join(top, "class")
            os.makedirs(sys_tty)
            with mock.patch.dict(sys.modules, {"serial": None, "serial.tools": None}):
                with self.assertRaises(flash_tile.Failure):
                    flash_tile.find_hub(sys_tty)
                plug("ttyACM0", "2e3c", "df11")
                with self.assertRaises(flash_tile.Failure):
                    flash_tile.find_hub(sys_tty)
                plug("ttyACM3", "1209", "0001")
                self.assertEqual(flash_tile.find_hub(sys_tty), "/dev/ttyACM3")
                plug("ttyACM4", "1209", "0001")
                with self.assertRaises(flash_tile.Failure) as two:
                    flash_tile.find_hub(sys_tty)
                self.assertIn("/dev/ttyACM3, /dev/ttyACM4", str(two.exception))

    def test_posix_port_on_a_pty(self):
        # The real port class against a pseudo-terminal: bytes both ways,
        # untouched, and a wait that ends. A pty has no DTR, so set_dtr is
        # not tried here, or anywhere else.
        try:
            import pty
            master, slave = pty.openpty()
        except (ImportError, OSError) as e:
            self.skipTest(f"no pseudo-terminal: {e}")
        try:
            port = flash_tile.PosixPort(os.ttyname(slave))
        except OSError as e:
            os.close(master)
            os.close(slave)
            self.skipTest(f"cannot open the pseudo-terminal: {e}")
        try:
            every = bytes(range(256))
            port.write(every)
            got = b""
            while len(got) < 256:
                got += os.read(master, 256)
            self.assertEqual(got, every)
            self.assertEqual(port.read(0.05), b"")
            os.write(master, every)
            got = b""
            for _ in range(50):
                got += port.read(0.2)
                if len(got) >= 256:
                    break
            self.assertEqual(got, every)
        finally:
            port.close()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
