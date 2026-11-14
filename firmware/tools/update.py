#!/usr/bin/env python3
"""Give every tile and the hub a new program, through the hub.

    python3 tools/update.py              what `make tile hub` built, to all of them
    python3 tools/update.py --check      only looks: who runs what, who would get what
    python3 tools/update.py --only tiles     (or: --only hub)
    python3 tools/update.py --help       the other options

Every chip, a tile's and the hub's, holds a bootloader and two slots for its
program (core/fw.h). The program that runs takes a new one, a tile over the
cables and the hub over its USB, and puts it into the slot it does not run
from. So nothing here needs a chip's ROM, a tile can be anywhere among the
tiles, and the program that runs is never the one being written.

NOT TRIED ON HARDWARE. tests/test_update.py (`make test-tools`) runs it
against the hub's and the tiles' own programs in the simulator
(tests/hubsim.c), with each node's flash and the bootloader's choice of slot.
No real hub and no real tile has seen it.

What it does:

 1. Finds the hub's serial port as flash_tile.py does, asks the hub for its
    tiles, and asks every tile and the hub what it runs (`fw <tile>`,
    `fw hub`).
 2. Reads the images: build/tile/tile_a.img and tile_b.img, build/hub/hub_a.img
    and hub_b.img. A program is built once for each slot, and a node gets
    the one for the slot it does not run from. A node that runs this build
    already gets nothing.
 3. Sends each node its image, 48 bytes to a console line (`fw <tile> begin`,
    `write`, `commit`). The node checks the image's checksum before it takes
    it for a program. Up to here nothing has changed in what runs.
 4. Restarts what holds a new program; each bootloader starts it, on trial.
    If the hub is among them, that is one command, `reboot hub`: the tiles
    have their power through the hub, whose two switches are off while its
    chip starts, so every tile restarts with it. Then the hub's serial port
    is opened again. If only tiles got a program, the hub stays as it is
    and tells them (`reboot all`).
 5. Asks every node again; a tile that still runs its old program with the
    new one waiting is told to restart, by name. If each runs its new
    program, and every tile that was there before is there again, it
    confirms them (`fw <tile> confirm`): the new programs stay.

If something is wrong after the restart, nothing is confirmed, and that is
the way back. A program on trial that hears no confirm for a minute
restarts, and the bootloader does not start it a second time; one that hangs
is restarted by its watchdog after four seconds. So a new program that does
not talk on the cable, or a hub's whose console does not work, is gone again
a minute later without anybody doing anything. A hub that goes back takes
its tiles' power away as it restarts, and so every tile that was on trial
is back in its old program with it.

For the same reason a tile on trial is back in its old program as soon as
its power goes: unplug the hub during that minute, and nothing is new.

A run that was cut off, wherever, is taken up by the next one: an image that
a node holds whole is not sent again, and a node that is trying this build's
program is confirmed.

What it cannot do is reach a chip without our bootloader: a blank one, or one
that was flashed with a plain program. Such a chip's first program is a
factory file through its ROM (a tile: tools/flash_tile.py; the hub: USB DFU
with build/hub/hub_factory.bin). The script says which nodes those are,
updates the others, and ends with an error.

Needs Python 3.8 and what flash_tile.py needs.
"""

import argparse
import collections
import itertools
import os
import re
import sys

import flash_tile
import mkimage
from flash_tile import Failure, HubError, NoAnswer

CHUNK = 48              # core/fw.h: FW_CHUNK, the most one write carries
TRIAL = 60.0            # core/fw.h: FW_TRIAL_MS

# Seconds.
ASK_WAIT = 4.0          # the hub gives a tile half a second, and a sector's erase comes on top
LOOK_WAIT = 4.0         # a tile that is silent for 3 s leaves the hub's list
LIST_WAIT = 6.0         # a hub that has only just got its power is still finding its tiles
LIST_SAME = 1.0         # ... and has found them when the list stays as it is for this long
NOBODY_WAIT = 2.0
RESTART_WAIT = 1.0      # a tile restarts 250 ms after the command, which the hub
                        # may still be repeating 400 ms after it first said it
BACK_WAIT = 15.0        # the tiles find the hub again, each ring of them after the one before
HUB_BACK_WAIT = TRIAL + 15.0    # a hub program without a console is replaced after TRIAL
HUB_SLOW = 8.0          # a restart and USB take two or three
CONFIRM_BY = TRIAL - 15.0       # after the restart; later, a confirm would race the trial's end

