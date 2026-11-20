#!/usr/bin/env python3
"""Tests for tools/update.py.

    python3 tests/test_update.py        (or: make test-tools)

The script runs here against tests/hubsim.c: the hub's own program with its
console, the tiles' own program, the cables between them, and every node's
program flash with core/fw.c in front of it, which chooses the slot at each
start (sim/). `World` is the pipe to it and the test's clock: a millisecond
of the script's time is a millisecond of the simulator's, so a minute's
trial takes a moment.

The new programs are made here (`make_image`): images as tools/mkimage.py
makes them, with filler for code. The simulator runs core/'s program
whatever a slot holds, and looks in the image only for how to behave: as a
program that runs, one that hangs, or one that hears nothing.

The tiles have their power through the hub, as on the real boards: a hub
that restarts takes every tile down with it (sim/sim.h: hub_feeds).
`OwnPowerTest` is the other world, in which a tile runs on through the hub's
restart. No board is built like that; the script does not count on it
either way.

Skipped when build/host/hubsim is not built (`make test-tools` builds it).

What the hub's USB does when the hub restarts (the serial port goes and
comes back, perhaps under another name) is imitated by `FakePort`, not
tested; so is everything else a real chip does.
"""

import collections
import io
import math
import os
import random
import re
import shutil
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
import update  # noqa: E402

HUBSIM = os.path.join(HERE, "..", "build", "host", "hubsim")
REAL_BUILD = os.path.join(HERE, "..", "build")

ACT_AT = 0x210                  # sim/flashsim.h: FLASHSIM_ACT_AT
RUN, HANG, DEAF, OLD = 0, 1, 2, 3   # SIM_ACT_
MINUTE = 61000                  # ms: a trial, and a little

# Versions. The simulator's chips start with the program this PC builds,
# which calls itself FW_VERSION (core/proto.h). The builds the tests make
# are far from that, so that the next FW_VERSION is none of them.
with open(os.path.join(HERE, "..", "core", "proto.h")) as f:
    FACTORY = "%04x" % int(re.search(r"#define FW_VERSION\s+(\w+?)u?\s", f.read()).group(1), 0)
NEW, NEXT = 0x0102, 0x0103
BEFORE_RATES = "0001"           # sim.h: SIM_OLD_VERSION, what World(old=True) starts with

# How many times a node's flash is erased or written up to a place in an
# update (core/fw.c): begin erases the slot's first and last sector and
# writes one word; each write is one more, and the first that goes beyond a
# sector erases the next one before.
BEGIN_OPS = 3
SECTOR = 2048


def make_image(kind, version, slot, act=RUN, size=3000):
    """A program image for the simulator: filler, with what core/fw.c and
    sim/flashsim.c look for."""
    rng = random.Random("%d %d %d" % (kind, version, slot))
    body = bytearray(rng.getrandbits(8) for _ in range(size))
    base = mkimage.SLOT_ADDR[slot]
    struct.pack_into("<II", body, 0, mkimage.RAM_END, (base + ACT_AT + 8) | 1)
    mkimage.HEADER.pack_into(body, mkimage.HEADER_AT, mkimage.IMAGE_MAGIC, kind, 0, version,
                             base, 0)
    body[ACT_AT:ACT_AT + 5] = b"SIM!" + bytes([act])
    return mkimage.make_image(bytes(body))


Build = collections.namedtuple("Build", "dir tile hub")     # tile, hub: the image in A and in B
Chip = collections.namedtuple("Chip", "slot trial doing a b reboots wdt_resets no_program")
Slot = collections.namedtuple("Slot", "state version crc")


class World:
    """tests/hubsim.c behind its pipe, and the clock."""

    def __init__(self, tiles=1, cables=None, fed=True, old=False):
        self.proc = subprocess.Popen([HUBSIM], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True)
        hello = self.proc.stdout.readline().split()
        if len(hello) != 3 or hello[0] != "hubsim" or hello[2] != FACTORY:
            raise AssertionError("hubsim began with %r" % hello)
        self.ids = [hello[1]]
        if tiles > 1:
            self.ids = self.pipe("n %d" % tiles).split()[1:]
        if old:
            self.pipe("o")              # programs from before the cables' two rates
        if fed:
            self.pipe("e 1")            # the tiles' power comes through the hub
        if cables is None:
            # a row: the hub's cable in tile 0's W port, and E to W from there
            cables = [(i, "E", i + 1, "W") for i in range(tiles - 1)]
        for cable in cables:
            self.pipe("k %d %s %d %s" % cable)
        self.ms = 0
        self.hub = "1"                  # how often the hub's program has started; - while down
        self.to_pc = bytearray()
        self.line = bytearray()         # what the script is typing
        self.typed_lines = []
        self.hooks = []
        self.timers = []
        self.ports = []

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

    # -- time

    def now(self):
        return self.ms / 1000.0

    def sleep(self, seconds):
        self.run(math.ceil(seconds * 1000))

    def run(self, ms, until_output=False):
        """So many milliseconds pass; fewer with until_output, if the hub
        says something, or stops, or starts."""
        end = self.ms + int(ms)
        while self.ms < end:
            for timer in [t for t in self.timers if t[0] <= self.ms]:
                self.timers.remove(timer)
                timer[1]()
            step = min([end] + [t[0] for t in self.timers]) - self.ms
            _frames, _tile, _bridge, pc, _cable, hub, ran = self.pipe("s %d" % step).split()
            self.ms += int(ran)
            if pc != "-":
                self.to_pc += bytes.fromhex(pc)
            changed = hub != self.hub
            self.hub = hub
            if until_output and (self.to_pc or changed):
                break

    # -- what a test does to the nodes. A node: a tile's number, or "h"

    def power(self, node, on):
        self.pipe("p %d %s" % (on, node))
        self.run(1)

    def power_cycle(self, node):
        self.pipe("p 0 %s" % node)
        self.run(30)
        self.pipe("p 1 %s" % node)
        self.run(1)

    def console(self, line, ms=20):
        """What the hub's console answers to a line, typed between two runs
        of the script."""
        self.pipe("t " + (line + "\r").encode().hex())
        self.run(ms)
        text = bytes(self.to_pc).decode()
        self.to_pc.clear()
        return text

    def tiles(self):
        """The tiles' IDs as the hub's console lists them: in the order of
        their first words to the hub, which is nobody's to choose."""
        return re.findall(r"^\d+ ([0-9a-f]{8}) ", self.console("tiles"), re.M)

    def chip(self, node):
        w = self.pipe("q %s" % node).split()
        return Chip(w[0], int(w[1]), w[2], Slot(*w[3:6]), Slot(*w[6:9]), int(w[9]), int(w[10]),
                    int(w[11]))

    def after(self, ms, what):
        """Call `what` so many milliseconds from now."""
        self.timers.append((self.ms + ms, what))

    # -- what the script types

    def when(self, start, what, nth=1):
        """Call `what` when the script types the nth line that begins so,
        a moment before the hub gets the line."""
        self.hooks.append([start, nth, what])

    def dead_from(self, start, cable, nth=1):
        """The cable carries nothing from when the script types such a line
        until it types its next `fw` line."""
        def cut():
            self.pipe("l %d 1000" % cable)
            self.when("fw", lambda: self.pipe("l %d 0" % cable))

        self.when(start, cut, nth)

    def typed(self, data):
        for c in data:
            if c in (10, 13):
                if not self.line:
                    continue
                text = self.line.decode()
                self.line.clear()
                self.typed_lines.append(text)
                for hook in list(self.hooks):
                    if text.startswith(hook[0]):
                        hook[1] -= 1
                        if hook[1] == 0:
                            self.hooks.remove(hook)
                            hook[2]()
            elif c in (8, 127):
                del self.line[-1:]
            elif 0x20 <= c < 0x7F:
                self.line.append(c)


def stop():
    raise KeyboardInterrupt