SILENT_MAX = 5          # times in a row a node may not answer while it is sent a program
FAILS_MAX = 8           # times in a row it may refuse
BEGIN_MAX = 3           # times one image is begun
KICKS = 2               # times a tile that did not restart is told again

KIND = {"tile": 0, "hub": 1}            # core/proto.h: KIND_TILE, KIND_HUB

OLD_HUB = """\
the hub's console has no `fw` command: its program is from before programs
went through the hub. The hub needs build/hub/hub_factory.bin once, over USB
DFU (firmware/README.md). After that this script reaches it and every tile."""

NO_SLOTS_TILE = """\
its program knows nothing of slots: the chip was flashed without the
    bootloader, and takes no program through the hub. tools/flash_tile.py gives
    it build/tile/tile_factory.bin, with the hub's cable in this tile's W or S port"""

NO_SLOTS_HUB = """\
its program knows nothing of slots: the chip was flashed without the
    bootloader, and takes no program this way. It needs
    build/hub/hub_factory.bin once, over USB DFU (firmware/README.md)"""

NOT_RESTARTED = """\
No node was restarted: each still runs what it ran. What was sent stays where
it is, and the next run goes on from there. (A node that restarts before then
tries its new program for a minute and goes back to the old one.)"""

NOT_CONFIRMED = """\
Nothing was confirmed: every new program that was started goes back to the one
before it by itself, within a minute of its start. After that, run this again;
--check shows who runs what."""

ON_TRIAL = """\
The nodes that restarted are trying their new programs, and none was confirmed.
Run this again within the minute and it confirms them; if not, each goes back
to its old program by itself."""

HALF_CONFIRMED = """\
A node that was not confirmed goes back to its old program by itself, within a
minute of its restart; the confirmed ones keep the new one. Run this again: it
confirms a node that is still trying, and sends the others their program again."""

HUB_NOT_BACK = """\
the hub did not come back after its restart ({why}).
Unplug it and plug it in again: its bootloader does not start a second time a
program that was not confirmed, so the one from before runs then. If the hub
stays away all the same, build/hub/hub_factory.bin over USB DFU is what is left
(firmware/README.md)."""


def now():
    return flash_tile.now()             # looked up each time: the test puts its clock there


def sleep(seconds):
    flash_tile.sleep(seconds)


# ------------------------------------------------------------- the images ---

Program = collections.namedtuple("Program", "path data image")


def load_pair(build, kind):
    """A node's program as the Makefile left it: the image for slot A and
    the one for slot B."""
    pair = []
    for slot in (0, 1):
        path = os.path.join(build, kind, f"{kind}_{'ab'[slot]}.img")
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise Failure(f"cannot read {path}: {e.strerror}. `make {kind}` builds it; "
                          "--build names another build directory") from None
        try:
            image = mkimage.parse_image(data)
        except mkimage.Bad as e:
            raise Failure(f"{path} is not a program image: {e}") from None
        if image.kind != KIND[kind]:
            raise Failure(f"{path} holds a {mkimage.kind_name(image.kind)} program, "
                          f"not the {kind}'s")
        if image.slot != slot:
            raise Failure(f"{path} is linked for slot {mkimage.slot_name(image.slot)}")
        pair.append(Program(path, data, image))
    if pair[0].image.version != pair[1].image.version:
        raise Failure(f"{pair[0].path} is version {pair[0].image.version:04x} and "
                      f"{pair[1].path} is version {pair[1].image.version:04x}: "
                      f"not one build. `make {kind}` builds both")
    return pair


# ------------------------------------------------------------ the console ---

Fw = collections.namedtuple("Fw", "slot state version crc other other_crc at staged")
Answer = collections.namedtuple("Answer", "status at lines")

# core/console.c: show_fw(), console_reply(), fw_hub() and cmd_tiles()
FW_LINE = re.compile(r"(?:tile \d+|hub): runs ([ab]) (\w+) ([0-9a-f]{4}) ([0-9a-f]{8}), "
                     r"other (\w+) ([0-9a-f]{8}|-)(?:, receiving at (\d+))?(, staged)?")