class FakePort:
    """The hub's serial port, as flash_tile wants a port to be. The port
    is the hub's program's: it goes when that stops, and what comes when the
    hub starts again is another one."""

    def __init__(self, world):
        if world.hub == "-":
            raise flash_tile.Failure("cannot open fake: No such file or directory")
        self.world = world
        self.born = world.hub
        self.is_open = True
        world.ports.append(self)
        world.to_pc.clear()
        world.pipe("d 1")               # as opening a serial port does

    def gone(self):
        return self.world.hub != self.born

    def check(self):
        if not self.is_open:
            raise AssertionError("the script uses a port that it closed")
        if self.gone():
            raise flash_tile.Failure("the hub's serial port has gone (Input/output error)")

    def write(self, data):
        self.check()
        self.world.typed(data)          # a hook may take the hub away
        self.check()
        self.world.pipe("t " + bytes(data).hex())

    def read(self, wait):
        w = self.world
        self.check()
        if not w.to_pc and wait > 0:
            w.run(max(1, math.ceil(wait * 1000)), until_output=True)
            self.check()
        data = bytes(w.to_pc[:23])      # in pieces, as USB may
        del w.to_pc[:23]
        return data

    def set_dtr(self, on):
        if self.gone():
            raise OSError(5, "Input/output error")      # as the ioctl does
        self.world.pipe("d %d" % on)

    def close(self):
        if not self.gone():
            self.world.pipe("d 0")
        self.is_open = False


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.world = None
        self.out = ""
        self.mark = 0

    def start(self, tiles=1, warm_up=3000, cables=None, fed=True, old=False):
        self.world = World(tiles, cables, fed, old)
        self.addCleanup(self.world.close)
        self.world.run(warm_up)
        return self.world

    def build(self, version, tile=RUN, hub=RUN):
        """A build directory with one version's four images."""
        root = os.path.join(self.dir.name, "v%d" % version)
        images = {}
        for kind, act in (("tile", tile), ("hub", hub)):
            os.makedirs(os.path.join(root, kind))
            images[kind] = []
            for slot in (0, 1):
                data = make_image(update.KIND[kind], version, slot, act)
                with open(os.path.join(root, kind, "%s_%s.img" % (kind, "ab"[slot])), "wb") as f:
                    f.write(data)
                images[kind].append(mkimage.parse_image(data))
        return Build(root, images["tile"], images["hub"])

    def run_script(self, build, *args, find_hub=None):
        """main() against the world; the exit code. What it printed is in self.out."""
        w = self.world
        out = io.StringIO()
        self.mark = len(w.typed_lines)
        argv = ["--build", build.dir] + list(args)
        if find_hub is None:
            argv += ["--port", "fake"]
        with mock.patch.object(flash_tile, "now", w.now), \
                mock.patch.object(flash_tile, "sleep", w.sleep):
            code = update.main(argv, open_port=lambda name: FakePort(w), find_hub=find_hub,
                               stream=out)
        self.out = out.getvalue()
        for port in w.ports:
            self.assertFalse(port.is_open, "the script left a port open")
        w.hooks.clear()
        return code

    def typed(self, start):
        """The lines the last run typed that begin so."""
        return [t for t in self.world.typed_lines[self.mark:] if t.startswith(start)]

    def chip(self, node):
        return self.world.chip(node)

    def check_runs(self, node, image, state="good"):
        """The node runs this image, and the image's slot is in this state."""
        chip = self.chip(node)
        self.assertEqual(chip.doing, "run", self.out)
        self.assertEqual((chip.slot, chip[3 + image.slot]),
                         ("ab"[image.slot], Slot(state, "%04x" % image.version,
                                                 "%08x" % image.crc)),
                         "node %s: %s\n%s" % (node, chip, self.out))

    def check_old(self, node, other=None):
        """The node runs what it came with; `other`: slot B's state."""
        chip = self.chip(node)
        self.assertEqual((chip.slot, chip.doing, chip.a.state, chip.a.version),
                         ("a", "run", "none", FACTORY), "node %s: %s\n%s" % (node, chip, self.out))
        if other:
            self.assertEqual(chip.b.state, other, "node %s: %s\n%s" % (node, chip, self.out))

    def check_console(self):
        """Whatever happened, the hub's console is there for the next one."""
        w = self.world
        port = FakePort(w)
        port.write(b"\x7f" * 8 + b"\rversion\r")
        w.run(30)
        self.assertIn(b"piano hub, firmware", bytes(w.to_pc), self.out)
        port.close()