SAID = re.compile(r"(?:tile (?:\d+|[0-9a-f]{8})|hub): (.+?)(?:, at (\d+))?")
AT_LINE = re.compile(r"(?:tile \d+|hub): at (\d+)")
TILE_LINE = re.compile(r"\d+ ([0-9a-f]{8}) ")


class Session:
    """The hub's serial port, which is another one once the hub has restarted."""

    def __init__(self, opt, out, open_port, find_hub):
        self.opt = opt
        self.out = out
        self.open_port = open_port
        self.find_hub = find_hub
        self.port = None
        self.hub = None
        self.phase = "look"             # what a stop from here on leaves behind

    def open(self):
        """Open the port; the console's `version` line."""
        self.port = self.open_port(self.opt.port or self.find_hub())
        self.hub = flash_tile.Hub(self.port, self.out)
        try:
            return self.hub.wake()
        except OSError as e:            # setting DTR on a port that went with the hub
            raise Failure(f"the hub's serial port has gone ({e.strerror})") from None

    def close(self):
        port, self.port, self.hub = self.port, None, None
        if port is not None:
            try:
                port.close()
            except OSError:
                pass                    # it went with the hub


def tell(hub, command):
    """One console command, and what the node it is for made of it: `ok`,
    or the console's words for what went wrong. `at`: where the node wants a
    program's next bytes from, if it said."""
    try:
        lines = hub.ask(command, ASK_WAIT)
    except NoAnswer:
        raise Failure("the hub's console stopped answering") from None
    except HubError as e:
        if e.text == "no such tile":
            return Answer("gone", None, e.lines)
        if e.text.startswith("no such command"):
            raise Failure(OLD_HUB) from None
        if re.fullmatch(r"\d+ failed", e.text):
            for text in e.lines:
                m = SAID.fullmatch(text)
                if m:
                    return Answer(m.group(1), None if m.group(2) is None else int(m.group(2)),
                                  e.lines)
        short = command if len(command) <= 40 else command[:37] + "..."
        raise Failure(f"the hub answered `{short}` with: "
                      + "; ".join(e.lines + [e.text])) from None
    at = None
    for text in lines:
        m = AT_LINE.fullmatch(text)
        if m:
            at = int(m.group(1))
    return Answer("ok", at, lines)


def parse_fw(lines):
    for text in lines:
        m = FW_LINE.fullmatch(text)
        if m:
            return Fw("ab".index(m.group(1)), m.group(2), int(m.group(3), 16), int(m.group(4), 16),
                      m.group(5), None if m.group(6) == "-" else int(m.group(6), 16),
                      None if m.group(7) is None else int(m.group(7)), m.group(8) is not None)
    return None


def what_runs(s, node, wait=0.0):
    """`fw <node>`: (what it runs, "ok"), or (None, why it does not say).
    A node that gives no answer is asked again for `wait` seconds."""
    end = now() + wait
    while True:
        a = tell(s.hub, f"fw {node.who}")
        if a.status == "ok":
            fw = parse_fw(a.lines)
            return fw, "ok" if fw else "odd reply"
        if a.status != "no answer" or now() >= end:
            return None, a.status
        sleep(0.25)


def list_tiles(hub):
    """The IDs of the tiles the hub knows."""
    try:
        lines = hub.ask("tiles")
    except NoAnswer:
        raise Failure("the hub's console stopped answering") from None
    except HubError as e:
        raise Failure(f"the hub answered `tiles` with: {e.text}") from None
    return [m.group(1) for m in map(TILE_LINE.match, lines) if m]


def settled_tiles(hub):
    """list_tiles, with some patience: a hub that has only just got its
    power is still finding its tiles, one ring of them after the other. The
    list counts once it has stayed as it is for a second."""
    began = same = now()
    before = None
    while True:
        tiles = list_tiles(hub)
        if tiles != before:
            before, same = tiles, now()
        elif tiles and now() - same >= LIST_SAME:
            return tiles
        if now() - began >= (LIST_WAIT if tiles else NOBODY_WAIT):
            return tiles
        sleep(0.25)


# -------------------------------------------------------------- the nodes ---

class Node:
    """A tile or the hub."""

    def __init__(self, who, pair):
        self.who = who                  # as the console calls it: a tile's ID, or hub
        self.name = "the hub" if who == "hub" else f"tile {who}"
        self.pair = pair                # its program for slot A and for slot B; None: not
                                        # this run's business (--only)
        self.fw = None                  # what it said it runs, before
        self.todo = None                # nothing, confirm, withdraw, trial, staged, send
        self.new = None                 # the image it is to run
        self.seen = None                # after the restart: an Fw, or a word