@unittest.skipUnless(os.path.exists(HUBSIM), "build/host/hubsim is not built")
class UpdateTest(Case):

    # -- the way it is meant to go ------------------------------------------

    def test_one_tile_and_the_hub(self):
        w = self.start()
        new = self.build(NEW)
        tile = w.ids[0]
        self.assertEqual(self.run_script(new), 0, self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])
        # nobody told the tile to restart: its power went with the hub's
        self.assertEqual((self.chip(0).reboots, self.chip("h").reboots), (0, 1))
        self.assertEqual(self.typed("reboot"), ["reboot hub"])
        self.assertEqual(self.typed("fw %s begin" % tile),
                         ["fw %s begin %d" % (tile, new.tile[1].length)])
        self.assertEqual(len(self.typed("fw %s write" % tile)), -(-new.tile[1].length // 48))
        self.assertEqual(self.typed("fw %s commit" % tile), ["fw %s commit" % tile])
        self.assertEqual(self.typed("fw hub begin"), ["fw hub begin %d" % new.hub[1].length])
        self.assertEqual(len(self.typed("fw hub write")), -(-new.hub[1].length // 48))
        self.assertEqual(len(self.typed("fw hub confirm")), 1)
        for text in ("the tile's program in %s: version 0102, %d bytes"
                     % (os.path.join(new.dir, "tile"), new.tile[0].length),
                     "tile %s: runs version %s in slot A; gets version 0102 into slot B"
                     % (tile, FACTORY),
                     "the hub: runs version %s in slot A; gets version 0102 into slot B" % FACTORY,
                     "sending tile %s version 0102 for its slot B: %d bytes"
                     % (tile, new.tile[1].length),
                     "restarting the hub, and the tile with it: its power comes through the "
                     "hub\n",
                     "the hub is back: piano hub, firmware",
                     "done: 1 tile now runs version 0102 and the hub now runs version 0102"):
            self.assertIn(text, self.out)
        self.assertNotIn("restarting tile", self.out)
        self.assertEqual(len(w.ports), 2)
        self.check_console()

    def test_a_row_of_tiles(self):
        w = self.start(4)
        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        for i in range(4):
            self.check_runs(i, new.tile[1])
            self.assertEqual(self.chip(i).reboots, 0)
        self.check_runs("h", new.hub[1])
        self.assertEqual(self.typed("reboot"), ["reboot hub"])
        self.assertIn("restarting the hub, and the tiles with it: their power comes through "
                      "the hub\n", self.out)
        self.assertEqual(sorted(t.split()[1] for t in self.typed("fw") if t.endswith("confirm")),
                         sorted(w.ids + ["hub"]))
        self.assertIn("done: 4 tiles now run version 0102 and the hub now runs version 0102",
                      self.out)

    def test_tiles_that_branch(self):
        # the hub's tile in the middle, a tile on three of its sides, and
        # one more behind one of those
        w = self.start(5, cables=[(0, "N", 1, "S"), (0, "E", 2, "W"), (0, "S", 3, "N"),
                                  (2, "E", 4, "W")])
        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        for i in range(5):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.assertEqual(len(w.ids), 5)

    def test_only_the_tiles(self):
        self.start(2)
        new = self.build(NEW)
        shutil.rmtree(os.path.join(new.dir, "hub"))     # not needed, so not read
        self.assertEqual(self.run_script(new, "--only", "tiles"), 0, self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs(1, new.tile[1])
        self.check_old("h", other="none")
        self.assertEqual(self.typed("fw hub"), [])
        self.assertEqual(self.typed("reboot"), ["reboot all"])
        self.assertIn("restarting the tiles\n", self.out)
        self.assertEqual([self.chip(n).reboots for n in (0, 1, "h")], [1, 1, 0])
        self.assertEqual(len(self.world.ports), 1)
        self.assertIn("done: 2 tiles now run version 0102\n", self.out)
        self.assertNotIn("the hub:", self.out)

    def test_only_the_hub(self):
        w = self.start(2)
        new = self.build(NEW)
        shutil.rmtree(os.path.join(new.dir, "tile"))
        self.assertEqual(self.run_script(new, "--only", "hub"), 0, self.out)
        self.check_runs("h", new.hub[1])
        for i in (0, 1):
            self.check_old(i, other="none")
            self.assertEqual(self.chip(i).reboots, 0)
        self.assertEqual(self.typed("reboot"), ["reboot hub"])
        # the tiles are not asked what they run before, and are after: they
        # went with the hub's power, and have to be there again
        asked = [t for t in self.typed("fw") if not t.startswith("fw hub")]
        self.assertEqual(set(asked), set("fw " + i for i in w.ids))
        self.assertLess(self.typed("fw").index("fw hub"), self.typed("fw").index(asked[0]))
        self.assertIn("the hub sees 2 tiles", self.out)
        self.assertIn("done: the hub now runs version 0102\n", self.out)

    def test_again_does_nothing(self):
        w = self.start(2)
        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        before = [self.chip(n) for n in (0, 1, "h")]
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertEqual([self.chip(n) for n in (0, 1, "h")], before)
        self.assertEqual(sorted(self.typed("fw")), sorted("fw " + n for n in w.ids + ["hub"]))
        self.assertEqual(self.typed("reboot"), [])
        self.assertIn("tile %s: runs version 0102 in slot B, which is this build" % w.ids[1],
                      self.out)
        self.assertIn("nothing to send: they all run this build", self.out)

    def test_the_next_version_goes_into_slot_a(self):
        self.start()
        second, third = self.build(NEW), self.build(NEXT)
        self.assertEqual(self.run_script(second), 0, self.out)
        self.assertEqual(self.run_script(third), 0, self.out)
        self.check_runs(0, third.tile[0])
        self.check_runs("h", third.hub[0])
        self.assertEqual(self.chip(0).b, Slot("good", "0102", "%08x" % second.tile[1].crc))
        self.assertIn("runs version 0102 in slot B; gets version 0103 into slot A", self.out)
        # and back: an older build is a build like any other
        self.assertEqual(self.run_script(second), 0, self.out)
        self.check_runs(0, second.tile[1])
        self.check_runs("h", second.hub[1])

    def test_only_the_nodes_that_need_it(self):
        self.start(2)
        new = self.build(NEW)
        self.assertEqual(self.run_script(new, "--only", "hub"), 0, self.out)
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertEqual(self.typed("fw hub begin"), [])
        self.assertEqual(self.typed("reboot"), ["reboot all"])
        self.assertEqual(self.chip("h").reboots, 1)
        self.check_runs(0, new.tile[1])
        self.check_runs(1, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.assertIn("the hub: runs version 0102 in slot B, which is this build", self.out)
        self.assertIn("done: 2 tiles now run version 0102\n", self.out)

    def test_check_changes_nothing(self):
        w = self.start(2)
        new = self.build(NEW)
        before = [self.chip(n) for n in (0, 1, "h")]
        self.assertEqual(self.run_script(new, "--check"), 0, self.out)
        self.assertEqual([self.chip(n) for n in (0, 1, "h")], before)
        self.assertEqual(sorted(self.typed("fw")), sorted("fw " + n for n in w.ids + ["hub"]))
        self.assertEqual(self.typed("reboot"), [])
        self.assertIn("tile %s: runs version %s in slot A; gets version 0102 into slot B"
                      % (w.ids[1], FACTORY), self.out)
        self.assertIn("--check: nothing was changed", self.out)

    def test_hub_only_just_plugged_in(self):
        w = self.start(3, warm_up=0)
        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        for i in range(3):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.assertLess(w.now(), 60)

    def test_cables_are_quiet_from_an_earlier_command(self):
        # tools/flash_tile.py, cut off; the hub forgets its tiles meanwhile
        w = self.start(3)
        new = self.build(NEW)
        port = FakePort(w)
        port.write(b"quiet 3000\r")
        w.run(5)
        port.close()
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("the cables are quiet from an earlier command", self.out)
        for i in range(3):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_no_tiles(self):
        w = self.start(warm_up=0)
        w.power(0, False)
        w.run(4000)
        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        self.check_runs("h", new.hub[1])
        self.assertIn("the hub sees no tiles", self.out)
        self.assertIn("done: the hub now runs version 0102\n", self.out)
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("nothing to send: it runs this build", self.out)
        self.assertEqual(self.run_script(new, "--only", "tiles"), 0, self.out)
        self.assertIn("nothing to send\n", self.out)

    def test_finds_the_hub_by_itself(self):
        self.start()
        new = self.build(NEW)
        looked = []
        self.assertEqual(self.run_script(new, find_hub=lambda: looked.append(1) or "found"), 0,
                         self.out)
        self.assertEqual(len(looked), 2)        # and again after the hub's restart
        self.check_runs("h", new.hub[1])

    def from_before_the_cable_rates(self, fed):
        # A hub and tiles that know one rate take programs that know two,
        # and then, the same way, programs that know one again.
        w = self.start(3, fed=fed, old=True)
        for node in (0, 1, 2, "h"):
            self.assertEqual(self.chip(node).a.version, BEFORE_RATES)
        self.assertNotIn("Mbaud", w.console("info " + w.ids[1], 60))

        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("tile %s: runs version %s in slot A; gets version 0102 into slot B"
                      % (w.ids[2], BEFORE_RATES), self.out)
        self.assertIn("done: 3 tiles now run version 0102 and the hub now runs version 0102",
                      self.out)
        for i in range(3):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.assertNotIn("slow:", w.console("grid"))
        for i, ports in enumerate(("WE", "WE", "W")):
            info = w.console("info " + w.ids[i], 60)
            self.assertEqual(re.findall(r"^([WNES]): .*, 4 Mbaud\r?$", info, re.M), list(ports), info)
        self.assertIn("dropped from a full queue, 4 Mbaud\r\n", w.console("stats"))

        old = self.build(NEXT, tile=OLD, hub=OLD)
        self.assertEqual(self.run_script(old), 0, self.out)
        self.assertIn("done: 3 tiles now run version 0103 and the hub now runs version 0103",
                      self.out)
        for i in range(3):
            self.check_runs(i, old.tile[0])
        self.check_runs("h", old.hub[0])
        self.assertNotIn("Mbaud", w.console("info " + w.ids[1], 60))
        self.assertIn("dropped from a full queue, 1 Mbaud\r\n", w.console("stats"))
        self.assertNotIn("slow:", w.console("grid"))
        self.assertNotIn("telling it again", self.out)
        self.check_console()

    def test_keyboard_from_before_the_cable_rates(self):
        self.from_before_the_cable_rates(fed=True)

    def test_keyboard_from_before_the_cable_rates_on_its_own_power(self):
        # the tiles restart one by one, each next to neighbours of the other kind
        self.from_before_the_cable_rates(fed=False)

    @unittest.skipUnless(all(os.path.exists(os.path.join(REAL_BUILD, kind, "%s_%s.img" % (kind, s)))
                             for kind in ("tile", "hub") for s in "ab"),
                         "the tile's and the hub's images are not built")
    def test_the_real_programs(self):
        self.start()
        self.assertEqual(self.run_script(Build(REAL_BUILD, None, None)), 0, self.out)
        for node, kind in ((0, "tile"), ("h", "hub")):
            with open(os.path.join(REAL_BUILD, kind, kind + "_b.img"), "rb") as f:
                self.check_runs(node, mkimage.parse_image(f.read()))

    # -- cables and flash that are not what they should be ------------------

    def test_cables_that_lose_frames(self):
        w = self.start(3)
        w.pipe("l 0 60")
        w.pipe("l 2 100")
        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        for i in range(3):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])

    def flash_fails(self, node, who, nth, begins, twice=None):
        w = self.start()
        new = self.build(NEW)
        who = who or w.ids[0]
        w.pipe("x %s %d" % (node, nth))
        self.assertEqual(self.run_script(new), 0, self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.assertEqual(len(self.typed("fw %s begin" % who)), begins)
        if twice:
            self.assertEqual(len(self.typed("fw %s %s" % (who, twice))), 2)

    def test_tile_flash_fails_at_the_begin(self):
        self.flash_fails(0, None, 1, 2)

    def test_tile_flash_fails_at_a_write(self):
        self.flash_fails(0, None, BEGIN_OPS + 7, 2, twice="write 288 ")

    def test_tile_flash_fails_at_an_erase_on_the_way(self):
        # the write that reaches the second sector; the node is still where
        # it was, and takes the same write again
        self.flash_fails(0, None, BEGIN_OPS + SECTOR // 48 + 1, 1,
                         twice="write %d " % (SECTOR // 48 * 48))

    def test_tile_flash_fails_at_the_commit(self):
        new_len = len(make_image(0, NEW, 1))
        self.flash_fails(0, None, BEGIN_OPS + -(-new_len // 48) + 1 + 1, 2, twice="commit")

    def test_hub_flash_fails_at_the_begin(self):
        self.flash_fails("h", "hub", 2, 2)

    def test_hub_flash_fails_at_a_write(self):
        self.flash_fails("h", "hub", BEGIN_OPS + 7, 2, twice="write 288 ")

    def test_hub_flash_fails_at_an_erase_on_the_way(self):
        self.flash_fails("h", "hub", BEGIN_OPS + SECTOR // 48 + 1, 1,
                         twice="write %d " % (SECTOR // 48 * 48))

    def test_an_answer_that_got_lost(self):
        # the tile took a write and the script did not hear so: here,
        # because somebody else typed that write a moment before
        w = self.start()
        new = self.build(NEW)

        def first():
            w.pipe("t " + (w.typed_lines[-1] + "\r").encode().hex())
            w.run(60)
            w.to_pc.clear()

        w.when("fw %s write 480 " % w.ids[0], first)
        self.assertEqual(self.run_script(new, "-v"), 0, self.out)
        self.assertIn("value not allowed, at 528", self.out)
        self.assertEqual(len(self.typed("fw %s write" % w.ids[0])), -(-new.tile[1].length // 48))
        self.assertEqual(len(self.typed("fw %s begin" % w.ids[0])), 1)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_flash_that_keeps_failing(self):
        w = self.start()
        new = self.build(NEW)
        for nth in (1, 2, 3):
            w.when("fw %s begin" % w.ids[0], lambda: w.pipe("x 0 1"), nth)
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertEqual(len(self.typed("fw %s begin" % w.ids[0])), 3)
        self.assertIn("error: tile %s did not take its new program, begun 3 times" % w.ids[0],
                      self.out)
        self.assertIn("No node was restarted", self.out)
        self.assertEqual(self.typed("reboot"), [])
        self.check_old(0, other="none")
        self.check_old("h", other="none")
        self.check_console()

    # -- new programs that are no good --------------------------------------

    def check_all_old(self, tiles, other="tried"):
        for i in range(tiles):
            self.check_old(i, other=other)
        self.check_old("h", other=other)

    def test_tile_program_that_hangs(self):
        w = self.start(2)
        bad = self.build(NEW, tile=HANG)
        self.assertEqual(self.run_script(bad), 1, self.out)
        for i in (0, 1):
            self.assertIn("tile %s started its new program and is back in the old one" % w.ids[i],
                          self.out)
            self.assertEqual(self.chip(i).wdt_resets, 1)
        self.assertIn("Nothing was confirmed", self.out)
        self.assertEqual(self.typed("fw hub confirm"), [])
        # the hub's new program was fine, and goes all the same
        self.check_runs("h", bad.hub[1], state="tried")
        w.run(MINUTE)
        self.check_all_old(2)
        self.check_console()

        # the same build again: it says so, and tries
        self.assertEqual(self.run_script(bad, "--check"), 0, self.out)
        self.assertIn("tile %s: runs version %s in slot A; gets version 0102 into slot B again: "
                      "it was started there before\n    and did not stay" % (w.ids[0], FACTORY), self.out)
        # a build that works goes where the bad one was
        good = self.build(NEXT)
        self.assertEqual(self.run_script(good), 0, self.out)
        self.check_runs(0, good.tile[1])
        self.check_runs(1, good.tile[1])
        self.check_runs("h", good.hub[1])

    def test_tile_program_that_hears_nothing(self):
        w = self.start(2)
        bad = self.build(NEW, tile=DEAF)
        self.assertEqual(self.run_script(bad), 1, self.out)
        for i in (0, 1):
            self.assertIn("tile %s did not come back" % w.ids[i], self.out)
        self.assertIn("Nothing was confirmed", self.out)
        self.check_runs(0, bad.tile[1], state="tried")
        w.run(MINUTE)
        self.check_all_old(2)
        self.assertEqual(self.run_script(self.build(NEXT)), 0, self.out)

    def test_hub_program_that_hangs(self):
        w = self.start(2)
        bad = self.build(NEW, hub=HANG)
        self.assertEqual(self.run_script(bad), 1, self.out)
        self.assertIn("the hub is back: piano hub, firmware", self.out)
        self.assertIn("error: the hub started its new program and is back in the old one\n"
                      "Nothing was confirmed", self.out)
        self.assertEqual([t for t in self.typed("fw") if t.endswith("confirm")], [])
        self.assertEqual(self.chip("h").wdt_resets, 1)
        self.check_old("h", other="tried")
        # the tiles had no power while the hub's program hung. The old one
        # feeds them, and they start their new program, which was fine, and
        # goes all the same
        w.run(1000)
        for i in (0, 1):
            self.check_runs(i, bad.tile[1], state="tried")
            self.assertEqual(self.chip(i).reboots, 0)
        w.run(MINUTE)
        self.check_all_old(2)
        self.assertEqual([self.chip(i).reboots for i in (0, 1)], [1, 1])
        self.assertEqual(self.run_script(self.build(NEXT)), 0, self.out)

    def test_hub_program_that_hears_nothing(self):
        w = self.start(2)
        bad = self.build(NEW, hub=DEAF)
        began = w.now()
        self.assertEqual(self.run_script(bad), 1, self.out)
        self.assertIn("the hub is not back yet", self.out)
        self.assertIn("the hub is back: piano hub, firmware", self.out)
        self.assertIn("error: the hub started its new program and is back in the old one\n"
                      "Nothing was confirmed", self.out)
        self.assertGreater(w.now() - began, 60)
        self.assertEqual(self.chip("h").wdt_resets, 0)
        # the tiles' minute was not over: the hub took their power as it went
        w.run(1000)
        self.check_all_old(2)
        self.assertEqual([self.chip(i).reboots for i in (0, 1)], [0, 0])
        self.check_console()

    def test_hub_that_stays_away(self):
        w = self.start()
        new = self.build(NEW)
        # its power goes as it restarts
        w.when("reboot hub", lambda: w.after(100, lambda: w.pipe("p 0 h")))
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("error: the hub did not come back after its restart (cannot open fake: "
                      "No such file or directory).\nUnplug it", self.out)
        self.assertIn("Nothing was confirmed", self.out)
        # and the tile's with it
        self.assertEqual((self.chip(0).doing, self.chip(0).b.state), ("off", "new"))
        w.power("h", True)
        w.run(3000)
        # neither had started its new program, and does now, on trial
        self.check_runs("h", new.hub[1], state="tried")
        self.check_runs(0, new.tile[1], state="tried")
        w.run(MINUTE)
        self.check_all_old(1)

    def test_too_late_to_confirm(self):
        w = self.start()
        new = self.build(NEW)
        with mock.patch.object(update, "CONFIRM_BY", 0.5):
            self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("too close to the\nend of their trial to confirm them all in time\n"
                      "Nothing was confirmed", self.out)
        self.assertEqual([t for t in self.typed("fw") if t.endswith("confirm")], [])
        w.run(MINUTE)
        self.check_all_old(1)

    # -- tiles that come and go ---------------------------------------------

    def test_tile_that_did_not_hear_the_restart(self):
        w = self.start(3)
        new = self.build(NEW)
        w.dead_from("reboot all", 2)            # the cable to the last tile
        self.assertEqual(self.run_script(new, "--only", "tiles"), 0, self.out)
        self.assertIn("tile %s did not restart; telling it again" % w.ids[2], self.out)
        self.assertEqual(self.typed("reboot"), ["reboot all", "reboot " + w.ids[2]])
        for i in range(3):
            self.check_runs(i, new.tile[1])
            self.assertEqual(self.chip(i).reboots, 1)

    def test_no_tile_hears_the_restart(self):
        w = self.start(2)
        new = self.build(NEW)
        w.dead_from("reboot all", 0)            # the hub's own cable
        self.assertEqual(self.run_script(new, "--only", "tiles"), 0, self.out)
        self.assertEqual(self.out.count("restarting the tiles\n"), 1)
        for i in (0, 1):
            self.assertIn("tile %s did not restart; telling it again\n" % w.ids[i], self.out)
            self.check_runs(i, new.tile[1])
            self.assertEqual(self.chip(i).reboots, 1)
        self.assertEqual(self.typed("reboot"),
                         ["reboot all", "reboot " + w.ids[0], "reboot " + w.ids[1]])

    def test_tile_that_never_hears_the_restart(self):
        w = self.start(2)
        new = self.build(NEW)
        w.dead_from("reboot all", 1)
        for nth in range(1, update.KICKS + 1):
            w.dead_from("reboot " + w.ids[1], 1, nth)
        self.assertEqual(self.run_script(new, "--only", "tiles"), 1, self.out)
        self.assertEqual(self.typed("reboot"),
                         ["reboot all"] + ["reboot " + w.ids[1]] * update.KICKS)
        self.assertIn("error: tile %s still runs its old program: it did not restart\n"
                      "Nothing was confirmed" % w.ids[1], self.out)
        self.assertEqual([t for t in self.typed("fw") if t.endswith("confirm")], [])
        self.check_old(1, other="new")
        self.assertEqual(self.chip(1).reboots, 0)
        w.run(MINUTE)
        self.check_old(0, other="tried")
        self.check_old("h", other="none")
        self.check_old(1, other="new")
        # the next run: the one that never restarted holds its program still
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertEqual(self.typed("fw %s begin" % w.ids[1]), [])
        self.assertEqual(len(self.typed("fw %s begin" % w.ids[0])), 1)
        for i in (0, 1):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_tile_loses_power_while_it_is_sent_its_program(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("fw %s write" % w.ids[1], lambda: w.power_cycle(1), nth=30)
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertEqual(len(self.typed("fw %s begin" % w.ids[1])), 2)
        self.check_runs(0, new.tile[1])
        self.check_runs(1, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_tile_goes_while_it_is_sent_its_program(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("fw %s write" % w.ids[1], lambda: w.power(1, False), nth=30)
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertRegex(self.out, "error: tile %s (stopped answering|left the hub's list) while "
                         "it was sent its new program\nNo node was restarted" % w.ids[1])
        self.assertEqual(self.typed("reboot"), [])
        self.assertEqual(self.chip(0).b.state, "new")
        self.check_old(0)
        self.check_old("h", other="none")
        self.check_console()
        # without it, the others get theirs: what tile 0 holds is not sent again
        w.run(4000)
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertEqual(self.typed("fw %s begin" % w.ids[0]), [])
        self.assertIn("tile %s: runs version %s in slot A; holds version 0102 for slot B "
                      "already, not started yet" % (w.ids[0], FACTORY), self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_tile_goes_while_it_is_asked(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("fw " + w.ids[1], lambda: w.power(1, False))
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("tile %s: was in the hub's list and has left it" % w.ids[1], self.out)
        self.assertIn("done: 1 tile now runs version 0102 and the hub now runs version 0102\n"
                      "error: not updated, for what it says above: tile %s\n" % w.ids[1],
                      self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_tile_restarts_before_it_is_told_to(self):
        w = self.start(2)
        new = self.build(NEW)
        # its power goes and comes back when it has just taken the program
        w.when("fw %s commit" % w.ids[1],
               lambda: w.when("fw " + w.ids[1], lambda: w.power_cycle(1)))
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("error: tile %s took its new program and does not hold it afterwards (it "
                      "runs from slot B, and its other slot is none)\nNo node was restarted"
                      % w.ids[1], self.out)
        self.check_runs(1, new.tile[1], state="tried")
        self.check_old(0, other="new")
        # the next run, within the minute, keeps it there
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("tile %s: confirmed" % w.ids[1], self.out)
        self.assertEqual(self.typed("reboot"), ["reboot hub"])
        for i in (0, 1):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_tile_loses_power_before_it_is_confirmed(self):
        w = self.start(2)
        new = self.build(NEW)
        # a program on trial is not started a second time
        w.when("fw %s confirm" % w.ids[1], lambda: w.power_cycle(1))
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("error: tile %s could not be confirmed: it has gone back to the program "
                      "before\n%s\n" % (w.ids[1], update.HALF_CONFIRMED), self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.check_old(1, other="tried")
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertEqual(self.typed("reboot"), ["reboot " + w.ids[1]])
        for i in (0, 1):
            self.check_runs(i, new.tile[1])
        self.assertIn("done: 1 tile now runs version 0102\n", self.out)

    def test_tile_on_trial_is_not_restarted_with_the_others(self):
        # tiles 1 and 2 each on a side of tile 0, where the hub's cable is
        w = self.start(3, cables=[(0, "N", 1, "S"), (0, "E", 2, "W")])
        new = self.build(NEW)
        # The power of one of the two goes and comes back as the last tile
        # of the hub's list has its program whole: that one starts its own,
        # and a `reboot all` would be the end of that
        order = w.tiles()
        self.assertEqual(sorted(order), sorted(w.ids))
        cycled = 1 if order[-1] == w.ids[2] else 2
        others = [t for t in order if t != w.ids[cycled]]
        w.when("fw %s commit" % order[-1], lambda: w.power_cycle(cycled))
        self.assertEqual(self.run_script(new, "--only", "tiles"), 0, self.out)
        self.assertEqual([t.split()[1] for t in self.typed("fw") if t.endswith(" commit")], order)
        self.assertEqual(self.typed("reboot"), ["reboot " + t for t in others])
        self.assertEqual([self.chip(i).reboots for i in range(3)],
                         [int(i != cycled) for i in range(3)])
        for i in range(3):
            self.check_runs(i, new.tile[1])
        self.assertIn("restarting tile %s\nrestarting tile %s\n" % tuple(others), self.out)
        self.assertNotIn("telling it again", self.out)

    def test_tile_goes_as_the_tiles_restart(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("reboot all", lambda: w.power(1, False))
        self.assertEqual(self.run_script(new, "--only", "tiles"), 1, self.out)
        self.assertIn("error: tile %s did not come back\nNothing was confirmed" % w.ids[1],
                      self.out)
        self.assertEqual([t for t in self.typed("fw") if t.endswith("confirm")], [])
        self.check_runs(0, new.tile[1], state="tried")
        w.run(MINUTE)
        self.check_old(0, other="tried")
        self.assertEqual((self.chip(0).reboots, self.chip("h").reboots), (2, 0))

    def test_tile_goes_at_the_restart(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("reboot hub", lambda: w.power(1, False))
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("error: tile %s did not come back\nNothing was confirmed" % w.ids[1],
                      self.out)
        self.assertEqual([t for t in self.typed("fw") if t.endswith("confirm")], [])
        self.check_runs(0, new.tile[1], state="tried")
        w.run(MINUTE)
        self.check_old(0, other="tried")
        self.check_old("h", other="tried")

    def test_tile_that_got_nothing_stays_away(self):
        # the hub's new program has to find every tile again, also one that
        # got no program
        w = self.start(2)
        new = self.build(NEW)
        w.when("reboot hub", lambda: w.power(1, False))
        self.assertEqual(self.run_script(new, "--only", "hub"), 1, self.out)
        self.assertIn("error: tile %s did not come back\nNothing was confirmed" % w.ids[1],
                      self.out)
        self.assertEqual(self.typed("fw hub confirm"), [])
        self.check_runs("h", new.hub[1], state="tried")
        w.run(MINUTE)
        self.check_old("h", other="tried")

    def test_hub_loses_power_while_it_sends(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("fw %s write" % w.ids[1], lambda: w.power_cycle("h"), nth=30)
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("error: the hub's serial port has gone (Input/output error)\n"
                      "No node was restarted", self.out)
        self.assertEqual(self.chip(0).b.state, "new")
        w.run(3000)
        # the tiles' power went with the hub's: tile 0, which had its
        # program whole, has started it, and the next run keeps it there
        self.check_runs(0, new.tile[1], state="tried")
        self.check_old(1, other="partial")
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("tile %s: confirmed" % w.ids[0], self.out)
        self.assertEqual(self.typed("fw %s begin" % w.ids[0]), [])
        self.assertEqual(len(self.typed("fw %s begin" % w.ids[1])), 1)
        self.check_runs(0, new.tile[1])
        self.check_runs(1, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_tile_from_before_the_bootloader(self):
        w = self.start(2)
        new = self.build(NEW)
        w.pipe("u 1")
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("tile %s: its program knows nothing of slots" % w.ids[1], self.out)
        self.assertIn("build/tile/tile_factory.bin", self.out)
        self.assertIn("done: 1 tile now runs version 0102 and the hub now runs version 0102\n"
                      "error: not updated, for what it says above: tile %s\n" % w.ids[1],
                      self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.check_old(1, other="none")
        self.assertEqual(self.typed("fw %s begin" % w.ids[1]), [])
        self.assertEqual(self.typed("reboot"), ["reboot hub"])

    def test_hub_from_before_the_bootloader(self):
        w = self.start(2)
        new = self.build(NEW)
        w.pipe("u h")
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("the hub: its program knows nothing of slots", self.out)
        self.assertIn("build/hub/hub_factory.bin once, over USB DFU", self.out)
        self.assertIn("done: 2 tiles now run version 0102\n"
                      "error: not updated, for what it says above: the hub\n", self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs(1, new.tile[1])
        self.check_old("h", other="none")
        self.assertEqual(self.typed("reboot"), ["reboot all"])
        self.assertEqual(self.run_script(new, "--check"), 1, self.out)
        self.assertIn("--check: nothing was changed\nerror: not updated", self.out)

    # -- runs that were cut off ---------------------------------------------

    def test_stopped_while_it_sends(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("fw %s write" % w.ids[1], stop, nth=30)
        self.assertEqual(self.run_script(new), 130, self.out)
        self.assertTrue(self.out.endswith("stopped\n" + update.NOT_RESTARTED + "\n"), self.out)
        self.assertEqual(self.typed("reboot"), [])
        self.check_console()
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertEqual(self.typed("fw %s begin" % w.ids[0]), [])
        self.assertEqual(len(self.typed("fw %s begin" % w.ids[1])), 1)
        self.assertEqual(self.typed("reboot"), ["reboot hub"])
        for i in (0, 1):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_stopped_before_the_restart(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("reboot", stop)
        self.assertEqual(self.run_script(new), 130, self.out)
        for n in (0, 1, "h"):
            self.check_old(n, other="new")
            self.assertEqual(self.chip(n).reboots, 0)
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertEqual([t for t in self.typed("fw") if " begin" in t or " write" in t], [])
        self.assertIn("the hub: runs version %s in slot A; holds version 0102 for slot B "
                      "already, not started yet" % FACTORY, self.out)
        for i in (0, 1):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])

    def stopped_after_the_restart(self, tiles=2):
        """A run that is cut off when the new programs have just started."""
        w = self.start(tiles)
        new = self.build(NEW)
        w.when("reboot hub", lambda: w.when("fw", stop))
        self.assertEqual(self.run_script(new), 130, self.out)
        self.assertTrue(self.out.endswith("stopped\n" + update.ON_TRIAL + "\n"), self.out)
        for i in range(tiles):
            self.check_runs(i, new.tile[1], state="tried")
        self.check_runs("h", new.hub[1], state="tried")
        return w, new

    def test_stopped_after_the_restart(self):
        w, new = self.stopped_after_the_restart()
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("tile %s: is trying this build (version 0102, slot B) and was not "
                      "confirmed yet" % w.ids[0], self.out)
        self.assertIn("tile %s: confirmed" % w.ids[0], self.out)
        self.assertIn("the hub: confirmed", self.out)
        self.assertIn("nothing to send: they all run this build", self.out)
        self.assertEqual(self.typed("reboot"), [])
        for i in (0, 1):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.assertEqual(self.chip("h").reboots, 1)
        w.run(MINUTE)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])

    def test_stopped_after_the_restart_and_left_alone(self):
        w, _ = self.stopped_after_the_restart()
        w.run(MINUTE)
        self.check_all_old(2)

    def test_check_confirms_nothing(self):
        w, new = self.stopped_after_the_restart(1)
        self.assertEqual(self.run_script(new, "--check"), 0, self.out)
        self.assertIn("the hub: is trying this build (version 0102, slot B)", self.out)
        self.assertEqual([t for t in self.typed("fw") if t.endswith("confirm")], [])
        w.run(MINUTE)
        self.check_all_old(1)

    def test_stopped_while_it_confirms(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("fw hub confirm", stop)
        self.assertEqual(self.run_script(new), 130, self.out)
        self.assertTrue(self.out.endswith("stopped\n" + update.HALF_CONFIRMED + "\n"), self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1], state="tried")
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("the hub: confirmed", self.out)
        self.check_runs("h", new.hub[1])

    def test_another_build_is_on_trial(self):
        w, _ = self.stopped_after_the_restart()
        third = self.build(NEXT)
        before = [self.chip(n) for n in (0, 1, "h")]
        self.assertEqual(self.run_script(third), 1, self.out)
        self.assertIn("tile %s: is trying a program that is not this build (version 0102, slot B)"
                      % w.ids[1], self.out)
        self.assertIn("error: tile %s, tile %s, the hub are trying a program that is not this "
                      "build" % tuple(w.ids), self.out)
        self.assertIn("Nothing was changed.", self.out)
        self.assertEqual([t for t in self.typed("fw") if len(t.split()) > 2], [])
        self.assertEqual([self.chip(n) for n in (0, 1, "h")], before)
        w.run(MINUTE)
        self.assertEqual(self.run_script(third), 0, self.out)
        for i in (0, 1):
            self.check_runs(i, third.tile[1])
        self.check_runs("h", third.hub[1])

    def test_a_program_of_another_build_that_waits_for_a_restart(self):
        w = self.start()
        second, third = self.build(NEW), self.build(NEXT)
        self.assertEqual(self.run_script(second), 0, self.out)
        w.when("reboot", stop)
        self.assertEqual(self.run_script(third), 130, self.out)
        self.assertEqual(self.chip(0).a, Slot("new", "0103", "%08x" % third.tile[0].crc))
        # the second build after all: what waits in slot A must not start
        self.assertEqual(self.run_script(second), 0, self.out)
        self.assertIn("tile %s: runs version 0102 in slot B, which is this build; holds another "
                      "program in slot A\n    that it would start at its next restart" % w.ids[0],
                      self.out)
        self.assertIn("tile %s: the other program it held is gone" % w.ids[0], self.out)
        self.assertIn("the hub: the other program it held is gone", self.out)
        self.assertEqual(self.typed("reboot"), [])
        for n in (0, "h"):
            self.assertEqual(self.chip(n).a.state, "partial")
            w.power_cycle(n)
        w.run(3000)
        self.check_runs(0, second.tile[1])
        self.check_runs("h", second.hub[1])
        self.assertEqual((self.chip(0).trial, self.chip("h").trial), (0, 0))


@unittest.skipUnless(os.path.exists(HUBSIM), "build/host/hubsim is not built")
class OwnPowerTest(Case):
    """Tiles that run on while the hub restarts. The boards are not built
    so, but no board has run any of this yet; and what the script does then
    is what it does for any tile that still runs its old program: it tells
    the tile to restart."""

    def start(self, tiles=1):
        return super().start(tiles, fed=False)

    def test_one_tile_and_the_hub(self):
        w = self.start()
        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.assertEqual((self.chip(0).reboots, self.chip("h").reboots), (1, 1))
        self.assertEqual(self.typed("reboot"), ["reboot hub", "reboot " + w.ids[0]])
        self.assertIn("the hub is back: piano hub, firmware", self.out)
        self.assertIn("restarting tile %s\n" % w.ids[0], self.out)

    def test_a_row_of_tiles(self):
        self.start(4)
        new = self.build(NEW)
        self.assertEqual(self.run_script(new), 0, self.out)
        for i in range(4):
            self.check_runs(i, new.tile[1])
            self.assertEqual(self.chip(i).reboots, 1)
        self.check_runs("h", new.hub[1])
        self.assertEqual(self.typed("reboot"), ["reboot hub", "reboot all"])
        self.assertIn("restarting the tiles\n", self.out)

    def test_tile_that_did_not_hear_the_restart(self):
        w = self.start(3)
        new = self.build(NEW)
        w.dead_from("reboot all", 2)            # the cable to the last tile
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("tile %s did not restart; telling it again" % w.ids[2], self.out)
        self.assertEqual(self.typed("reboot"), ["reboot hub", "reboot all", "reboot " + w.ids[2]])
        for i in range(3):
            self.check_runs(i, new.tile[1])
            self.assertEqual(self.chip(i).reboots, 1)
        self.check_runs("h", new.hub[1])

    def test_tile_from_before_the_bootloader_is_there_afterwards(self):
        # it keeps its power and its program, and after the hub's restart it
        # still cannot say what it runs: that it answers is enough
        w = self.start(2)
        new = self.build(NEW)
        w.pipe("u 1")
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("done: 1 tile now runs version 0102 and the hub now runs version 0102\n"
                      "error: not updated, for what it says above: tile %s\n" % w.ids[1],
                      self.out)
        self.assertEqual(self.typed("reboot"), ["reboot hub", "reboot " + w.ids[0]])
        self.check_runs(0, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.check_old(1, other="none")
        self.assertEqual(self.chip(1).reboots, 0)
        self.assertEqual(self.typed("fw %s begin" % w.ids[1]), [])

    def test_tile_from_before_the_bootloader_among_others(self):
        # two tiles wait to be restarted, and the third says a word where the
        # others say what they run
        w = self.start(3)
        new = self.build(NEW)
        w.pipe("u 2")
        self.assertEqual(self.run_script(new), 1, self.out)
        self.assertIn("done: 2 tiles now run version 0102 and the hub now runs version 0102\n"
                      "error: not updated, for what it says above: tile %s\n" % w.ids[2],
                      self.out)
        self.assertEqual(self.typed("reboot"), ["reboot hub", "reboot all"])
        for i in (0, 1):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])
        self.check_old(2, other="none")

    def test_tile_program_that_hangs(self):
        w = self.start(2)
        bad = self.build(NEW, tile=HANG)
        self.assertEqual(self.run_script(bad), 1, self.out)
        for i in (0, 1):
            self.assertIn("tile %s started its new program and is back in the old one" % w.ids[i],
                          self.out)
            self.assertEqual((self.chip(i).reboots, self.chip(i).wdt_resets), (1, 1))
        self.assertIn("Nothing was confirmed", self.out)
        self.check_runs("h", bad.hub[1], state="tried")
        w.run(MINUTE)
        for n in (0, 1, "h"):
            self.check_old(n, other="tried")

    def test_hub_program_that_hangs(self):
        w = self.start(2)
        bad = self.build(NEW, hub=HANG)
        self.assertEqual(self.run_script(bad), 1, self.out)
        self.assertIn("error: the hub started its new program and is back in the old one\n"
                      "Nothing was confirmed", self.out)
        self.assertEqual(self.typed("reboot"), ["reboot hub"])
        self.check_old("h", other="tried")
        # the tiles were not told to restart, and hold their program still
        w.run(MINUTE)
        for i in (0, 1):
            self.check_old(i, other="new")
            self.assertEqual(self.chip(i).reboots, 0)
        self.assertEqual(self.run_script(self.build(NEXT)), 0, self.out)

    def test_stopped_after_the_hub_restarted(self):
        w = self.start(2)
        new = self.build(NEW)
        w.when("reboot hub", lambda: w.when("fw", stop))
        self.assertEqual(self.run_script(new), 130, self.out)
        self.assertTrue(self.out.endswith("stopped\n" + update.ON_TRIAL + "\n"), self.out)
        self.check_runs("h", new.hub[1], state="tried")
        for i in (0, 1):
            self.check_old(i, other="new")
        self.assertEqual(self.run_script(new), 0, self.out)
        self.assertIn("the hub: confirmed", self.out)
        self.assertEqual(self.typed("reboot"), ["reboot all"])
        self.assertEqual([t for t in self.typed("fw") if " begin" in t], [])
        for i in (0, 1):
            self.check_runs(i, new.tile[1])
        self.check_runs("h", new.hub[1])


class FilesTest(Case):
    """Image files that are not what the script wants: it says so before it
    opens a port."""

    def refused(self, build, *args):
        out = io.StringIO()

        def no_port(name):
            raise AssertionError("the script opened a port")

        code = update.main(["--port", "fake", "--build", build.dir] + list(args),
                           open_port=no_port, stream=out)
        self.assertEqual(code, 1, out.getvalue())
        return out.getvalue()

    def path(self, build, name):
        return os.path.join(build.dir, name[:name.index("_")], name)

    def put(self, build, name, data):
        with open(self.path(build, name), "wb") as f:
            f.write(data)

    def test_no_build(self):
        build = Build(os.path.join(self.dir.name, "nothing"), None, None)
        out = self.refused(build)
        self.assertIn("error: cannot read %s: No such file or directory. `make tile` builds it"
                      % os.path.join(build.dir, "tile", "tile_a.img"), out)

    def test_one_file_missing(self):
        build = self.build(NEW)
        os.remove(self.path(build, "hub_b.img"))
        self.assertIn("cannot read %s" % self.path(build, "hub_b.img"), self.refused(build))
        self.assertIn("`make hub` builds it", self.refused(build, "--only", "hub"))

    def test_not_an_image(self):
        build = self.build(NEW)
        self.put(build, "tile_b.img", make_image(0, NEW, 1)[:-4])
        self.assertIn("error: %s is not a program image: " % self.path(build, "tile_b.img"),
                      self.refused(build))

    def test_the_other_kind(self):
        build = self.build(NEW)
        self.put(build, "tile_a.img", make_image(1, NEW, 0))
        self.assertIn("tile_a.img holds a hub program, not the tile's", self.refused(build))
        build = self.build(NEXT)
        self.put(build, "hub_b.img", make_image(0, NEXT, 1))
        self.assertIn("hub_b.img holds a tile program, not the hub's", self.refused(build))

    def test_the_other_slot(self):
        build = self.build(NEW)
        self.put(build, "tile_a.img", make_image(0, NEW, 1))
        self.assertIn("tile_a.img is linked for slot B", self.refused(build))

    def test_two_builds(self):
        build = self.build(NEW)
        self.put(build, "hub_b.img", make_image(1, NEXT, 1))
        self.assertIn("hub_a.img is version 0102 and %s is version 0103: not one build"
                      % self.path(build, "hub_b.img"), self.refused(build))


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class ScriptedPort:
    """A console that is not the simulator's: `answer` makes of a line what
    it says, or None for nothing."""

    def __init__(self, clock, answer):
        self.clock = clock
        self.answer = answer
        self.line = bytearray()
        self.lines = []
        self.out = bytearray()

    def write(self, data):
        for c in data:
            if c == 13:
                text = self.line.decode()
                self.line.clear()
                if text:
                    self.lines.append(text)
                    said = self.answer(text)
                    if said is not None:
                        self.out += "".join(t + "\r\n" for t in said).encode()
            elif c == 127:
                del self.line[-1:]
            else:
                self.line.append(c)

    def read(self, wait):
        if not self.out:
            self.clock.sleep(max(wait, 0.001))
        data = bytes(self.out)
        self.out.clear()
        return data

    def set_dtr(self, on):
        pass

    def close(self):
        pass


class ConsoleTest(Case):
    """Consoles that the simulator's hub does not have."""

    VERSION = "piano hub, firmware 0001, protocol 1, id 0badcafe"

    def run_with(self, answer, *args, set_dtr=None):
        clock = Clock()
        self.port = ScriptedPort(clock, answer)
        if set_dtr:
            self.port.set_dtr = set_dtr
        out = io.StringIO()
        build = self.build(NEW)
        with mock.patch.object(flash_tile, "now", clock.now), \
                mock.patch.object(flash_tile, "sleep", clock.sleep):
            code = update.main(["--port", "fake", "--build", build.dir] + list(args),
                               open_port=lambda name: self.port, stream=out)
        self.out = out.getvalue()
        return code

    def old_hub(self, text):
        """The hub's console as it was before programs went through the hub."""
        if text == "version":
            return [self.VERSION, "ok"]
        if text == "ping nobody":
            return ["error: no such tile"]
        if text == "tiles":
            return ["0 11223344 at 0,0 parent W fw 0001", "1 tile", "ok"]
        return ["error: no such command (try help)"]

    def test_hub_from_before_there_was_fw(self):
        self.assertEqual(self.run_with(self.old_hub), 1, self.out)
        self.assertIn("error: the hub's console has no `fw` command", self.out)
        self.assertIn("build/hub/hub_factory.bin once, over USB\nDFU", self.out)
        self.assertEqual(self.port.lines[-1], "fw 11223344")

    def test_not_the_hub(self):
        self.assertEqual(self.run_with(lambda text: ["what?"]), 1, self.out)
        self.assertIn("error: the serial port does not answer like the hub's console", self.out)

    def test_port_that_goes_as_it_is_opened(self):
        def gone(on):
            raise OSError(5, "Input/output error")      # as the ioctl does

        self.assertEqual(self.run_with(lambda text: None, set_dtr=gone), 1, self.out)
        self.assertEqual(self.out, "error: the hub's serial port has gone (Input/output error)\n")

    def test_console_that_stops_answering(self):
        def answer(text):
            return None if text.startswith("fw") else self.old_hub(text)

        self.assertEqual(self.run_with(answer), 1, self.out)
        self.assertIn("error: the hub's console stopped answering", self.out)

    def test_an_answer_it_does_not_know(self):
        def answer(text):
            return ["error: out of cheese"] if text.startswith("fw") else self.old_hub(text)

        self.assertEqual(self.run_with(answer), 1, self.out)
        self.assertIn("error: the hub answered `fw 11223344` with: out of cheese", self.out)

    def test_a_tile_that_says_something_odd(self):
        def answer(text):
            if text.startswith("fw"):
                return ["tile 0: runs c good 0001 12345678, other none -", "ok"]
            return self.old_hub(text)

        self.assertEqual(self.run_with(answer), 1, self.out)
        self.assertIn("error: tile 11223344 does not say what it runs (odd reply). Nothing was "
                      "changed.", self.out)


class PiecesTest(unittest.TestCase):
    def test_what_a_node_says_it_runs(self):
        fw = update.parse_fw(["tile 3: runs a none 0001 0a1b2c3d, other none -"])
        self.assertEqual(fw, update.Fw(0, "none", 1, 0x0A1B2C3D, "none", None, None, False))
        fw = update.parse_fw(["hub: runs b tried 0102 00000001, other good ffffffff"])
        self.assertEqual(fw, update.Fw(1, "tried", 0x102, 1, "good", 0xFFFFFFFF, None, False))
        fw = update.parse_fw(["tile 12: runs a good 0001 0a1b2c3d, other partial -, "
                              "receiving at 2016"])
        self.assertEqual((fw.other, fw.other_crc, fw.at, fw.staged), ("partial", None, 2016, False))
        fw = update.parse_fw(["tile 0: runs a good 0001 0a1b2c3d, other new 00c0ffee, staged"])
        self.assertEqual((fw.other, fw.other_crc, fw.at, fw.staged), ("new", 0xC0FFEE, None, True))
        self.assertIsNone(update.parse_fw(["tile 0: runs a good 0001 0a1b2c3d"]))
        self.assertIsNone(update.parse_fw([]))

    def test_plan(self):
        def image(slot, crc):
            return update.Program("x", b"", mkimage.Image(0, 2, mkimage.SLOT_ADDR[slot], slot,
                                                         3004, crc))

        pair = [image(0, 0xA), image(1, 0xB)]

        def plan(slot, state, crc, other, other_crc):
            node = update.Node("11223344", pair)
            node.fw = update.Fw(slot, state, 1, crc, other, other_crc, None, False)
            return update.plan(node), node.new

        self.assertEqual(plan(0, "none", 0x1, "none", None), ("send", pair[1]))
        self.assertEqual(plan(1, "good", 0x1, "good", 0x2), ("send", pair[0]))
        self.assertEqual(plan(1, "good", 0x1, "good", 0xA), ("send", pair[0]))     # not NEW: it
        self.assertEqual(plan(1, "good", 0x1, "tried", 0xA), ("send", pair[0]))    # would not start
        self.assertEqual(plan(0, "good", 0x1, "new", 0xB), ("staged", pair[1]))
        self.assertEqual(plan(0, "good", 0x1, "new", 0xA), ("send", pair[1]))      # A's, in B
        self.assertEqual(plan(0, "good", 0xB, "none", None), ("send", pair[1]))    # B's, in A
        self.assertEqual(plan(0, "good", 0xA, "good", 0x1), ("nothing", None))
        self.assertEqual(plan(1, "none", 0xB, "partial", None), ("nothing", None))
        self.assertEqual(plan(1, "tried", 0xB, "good", 0x1), ("confirm", None))
        self.assertEqual(plan(1, "good", 0xB, "new", 0x1), ("withdraw", None))
        self.assertEqual(plan(1, "tried", 0x1, "good", 0x2), ("trial", None))
        self.assertEqual(plan(1, "tried", 0x1, "good", 0xA), ("trial", None))

    def test_help(self):
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out), self.assertRaises(SystemExit) as e:
            update.main(["--help"])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("--only {tiles,hub}", out.getvalue())


if __name__ == "__main__":
    unittest.main()