def plan(node):
    """What a node needs, from what it said it runs."""
    fw = node.fw
    here, there = node.pair[fw.slot], node.pair[1 - fw.slot]
    if fw.crc == here.image.crc:
        if fw.state == "tried":
            return "confirm"
        if fw.other == "new":
            return "withdraw"
        return "nothing"
    if fw.state == "tried":
        return "trial"
    node.new = there
    if fw.other == "new" and fw.other_crc == there.image.crc:
        return "staged"
    return "send"


def look(s, node, left_out):
    """Ask a node what it runs, say it, and plan. False: the tile has gone."""
    out, name = s.out, node.name
    fw, status = what_runs(s, node, LOOK_WAIT)
    if fw is None:
        if status == "does not know that":
            out.say(f"{name}: " + (NO_SLOTS_HUB if node.who == "hub" else NO_SLOTS_TILE))
            left_out.append(name)
            return True
        if status == "gone":
            out.say(f"{name}: was in the hub's list and has left it")
            left_out.append(name)
            return False
        raise Failure(f"{name} does not say what it runs ({status}). Nothing was changed.")
    node.fw = fw
    node.todo = plan(node)

    slot, other = mkimage.slot_name(fw.slot), mkimage.slot_name(1 - fw.slot)
    runs = f"runs version {fw.version:04x} in slot {slot}"
    if node.todo == "nothing":
        out.say(f"{name}: {runs}, which is this build")
    elif node.todo == "confirm":
        out.say(f"{name}: is trying this build (version {fw.version:04x}, slot {slot}) "
                "and was not confirmed yet")
    elif node.todo == "withdraw":
        out.say(f"{name}: {runs}, which is this build; holds another program in slot {other}\n"
                "    that it would start at its next restart")
    elif node.todo == "trial":
        out.say(f"{name}: is trying a program that is not this build (version "
                f"{fw.version:04x}, slot {slot})")
    else:
        new = f"version {node.new.image.version:04x}"
        if node.todo == "staged":
            out.say(f"{name}: {runs}; holds {new} for slot {other} already, not started yet")
        elif fw.other == "tried" and fw.other_crc == node.new.image.crc:
            out.say(f"{name}: {runs}; gets {new} into slot {other} again: it was started "
                    "there before\n    and did not stay")
        else:
            out.say(f"{name}: {runs}; gets {new} into slot {other}")
    return True


# ---------------------------------------------------------------- sending ---

def send(s, node):
    """Put node.new into the slot the node does not run from, and see that
    the node holds it: whole, checked, and not started."""
    out, who = s.out, node.who
    data, image = node.new.data, node.new.image
    total = len(data)
    out.say(f"sending {node.name} version {image.version:04x} for its slot "
            f"{mkimage.slot_name(image.slot)}: {total} bytes")

    at = None               # where the node wants the next bytes from; None: it must begin
    begins = silent = fails = 0
    left = 3 * (total // CHUNK) + 50    # commands: enough for three times over
    while True:
        if at is None:
            if begins == BEGIN_MAX:
                raise Failure(f"{node.name} did not take its new program, begun "
                              f"{BEGIN_MAX} times")
            begins += 1
            command = f"fw {who} begin {total}"
        elif at < total:
            command = f"fw {who} write {at} {data[at:at + CHUNK].hex()}"
        else:
            command = f"fw {who} commit"
        if left == 0:
            raise Failure(f"{node.name} keeps sending its new program back to an earlier "
                          "place")
        left -= 1

        a = tell(s.hub, command)
        if a.status == "ok":
            silent = fails = 0
            if at is None:
                at = 0
            elif at == total:
                break
            elif a.at is None or not at < a.at <= total or a.at % 4:
                raise Failure(f"{node.name} took the bytes at {at} and wants to go on "
                              f"at {a.at}")
            else:
                at = a.at
                out.progress(f"{node.name}: {at} of {total} bytes")
            continue

        if a.status == "no answer":
            # the hub asked five times. If the node did hear, saying it
            # again does no harm: it answers where it is
            silent += 1
            if silent > SILENT_MAX:
                raise Failure(f"{node.name} stopped answering while it was sent its "
                              "new program")
            continue
        silent = 0
        fails += 1
        if fails > FAILS_MAX:
            raise Failure(f"{node.name} does not take its new program: {a.status}, "
                          f"{FAILS_MAX} times and more")
        if (a.status == "value not allowed" and at is not None and a.at is not None
                and a.at <= total and a.at % 4 == 0):
            at = a.at                   # it is somewhere else: an answer got lost
        elif a.status == "failed":
            # Its flash refused something. After an erase that failed the
            # node is where it was; after a write that failed, or an image
            # that did not pass the check at its end, it wants all of it
            # again. It says which.
            fw, _ = what_runs(s, node)
            at = fw.at if fw else None
        elif a.status == "gone":
            raise Failure(f"{node.name} left the hub's list while it was sent its new program")
        else:
            raise Failure(f"{node.name} refused its new program: {a.status}")

    fw, status = what_runs(s, node, LOOK_WAIT)
    if not (fw and fw.other == "new" and fw.other_crc == image.crc):
        raise Failure(f"{node.name} took its new program and does not hold it afterwards ("
                      + (status if fw is None else f"it runs from slot "
                         f"{mkimage.slot_name(fw.slot)}, and its other slot is {fw.other}") + ")")


def withdraw(s, node):
    """The node holds a program that is not this build's and would start it
    at its next restart. Beginning another in its place takes that away; a
    slot with half a program is never started."""
    length = node.pair[1 - node.fw.slot].image.length
    a = tell(s.hub, f"fw {node.who} begin {length}")
    for _ in range(SILENT_MAX):
        if a.status != "no answer":
            break
        a = tell(s.hub, f"fw {node.who} begin {length}")
    if a.status != "ok":
        raise Failure(f"{node.name} did not give up the other program it holds: {a.status}")
    s.out.say(f"{node.name}: the other program it held is gone")


def confirm(s, node, crc):
    """Tell a node on trial that its program stays, and see that it took
    that. None, or what went wrong."""
    for _ in range(SILENT_MAX):
        a = tell(s.hub, f"fw {node.who} confirm")
        if a.status != "no answer":
            break
    if a.status != "ok":
        return a.status
    fw, status = what_runs(s, node, LOOK_WAIT)
    if fw is None:
        return f"it does not say what it runs now ({status})"
    if fw.crc != crc:
        return "it has gone back to the program before"
    if fw.state != "good":
        return f"its program is still marked {fw.state}"
    return None


# ------------------------------------------------------------ the restart ---

def reopen(s, said):
    """The hub's serial port again after the hub restarted; its `version`
    line. A new program that does not get as far as its console is replaced
    by the old one after TRIAL, or after four seconds if it hangs: that long
    is waited."""
    told = False
    while True:
        try:
            return s.open()
        except Failure as e:
            why = str(e).split("\n")[0]
        s.close()
        if now() - said >= HUB_BACK_WAIT:
            raise Failure(HUB_NOT_BACK.format(why=why))
        if not told and now() - said >= HUB_SLOW:
            s.out.say("the hub is not back yet. If its new program does not get as far as its\n"
                      "console, the old one is started again within a minute: waiting for that")
            told = True
        s.out.progress("waiting for the hub")
        sleep(0.5)


def restart_hub(s, tiles):
    """Restart the hub, which holds a new program. When it was told.

    The tiles restart with it, and nobody has to tell them: their power
    comes through the hub's two switches, and those are off while the hub's
    chip starts. A tile that holds a new program starts it then. (And one
    that was on trial would be back in its old program: so the hub goes
    first, never after the tiles.)"""
    out = s.out
    with_it = ""
    if tiles:
        with_it = (", and the tile with it: its power comes" if len(tiles) == 1 else
                   ", and the tiles with it: their power comes") + " through the hub"
    out.say("restarting the hub" + with_it)
    a = tell(s.hub, "reboot hub")
    if a.status != "ok":
        raise Failure(f"the hub refused `reboot hub`: {a.status}")
    said = now()
    s.close()                   # at once: held open, the port would keep its name,
    sleep(RESTART_WAIT)         # and the hub would come back under another
    out.say("the hub is back: " + reopen(s, said))
    return said


def poll(s, tiles):
    """Ask every tile what it runs, until each has said or BACK_WAIT is over."""
    end = now() + BACK_WAIT
    for t in tiles:
        t.seen = None
    left = list(tiles)
    while True:
        for t in list(left):
            a = tell(s.hub, f"fw {t.who}")
            if a.status == "ok":
                t.seen = parse_fw(a.lines) or "odd reply"
            elif a.status == "does not know that":
                t.seen = a.status
            else:
                continue                # not there yet
            left.remove(t)
        if not left or now() >= end:
            return
        s.out.progress(f"waiting for {len(left)} tile{'' if len(left) == 1 else 's'}")
        sleep(0.25)


SAME_AS_BEFORE = "still runs its old program: it did not restart"


def verdict(node):
    """What is wrong with a node after the restart, or None."""
    seen = node.seen
    if seen is None:
        return "did not come back"
    if node.new is None:
        return None                     # it got nothing; it only has to be there
    if not isinstance(seen, Fw):
        return f"does not say what it runs ({seen})"
    if seen.crc == node.new.image.crc:
        return None
    if seen.other == "new":
        return SAME_AS_BEFORE
    if seen.other == "tried":
        return "started its new program and is back in the old one"
    return (f"runs neither its old program nor the new one (slot "
            f"{mkimage.slot_name(seen.slot)}, version {seen.version:04x})")


def on_trial(node):
    return isinstance(node.seen, Fw) and node.seen.state == "tried"


def settle(s, tiles):
    """Every tile's verdict, once each has started what it holds. A tile
    that still runs its old program with the new one waiting is told to
    restart, and KICKS times again if it does not. Without a hub restart
    before, that is every tile that got a program; after one, a tile that
    kept its power when the hub's went."""
    out = s.out
    for wave in itertools.count():
        poll(s, tiles)
        late = [t for t in tiles if verdict(t) is SAME_AS_BEFORE]
        if not late or wave > KICKS:    # told once, and KICKS times again
            return
        if wave == 0 and len(late) > 1 and not any(on_trial(t) for t in tiles):
            out.say("restarting the tiles")
            tell(s.hub, "reboot all")           # not heard: told again, by name
        else:
            # By name, also the first time, while some tile is on trial:
            # `reboot all` would restart that one too, and a program on
            # trial is not started twice.
            for t in late:
                out.say(f"restarting {t.name}" if wave == 0 else
                        f"{t.name} did not restart; telling it again")
                # `busy`: a tile on trial takes no restart in its first
                # second, so it has just restarted after all
                tell(s.hub, f"reboot {t.who}")
        sleep(RESTART_WAIT)


# ---------------------------------------------------------------- the run ---

def count(n, what):
    return f"{n} {what}{'' if n == 1 else 's'}"


def update(s):
    opt, out = s.opt, s.out
    tile_pair = load_pair(opt.build, "tile") if opt.only != "hub" else None
    hub_pair = load_pair(opt.build, "hub") if opt.only != "tiles" else None

    out.say("hub: " + s.open())
    flash_tile.wait_talking(s.hub, out)
    for kind, pair in (("tile", tile_pair), ("hub", hub_pair)):
        if pair:
            out.say(f"the {kind}'s program in {os.path.dirname(pair[0].path)}: version "
                    f"{pair[0].image.version:04x}, {pair[0].image.length} bytes")

    # -- who runs what
    left_out = []
    tiles = [Node(tile_id, tile_pair) for tile_id in settled_tiles(s.hub)]
    if not tiles:
        out.say("the hub sees no tiles")
    elif tile_pair is None:
        out.say(f"the hub sees {count(len(tiles), 'tile')}")
    else:
        tiles = [t for t in tiles if look(s, t, left_out)]
    hub = Node("hub", hub_pair)
    if hub_pair:
        look(s, hub, left_out)
    nodes = tiles + [hub]

    def done(text):
        out.say(text)
        if left_out:
            raise Failure("not updated, for what it says above: " + ", ".join(left_out))

    if opt.check:
        return done("--check: nothing was changed")
    trying = [n.name for n in nodes if n.todo == "trial"]
    if trying:
        raise Failure(", ".join(trying) + (" is" if len(trying) == 1 else " are")
                      + " trying a program that is not this build, and nobody confirmed it.\n"
                      "It goes back to the program before within a minute; run this again "
                      "then.\n(`fw <tile> confirm` on the hub's console would keep the program "
                      "on trial.)\nNothing was changed.")

    # -- what an earlier run left behind
    s.phase = "send"
    for node in nodes:
        if node.todo == "confirm":
            why = confirm(s, node, node.fw.crc)
            if why:
                raise Failure(f"{node.name} was trying this build and could not be "
                              f"confirmed: {why}.\nRun this again.")
            out.say(f"{node.name}: confirmed")
        elif node.todo == "withdraw":
            withdraw(s, node)

    # -- the new programs, into the slots that do not run
    fresh = [n for n in nodes if n.new]
    if not fresh:
        asked = sum(1 for n in nodes if n.fw)
        return done("nothing to send" + (": it runs this build" if asked == 1 else
                                         ": they all run this build" if asked else ""))
    try:
        for node in fresh:
            if node.todo == "send":
                send(s, node)
    except Failure as e:
        raise Failure(f"{e}\n{NOT_RESTARTED}") from None

    # -- the restart, and whether the new programs do
    s.phase = "restart"
    try:
        said = now()                    # no trial began before this
        if hub.new:
            said = restart_hub(s, tiles)
            hub.seen, status = what_runs(s, hub, LOOK_WAIT)
            if hub.seen is None:
                hub.seen = status
            why = verdict(hub)
            if why:
                raise Failure(f"the hub {why}")
        settle(s, tiles)
        wrong = [f"{t.name} {verdict(t)}" for t in tiles if verdict(t)]
        if wrong:
            raise Failure("\n".join(wrong))
        if now() - said > CONFIRM_BY:
            raise Failure(f"the new programs run, but it took {now() - said:.0f} s to see "
                          "that: too close to the\nend of their trial to confirm them all in "
                          "time")
    except Failure as e:
        raise Failure(f"{e}\n{NOT_CONFIRMED}") from None

    # -- they stay
    s.phase = "confirm"
    wrong = []
    try:
        for node in fresh:
            why = confirm(s, node, node.new.image.crc)
            if why:
                wrong.append(f"{node.name} could not be confirmed: {why}")
    except Failure as e:
        wrong.append(str(e))
    if wrong:
        raise Failure("\n".join(wrong) + "\n" + HALF_CONFIRMED)
    s.phase = "look"
    new_tiles = [t for t in tiles if t.new]
    parts = []
    if new_tiles:
        parts.append(f"{count(len(new_tiles), 'tile')} now "
                     f"run{'s' if len(new_tiles) == 1 else ''} version "
                     f"{tile_pair[0].image.version:04x}")
    if hub.new:
        parts.append(f"the hub now runs version {hub_pair[0].image.version:04x}")
    done("done: " + " and ".join(parts))


def parse(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    build = os.path.normpath(os.path.join(here, "..", "build"))
    try:
        build = os.path.relpath(build)
    except ValueError:
        pass                            # another drive
    p = argparse.ArgumentParser(
        description="Give every tile and the hub a new program, through the hub.",
        epilog="The top of this file says what it does, step by step, and what has "
               "not been tried.")
    p.add_argument("--build", default=build, metavar="DIR",
                   help="where `make tile hub` put the images: DIR/tile/tile_a.img and "
                        f"tile_b.img, DIR/hub/hub_a.img and hub_b.img (default: {build})")
    p.add_argument("--only", choices=["tiles", "hub"],
                   help="update the tiles and not the hub, or the hub and not the tiles")
    p.add_argument("--port", help="the hub's serial port (default: found by its USB ID)")
    p.add_argument("--check", action="store_true",
                   help="say what every node runs and what it would get, and change nothing")
    p.add_argument("-v", "--verbose", action="store_true", help="show every console line")
    return p.parse_args(argv)


STOPPED = {
    "look": "stopped",
    "send": "stopped\n" + NOT_RESTARTED,
    "restart": "stopped\n" + ON_TRIAL,
    "confirm": "stopped\n" + HALF_CONFIRMED,
}


def main(argv=None, open_port=None, find_hub=None, stream=None):
    opt = parse(argv)
    out = flash_tile.Out(stream or sys.stdout, opt.verbose)
    s = Session(opt, out, open_port or flash_tile.open_port, find_hub or flash_tile.find_hub)
    try:
        try:
            update(s)
        finally:
            s.close()
    except Failure as e:
        out.say(f"error: {e}")
        return 1
    except KeyboardInterrupt:
        out.say(STOPPED[s.phase])
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
