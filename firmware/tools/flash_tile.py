#!/usr/bin/env python3
"""Flash a tile through the hub.

A tile has no USB socket. Its chip has a bootloader in ROM that takes a
program over a UART, and the hub's console can become a plain UART on the
hub's cable (`bridge`). This script drives both:

    python3 tools/flash_tile.py              flashes build/tile/tile_factory.bin
    python3 tools/flash_tile.py other.bin
    python3 tools/flash_tile.py --check      only looks: the hub, the tile, the file
    python3 tools/flash_tile.py --rom        for a tile put into its ROM by hand
    python3 tools/flash_tile.py --help       the other options

This is for a tile's first program, and for a tile beyond other help. A
tile that runs gets a new program from tools/update.py, which needs neither
the ROM nor the tile on the hub's own cable, and which a program that turns
out not to work cannot lock out.

NOT TRIED ON HARDWARE. tests/test_flash_tile.py (`make test-tools`) runs it
against the hub's own program in the simulator (tests/hubsim.c), and
against an imitation of the hub for what the simulator cannot stage. The
tile's chip, its flash and its ROM bootloader, is an imitation both times,
written from Artery's manual. No real hub and no real ROM has seen it.

What it does:

 1. Finds the hub's serial port (USB 1209:0001; `--port` names another) and
    asks the console which tile is on the hub's cable, and by which of the
    tile's ports. If the cables are still quiet from an earlier command, it
    waits for that to end first.
 2. `bootloader <tile> <ms>`: the tile restarts into its ROM bootloader;
    the hub and every other tile keep off the cables for that long.
 3. `bridge 115200 e straight`, then 0x7F, which the ROM measures the speed
    by. If nothing answers, the same with `swapped`.
 4. Erases the 2 KB sectors the file covers, writes it, reads it back and
    compares, and restarts the tile.
 5. Ends the bridge (it lets go of DTR for a moment), waits until the hub
    sees the tile again, and asks the tile which program it runs.

The file is a factory file (tools/mkimage.py): the whole chip up to the
tile's settings, with the bootloader, the program in the first of its two
slots, and nothing in the second. So step 4 erases all of that, and a
program that was there before cannot be started in place of the new one.
The blank part is not written, and where nothing was written only the two
ends of every sector are read back: where a program would begin, and where
a slot keeps its notes.

It flashes one tile: the one the hub's cable is plugged into. The bridge
reaches no further, and the ROM bootloader listens on two of the tile's four
ports only: W (USART2) and S (USART3). The script looks which port the
cable is in and stops, before anything is changed, if it is N or E.

What it never does: erase or write the last 2 KB of flash (0x0803F800,
where a tile keeps its settings and calibration), erase everything at once,
or touch the chip's protection settings. It refuses a file that is not a
program for this chip, the hub's files, and a program built for one of the
two slots (tile_a.img, tile_b.img: those are update.py's). It does take a
plain program for the start of flash that is no factory file; a tile with
such a program and no bootloader takes no update through the hub.

`--rom` is for a tile that cannot be sent into its ROM from the console: a
blank one, or one whose program does not get there. The script first makes
the hub and the other tiles keep quiet, then asks you to restart the tile
with its BOOT0 pad held at 3.3 V, and goes on from step 3. The order
matters: the ROM takes the first byte it hears for the speed measurement,
so the hub must already be silent when the tile restarts.

Where the protocol comes from: Artery's PM0005, "AT32 Bootloader UART
Protocol" (V2.0.4), and PM0007, the bootloader's program manual (V2.0.8),
both in Bootloader_V2.0.7.zip on arterytek.com.

Needs Python 3.8 and nothing else on Linux and macOS. On Windows it needs
pyserial, and that path has never run at all.
"""

import argparse
import collections
import glob
import os
import re
import struct
import sys
import time

import mkimage

FLASH_BASE = 0x08000000
SECTOR = 2048                   # AT32F405xC: 128 sectors of 2 KB
RECORD_BASE = 0x0803F800        # the last sector: settings and calibration
RECORD_SECTOR = (RECORD_BASE - FLASH_BASE) // SECTOR
RAM_BASE = 0x20000000
RAM_END = 0x20019800            # 102 KB
BLOCK = 256                     # the most one Read or Write Memory carries
PROBE = 16                      # read at either end of a sector nothing was written to

# The chip's ID as the reference manual has it (DEBUG_IDCODE).
CHIPS = {
    0x70053240: "AT32F405RCT7",
    0x70053242: "AT32F405RCT7-7",
}

HUB_USB_ID = (0x1209, 0x0001)
HUB_MARK = b"piano hub, firmware"       # text only the hub's program contains

# PM0005: every command is its code and the code's complement; the ROM
# answers ACK or NACK. Its "Set ISP" (0xFA), which six older series want
# first, is not sent: this chip is not one of them.
SYNC = 0x7F
ACK = 0x79
NACK = 0x1F
GET_VERSION = 0x01
GET_ID = 0x02
READ_MEMORY = 0x11
JUMP = 0x21
WRITE_MEMORY = 0x31
ERASE = 0x44
RESET_DEVICE = 0xD4

# Times on the hub's side, in seconds (firmware/core/proto.h, hub/main.c).
ROM_START = 0.6         # the tile restarts 250 ms after the command; PM0007
                        # asks for 200 ms more before the first byte
BRIDGE_START = 0.15     # the hub switches over once its `ok` has left
DTR_LOW = 0.05
BRIDGE_END = 0.15       # the hub drops what was typed while a bridge ends
SYNC_WAIT = 0.3
SYNC_TRIES = 3
RUB_OUT = 176           # backspaces: more than the console's longest line
                        # (core/hub.h: HUB_LINE_MAX, 159)
QUIET_LEAD = 0.05       # the hub itself falls silent 20 ms after it takes `quiet`
QUIET_MAX = 67.0        # the longest quiet there is, and a little
NOBODY_WAIT = 2.0       # a hub that has only just got its power hears its tile
BACK_WAIT = 8.0         # link up, place in the tree, first INFO
HEARD_SLACK = 0.1       # a console answer on its way through USB
INFO_WAIT = 2.5         # a tile sends an INFO every second; one may get lost

# The clock. The test puts its own here.
now = time.monotonic
sleep = time.sleep


class Failure(Exception):
    """What went wrong, in words for the person at the keyboard."""


class Refused(Failure):
    """The ROM answered NACK. `first`: to the command itself, which is how
    it refuses a command that the chip's access protection forbids."""

    first = False


class NoAnswer(Exception):
    """Not enough came in the time allowed."""


class HubError(Exception):
    """The console answered `error: ...`."""

    def __init__(self, text, lines):
        Exception.__init__(self, text)
        self.text = text
        self.lines = lines


def xor(data):
    x = 0
    for b in data:
        x ^= b
    return x


# ------------------------------------------------------------ the program ---

def load_image(path):
    """The file to flash, checked and padded to whole words: a factory file,
    or a plain program for the start of flash."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise Failure(f"cannot read {path}: {e.strerror}") from None
    if data[:4] == b"\x7fELF":
        raise Failure(f"{path} is an ELF file; the ROM takes the plain binary (.bin)")
    if data[:1] == b":":
        raise Failure(f"{path} looks like an Intel HEX file; the ROM takes the plain binary (.bin)")
    if len(data) < 8:
        raise Failure(f"{path} is too short to be a program")
    if len(data) > RECORD_BASE - FLASH_BASE:
        raise Failure(f"{path} is {len(data)} bytes; a tile's program has room for "
                      f"{RECORD_BASE - FLASH_BASE} (the last sector is the tile's settings)")
    if mkimage.header(data) is not None:
        raise Failure(f"{path} is a program for one of the two slots behind the bootloader.\n"
                      "A running tile gets those through the hub: tools/update.py. What this\n"
                      "script flashes is the factory file, build/tile/tile_factory.bin")
    if len(data) == mkimage.FACTORY_LEN:
        try:
            program = mkimage.parse_factory(data)
        except mkimage.Bad as e:
            raise Failure(f"{path} has the size of a factory file and is not a whole one: "
                          f"{e}") from None
        if mkimage.kind_name(program.kind) != "tile":
            raise Failure(f"{path} is the {mkimage.kind_name(program.kind)}'s factory file, "
                          "not a tile's")
        return data
    stack, start = struct.unpack_from("<II", data)
    if not (RAM_BASE < stack <= RAM_END and stack % 4 == 0):
        raise Failure(f"{path} is not a program for this chip: its first word, the stack "
                      f"pointer, is 0x{stack:08x}, which is not in RAM")
    if not (start & 1 and FLASH_BASE + 8 <= start - 1 < FLASH_BASE + len(data)):
        raise Failure(f"{path} is not a program linked for 0x{FLASH_BASE:08x}: its start "
                      f"address is 0x{start:08x}")
    if HUB_MARK in data:
        raise Failure(f"{path} is the hub's program, not a tile's")
    return data + b"\xff" * (-len(data) % 4)


def factory_program(image):
    """The program in a factory file, as mkimage.Image; None for a plain
    program."""
    if len(image) != mkimage.FACTORY_LEN:
        return None
    return mkimage.parse_factory(image)


def plan(image):
    """How `image` gets into the flash: how many sectors are erased, the
    blocks that are written, as (offset, bytes), and the pieces that are
    read back, as (offset, length).

    A factory file is mostly 0xFF: it covers the whole chip so that all of
    it is erased. A blank block is not written. Of a sector that nothing is
    written to, the two ends are read back: a program left there begins at
    a sector's first byte, and a slot's trailer is its last sector's end."""
    sectors = -(-len(image) // SECTOR)
    writes = [(at, image[at:at + BLOCK]) for at in range(0, len(image), BLOCK)
              if image[at:at + BLOCK].strip(b"\xff")]
    busy = {at // SECTOR for at, _ in writes}
    reads = []
    for s in range(sectors):
        a, b = s * SECTOR, min((s + 1) * SECTOR, len(image))
        if s in busy:
            reads += [(at, min(BLOCK, b - at)) for at in range(a, b, BLOCK)]
        elif b - a <= 2 * PROBE:
            reads.append((a, b - a))
        else:
            reads += [(a, PROBE), (b - PROBE, PROBE)]
    return sectors, writes, reads


def flash_time(image, baud):
    """A guess at how long erasing, writing and reading back take, in
    seconds: the bytes on the wire, and a few USB round trips per block."""
    sectors, writes, reads = plan(image)

    def wire(n):
        return (n + 10) * 11.0 / baud

    return (1.0 + sectors * 0.03 + sum(wire(len(data)) + 0.030 for _, data in writes)
            + sum(wire(n) + 0.015 for _, n in reads))


# -------------------------------------------------------- the serial port ---
#
# A port here is four things: write(bytes); read(seconds), which gives what
# has arrived, waiting up to that long for the first byte; set_dtr(bool);
# close().

class PosixPort:
    """A serial port by termios: raw bytes and the DTR line."""

    def __init__(self, path):
        import fcntl
        import select
        import termios

        self.fcntl, self.select, self.termios = fcntl, select, termios
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            a = termios.tcgetattr(self.fd)
            a[0] = 0                                    # input: nothing changed
            a[1] = 0                                    # output: nothing changed
            a[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB
                      | getattr(termios, "CRTSCTS", 0))
            a[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
            a[3] = 0                                    # no echo, no lines, no signals
            a[4] = a[5] = termios.B115200               # USB does not care
            a[6][termios.VMIN] = 0
            a[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, a)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except Exception:
            os.close(self.fd)
            raise

    def write(self, data):
        left = memoryview(bytes(data))
        while left:
            if not self.select.select([], [self.fd], [], 2.0)[1]:
                raise Failure("the hub's serial port takes no more bytes")
            try:
                n = os.write(self.fd, left)
            except BlockingIOError:
                continue
            except OSError as e:
                raise Failure(f"the hub's serial port has gone ({e.strerror})") from None
            left = left[n:]

    def read(self, wait):
        if not self.select.select([self.fd], [], [], max(0.0, wait))[0]:
            return b""
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return b""
        except OSError as e:
            raise Failure(f"the hub's serial port has gone ({e.strerror})") from None
        if not data:
            time.sleep(0.01)            # readable and empty: do not spin on it
        return data

    def set_dtr(self, on):
        request = self.termios.TIOCMBIS if on else self.termios.TIOCMBIC
        self.fcntl.ioctl(self.fd, request, struct.pack("I", self.termios.TIOCM_DTR))

    def close(self):
        os.close(self.fd)


class PyserialPort:
    """The same through pyserial, where there is no termios. Never run."""

    def __init__(self, path):
        import serial

        self.serial = serial.Serial(path, 115200, timeout=0)

    def write(self, data):
        self.serial.write(bytes(data))

    def read(self, wait):
        self.serial.timeout = max(0.0, wait)
        data = self.serial.read(1)
        if data:
            self.serial.timeout = 0
            data += self.serial.read(4096)
        return data

    def set_dtr(self, on):
        self.serial.dtr = on

    def close(self):
        self.serial.close()


def open_port(path):
    try:
        import termios  # noqa: F401
    except ImportError:
        try:
            return PyserialPort(path)
        except ImportError:
            raise Failure("this system has no termios; the script needs pyserial here "
                          "(pip install pyserial)") from None
        except Exception as e:
            raise Failure(f"cannot open {path}: {e}") from None
    try:
        return PosixPort(path)
    except OSError as e:
        raise Failure(f"cannot open {path}: {e.strerror}") from None


def find_hub(sys_tty="/sys/class/tty"):
    """The serial port of the hub that is plugged in."""
    found = []
    for tty in sorted(glob.glob(os.path.join(sys_tty, "ttyACM*"))):
        # .../device is the USB interface; the directory above it is the device
        usb = os.path.dirname(os.path.realpath(os.path.join(tty, "device")))
        try:
            with open(os.path.join(usb, "idVendor")) as v, open(os.path.join(usb, "idProduct")) as p:
                ids = (int(v.read(), 16), int(p.read(), 16))
        except (OSError, ValueError):
            continue
        if ids == HUB_USB_ID:
            found.append("/dev/" + os.path.basename(tty))
    if not found:
        try:
            from serial.tools import list_ports
        except ImportError:
            pass
        else:
            found = [p.device for p in list_ports.comports() if (p.vid, p.pid) == HUB_USB_ID]
    if not found:
        raise Failure("found no hub (a USB serial port with ID %04x:%04x). Is it plugged in? "
                      "--port names the serial port by hand." % HUB_USB_ID)
    if len(found) > 1:
        raise Failure("more than one serial port could be the hub: " + ", ".join(found)
                      + ". Choose one with --port.")
    return found[0]


class Wire:
    """Bytes to and from the port, with an end to every wait."""

    def __init__(self, port):
        self.port = port
        self.held = bytearray()
        self.sent = 0

    def send(self, data):
        self.port.write(bytes(data))
        self.sent += len(data)

    def forget(self):
        """Drop what has arrived: it answers nothing that is asked from now on."""
        dropped = bytes(self.held)
        self.held.clear()
        for _ in range(64):
            more = self.port.read(0)
            if not more:
                break
            dropped += more
        return dropped

    def settle(self, gap):
        """Drop what arrives until nothing has for `gap` seconds (or 2 s are over)."""
        dropped = self.forget()
        end = now() + 2.0
        while now() < end:
            more = self.port.read(gap)
            if not more:
                break
            dropped += more
        return dropped

    def _more(self, end):
        left = end - now()
        if left <= 0:
            raise NoAnswer()
        self.held += self.port.read(left)

    def take(self, n, end):
        while len(self.held) < n:
            self._more(end)
        data = bytes(self.held[:n])
        del self.held[:n]
        return data

    def line(self, end):
        while b"\n" not in self.held:
            self._more(end)
        at = self.held.index(b"\n")
        text = self.held[:at].decode("ascii", "replace").rstrip("\r")
        del self.held[:at + 1]
        return text


class Out:
    """What the person at the keyboard gets to read."""

    def __init__(self, stream, verbose):
        self.stream = stream
        self.verbose = verbose
        self.tty = hasattr(stream, "isatty") and stream.isatty()
        self.width = 0

    def say(self, text):
        if self.width:
            self.stream.write("\r" + " " * self.width + "\r")
            self.width = 0
        self.stream.write(text + "\n")
        self.stream.flush()

    def detail(self, text):
        if self.verbose:
            self.say("    " + text)

    def progress(self, text):
        """A line that the next one overwrites; only on a terminal."""
        if self.tty:
            self.stream.write("\r" + text.ljust(self.width))
            self.stream.flush()
            self.width = max(self.width, len(text))


# ---------------------------------------------------------------- the hub ---

Tile = collections.namedtuple("Tile", "number id firmware port heard up")


class Hub:
    """The hub's console, and its bridge."""

    def __init__(self, port, out):
        self.port = port
        self.wire = Wire(port)
        self.out = out
        self.bridged = False
        self.mark = 0           # wire.sent when the bridge began

    def ask(self, command, wait=2.0):
        """Send one command; give the lines it answers before its `ok`."""
        self.wire.forget()
        self.out.detail("> " + command)
        self.wire.send(command.encode("ascii") + b"\r")
        end = now() + wait
        lines = []
        while True:
            text = self.wire.line(end)
            self.out.detail("< " + text)
            if text == "ok":
                return lines
            if text.startswith("error: "):
                raise HubError(text[7:], lines)
            lines.append(text)

    def hello(self, wait):
        """The console's `version` line, or None if it does not answer."""
        for _ in range(2):
            try:
                lines = self.ask("version", wait)
            except NoAnswer:
                return None
            except HubError:
                continue        # the tail of something else was in its line
            for text in lines:
                if text.startswith("piano hub"):
                    return text
        return None

    def pulse_dtr(self):
        """Let go of DTR for a moment: to the hub, the end of a bridge."""
        self.port.set_dtr(False)
        sleep(DTR_LOW)
        self.port.set_dtr(True)
        sleep(BRIDGE_END)
        self.wire.forget()

    def wake(self):
        """Get the console's attention, whatever the last program left behind."""
        for _ in range(3):
            # Rub out a line somebody left half typed, so that the Enter
            # after it runs nothing. 0x7F is the console's backspace; and
            # if a bridge is still on, it is the byte that does a ROM
            # bootloader at the far end the least harm.
            self.wire.send(bytes([SYNC]) * RUB_OUT + b"\r")
            self.wire.settle(0.2)
            text = self.hello(1.0)
            if text:
                return text
            # A bridge may still be on from a run that was cut off. What was
            # just sent has gone through it, so letting go of DTR ends it.
            self.pulse_dtr()
        raise Failure("the serial port does not answer like the hub's console "
                      "(`version` got no `piano hub ...`). Is another program using the port?")

    def is_quiet(self):
        """Is a `quiet` on? The console refuses `ping` for that reason
        before it looks who the ping is for, and no tile is called nobody:
        so the question does nothing, whatever the answer."""
        try:
            self.ask("ping nobody")
        except HubError as e:
            return e.text == "the cable is quiet"
        except NoAnswer:
            raise Failure("the hub's console stopped answering") from None
        return False

    def bridge(self, baud, way):
        try:
            self.ask(f"bridge {baud} e {way}")
        except HubError as e:
            raise Failure(f"the hub refused `bridge`: {e.text}") from None
        except NoAnswer:
            raise Failure("the hub did not answer `bridge`") from None
        self.bridged = True
        self.mark = self.wire.sent
        sleep(BRIDGE_START)
        self.wire.forget()

    def unbridge(self):
        """End the bridge and see that the console is back."""
        if not self.bridged:
            return
        if self.wire.sent == self.mark:
            # The hub ends a bridge only after a byte has gone through it.
            self.wire.send(bytes([SYNC]))
            sleep(DTR_LOW)
        for _ in range(3):
            self.pulse_dtr()
            if self.hello(0.7):
                self.bridged = False
                return
        raise Failure("the hub does not come out of its bridge. Its USER button ends one; "
                      "so does unplugging it.")


def find_tile(hub):
    """The tile on the hub's cable, as the console tells it; None without one."""
    number = None
    try:
        for text in hub.ask("grid"):
            m = re.fullmatch(r"hub on tile (\d+)", text)
            if m:
                number = int(m.group(1))
        if number is None:
            return None
        lines = hub.ask(f"info {number}")
    except HubError:
        return None                     # it went between the two commands
    except NoAnswer:
        raise Failure("the hub's console stopped answering") from None
    tile_id = firmware = port = heard = up = None
    for text in lines:
        m = re.match(r"tile \d+ id ([0-9a-f]{8}) ", text)
        if m:
            tile_id = m.group(1)
        m = re.match(r"heard (\d+) ms ago", text)
        if m:
            heard = int(m.group(1))
        m = re.match(r"firmware ([0-9a-f]{4}),", text)
        if m:
            firmware = m.group(1)
        m = re.fullmatch(r"([WNES]): the hub", text)
        if m:
            port = m.group(1)
        m = re.match(r"up (\d+) s,", text)
        if m:
            up = int(m.group(1))
    if tile_id is None:
        return None
    return Tile(number, tile_id, firmware, port, heard, up)


def wait_talking(hub, out):
    """Wait for a `quiet` to end that an earlier command began, a run of
    this script that was cut off for one. While it lasts the hub hears no
    tile and takes no command for one."""
    end = now() + QUIET_MAX
    waited = False
    while hub.is_quiet():
        if now() >= end:
            raise Failure("the cables stay quiet: the hub has refused commands for its tiles "
                          f"for {QUIET_MAX:.0f} s, longer than any `quiet` lasts")
        if not waited:
            out.say("the cables are quiet from an earlier command; waiting for that to end "
                    "(a minute at most)")
            waited = True
        out.progress("waiting for the quiet to end")
        sleep(0.5)


def settled_tile(hub, wait=3.0):
    """find_tile, with some patience: a hub that has only just got its
    power, or come out of a quiet, has not heard its tile yet; and a tile
    that has only just appeared has not told the hub about itself."""
    began = now()
    while True:
        tile = find_tile(hub)
        if tile and tile.firmware and tile.port:
            return tile
        if now() - began >= (wait if tile else NOBODY_WAIT):
            return tile
        sleep(0.25)


def check_port(tile):
    if tile.port is None:
        raise Failure(f"tile {tile.id} has not told the hub yet who is on its ports, so there "
                      "is no telling which port the hub's cable is in. Try again in a moment "
                      "(--any-port skips this check).")
    if tile.port not in "WS":
        raise Failure(
            f"the hub's cable is in tile {tile.id}'s {tile.port} port. The chip's ROM bootloader\n"
            "listens on the W and S ports only (USART2 and USART3), so it would never hear\n"
            "the hub there. Move the hub's cable to that tile's W or S port and run this\n"
            "again. Nothing was changed. (--any-port skips this check.)")


def to_rom(hub, tile, quiet_ms, out):
    """`bootloader`: the tile restarts into its ROM, everybody else is quiet."""
    try:
        hub.ask(f"bootloader {tile.id} {quiet_ms}", 4.0)
    except HubError as e:
        said = "; ".join(e.lines + [e.text])
        raise Failure(f"the hub could not send the tile into its ROM bootloader: {said}\n"
                      "Nothing was changed. (--rom is for a tile that has to be put there "
                      "by hand.)") from None
    except NoAnswer:
        raise Failure("the hub did not answer `bootloader`") from None
    quiet_end = now() + quiet_ms / 1000.0
    out.say(f"the tile restarts into its ROM bootloader; the cables are quiet for "
            f"{quiet_ms / 1000.0:.1f} s")
    sleep(ROM_START)
    return quiet_end


BY_HAND = """\
The tile has to be put into its ROM bootloader by hand: hold its BOOT0 pad
at 3.3 V (the pad beside it) and restart the tile, by touching its reset pad
to ground for a moment or by plugging its cable in again; then let go of
BOOT0. The hub has to be silent while that happens, and it can promise
silence for {seconds:.0f} s at a time.
(A tile that is in its ROM bootloader already needs nothing done.)
Press Enter when you are ready to do it: """

BY_HAND_NOW = """\
The cables are quiet for {seconds:.0f} s. Restart the tile into its ROM bootloader
now, then press Enter: """


def keep_quiet(hub, quiet_ms):
    """`quiet`: the hub and every tile keep off the cables. When that ends."""
    end = now() + QUIET_MAX
    while True:
        try:
            hub.ask(f"quiet {quiet_ms}")
            break
        except HubError as e:
            # one quiet has to end before the next can begin
            if e.text != "the cable is quiet" or now() >= end:
                raise Failure(f"the hub refused `quiet`: {e.text}") from None
        except NoAnswer:
            raise Failure("the hub did not answer `quiet`") from None
        sleep(0.5)
    quiet_end = now() + quiet_ms / 1000.0
    sleep(QUIET_LEAD)                   # only now is the hub silent itself
    return quiet_end


def by_hand(hub, quiet_ms, out, prompt):
    """--rom: silence first, then somebody restarts the tile into its ROM.
    The ROM takes the first byte it hears for its speed measurement, and
    the hub's own talk is at another speed."""
    try:
        prompt(BY_HAND.format(seconds=quiet_ms / 1000.0))
        while True:
            quiet_end = keep_quiet(hub, quiet_ms)
            prompt(BY_HAND_NOW.format(seconds=quiet_ms / 1000.0))
            left = quiet_end - now()
            if left >= 4.0:
                return quiet_end
            out.say("That took too long: the quiet is over, or nearly, and the hub speaks "
                    "on the cable again. Once more.")
            sleep(max(left, 0.0) + 0.3)
    except EOFError:
        raise Failure("--rom needs somebody at the keyboard") from None


# ----------------------------------------------------- the ROM bootloader ---

class Rom:
    """The chip's ROM bootloader, through the bridge (PM0005)."""

    def __init__(self, wire, baud, out):
        self.wire = wire
        self.baud = baud
        self.out = out

    def _wait(self, sent=0):
        """How long an answer may take after `sent` bytes went out."""
        return 1.0 + 2.0 * sent * 11.0 / self.baud

    def _ack(self, what, wait=None, first=False):
        try:
            b = self.wire.take(1, now() + (wait or self._wait()))[0]
        except NoAnswer:
            raise Failure(f"the ROM bootloader stopped answering ({what})") from None
        if b == NACK:
            e = Refused(f"the ROM bootloader refused: {what}")
            e.first = first
            raise e
        if b != ACK:
            raise Failure(f"the ROM bootloader answered 0x{b:02x} where 0x{ACK:02x} or "
                          f"0x{NACK:02x} belongs ({what})")

    def _take(self, n, what):
        try:
            return self.wire.take(n, now() + self._wait(n))
        except NoAnswer:
            raise Failure(f"the ROM bootloader stopped answering ({what})") from None

    def _command(self, code, what):
        stale = self.wire.forget()
        if stale:
            self.out.detail(f"rom: {len(stale)} bytes nobody asked for: {stale[:16].hex()}")
        self.out.detail("rom: " + what)
        self.wire.send(bytes([code, code ^ 0xFF]))
        self._ack(what, first=True)

    def _address(self, address, what):
        raw = struct.pack(">I", address)
        self.wire.send(raw + bytes([xor(raw)]))
        self._ack(what)

    def sync(self):
        """Send 0x7F until the ROM answers. One ACK: it has measured the
        speed. One NACK: it had the speed already and took two 0x7F for a
        bad command. Anything else is noise, such as a tile's own program
        talking at another speed."""
        for _ in range(SYNC_TRIES):
            self.wire.forget()
            self.wire.send(bytes([SYNC]))
            try:
                got = self.wire.take(1, now() + SYNC_WAIT)
            except NoAnswer:
                continue
            got += self.wire.settle(0.03)
            if got in (bytes([ACK]), bytes([NACK])):
                return True
            self.out.detail(f"rom: noise where an answer belongs: {got[:16].hex()}")
        return False

    def version(self):
        """(protocol version, the bootloader's version code)."""
        self._command(GET_VERSION, "Get Version")
        raw = self._take(3, "Get Version")
        self._ack("Get Version")
        return raw[0], raw[1] << 8 | raw[2]

    def chip(self):
        """The five ID bytes as they come."""
        self._command(GET_ID, "Get Device ID")
        n = self._take(1, "Get Device ID")[0] + 1
        raw = self._take(n, "Get Device ID")
        self._ack("Get Device ID")
        return raw

    def read(self, address, n):
        what = f"reading {n} bytes at 0x{address:08x}"
        self._command(READ_MEMORY, what)
        self._address(address, what)
        self.wire.send(bytes([n - 1, (n - 1) ^ 0xFF]))
        self._ack(what)
        return self._take(n, what)

    def write(self, address, data):
        if not (1 <= len(data) <= BLOCK and FLASH_BASE <= address
                and address + len(data) <= RECORD_BASE):
            raise AssertionError(f"a write of {len(data)} bytes at 0x{address:08x}")
        what = f"writing {len(data)} bytes at 0x{address:08x}"
        self._command(WRITE_MEMORY, what)
        self._address(address, what)
        body = bytes([len(data) - 1]) + data
        self.wire.send(body + bytes([xor(body)]))
        self._ack(what, self._wait(len(body)) + 1.0)

    def erase(self, sector):
        """One 2 KB sector. Never the last one, never all of them."""
        if not 0 <= sector < RECORD_SECTOR:
            raise AssertionError(f"an erase of sector {sector}")
        what = f"erasing sector {sector}"
        self._command(ERASE, what)
        body = struct.pack(">HH", 0, sector)    # one sector (count - 1), its index
        self.wire.send(body + bytes([xor(body)]))
        self._ack(what, 3.0)

    def reset(self):
        """Restart the chip. The ROM says ACK twice and does it."""
        self._command(RESET_DEVICE, "Reset Device")
        try:
            self._ack("Reset Device")
        except Failure as e:
            self.out.detail(f"rom: no second ACK ({e})")

    def jump(self, address):
        what = f"jumping to 0x{address:08x}"
        self._command(JUMP, what)
        self._address(address, what)


NO_ROM = """\
the tile's ROM bootloader does not answer{ways}.
What would explain it:
  - the hub's cable is not in the tile's W or S port (the ROM listens nowhere else);
  - the tile did not get into its ROM bootloader (the jump from the tile's program
    has never been tried on hardware; --rom is the way round it);
  - the ROM took another of its interfaces for the one in use: a tile's unused
    receive pins float, which Artery's FAQ0122 warns about."""


def connect(hub, opt, out):
    """Bridge, one way round and then the other, until the ROM answers."""
    ways = ["straight", "swapped"] if opt.orientation == "auto" else [opt.orientation]
    for way in ways:
        hub.bridge(opt.baud, way)
        rom = Rom(hub.wire, opt.baud, out)
        if rom.sync():
            try:
                protocol, code = rom.version()
            except Failure as e:
                out.detail(f"bridge {way}: {e}")
            else:
                out.say(f"the ROM bootloader answers (bridge {way}, {opt.baud} baud): "
                        f"protocol 0x{protocol:02x}, version 0x{code:04x}")
                return rom
        else:
            out.detail(f"bridge {way}: no answer")
        hub.unbridge()
    tried = ", with the bridge straight or swapped" if len(ways) > 1 else f" (bridge {ways[0]})"
    raise Failure(NO_ROM.format(ways=tried))


def which_chip(raw):
    """(ID, name or None, a note) from Get Device ID's bytes. PM0005 has
    them in the order 15..8, 7..0, 31..24, 23..16, then a project ID."""
    if len(raw) < 4:
        return 0, None, ""
    stated = raw[2] << 24 | raw[3] << 16 | raw[0] << 8 | raw[1]
    if stated in CHIPS:
        return stated, CHIPS[stated], ""
    for order in ("big", "little"):
        other = int.from_bytes(raw[:4], order)
        if other in CHIPS:
            return other, CHIPS[other], " (the bytes came in another order than PM0005 says)"
    return stated, None, ""


def identify(rom, opt, out):
    """Is this the chip the program is for, and may its flash be read?"""
    raw = rom.chip()
    chip, name, note = which_chip(raw)
    if name:
        out.say(f"chip: {name} (0x{chip:08x}){note}")
    elif opt.any_chip:
        out.say(f"chip: unknown, ID bytes {raw.hex()}; going on (--any-chip)")
    else:
        known = ", ".join(f"{n} 0x{i:08x}" for i, n in CHIPS.items())
        raise Failure(f"this is not the chip the program is for: Get Device ID gave {raw.hex()}\n"
                      f"(0x{chip:08x} in PM0005's byte order); a tile's chip is\n{known}. "
                      "--any-chip goes on all the same.")
    try:
        rom.read(FLASH_BASE, 4)
    except Refused as e:
        if not e.first:
            raise
        raise Failure(
            "the chip's access protection is on: its ROM bootloader refuses to read the\n"
            "flash, and will refuse to erase and write it. Taking the protection off erases\n"
            "the whole chip, the tile's settings with it; this script does not do that\n"
            "(Artery's ICP and ISP programmers do).") from None


def write_program(rom, image, opt, out):
    """Erase what the file covers, write it, read it back."""
    sectors, writes, reads = plan(image)
    began = now()
    for s in range(sectors):
        out.progress(f"erasing sector {s + 1} of {sectors}")
        rom.erase(s)
    out.say(f"erased {sectors} sectors, 0x{FLASH_BASE:08x} to "
            f"0x{FLASH_BASE + sectors * SECTOR - 1:08x} ({now() - began:.1f} s)")

    began = now()
    total = sum(len(data) for _, data in writes)
    done = 0
    for at, data in writes:
        out.progress(f"writing {done} of {total} bytes")
        rom.write(FLASH_BASE + at, data)
        done += len(data)
    blank = len(image) - total
    out.say(f"wrote {total} bytes" + (f"; the other {blank} are blank" if blank else "")
            + f" ({now() - began:.1f} s)")

    if not opt.verify:
        out.say("not read back (--no-verify)")
        return
    # Only now, when every block is written: a ROM that erased more than it
    # was asked to would show here and not block by block.
    began = now()
    for i, (at, n) in enumerate(reads):
        out.progress(f"reading back, piece {i + 1} of {len(reads)}")
        want = image[at:at + n]
        got = rom.read(FLASH_BASE + at, n)
        if got != want:
            i = next(i for i in range(len(want)) if got[i] != want[i])
            raise Failure(f"what the flash holds is not what was written: at "
                          f"0x{FLASH_BASE + at + i:08x} it reads 0x{got[i]:02x}, the program "
                          f"has 0x{want[i]:02x}")
    looked = sum(n for _, n in reads)
    if looked == len(image):
        out.say(f"read back {looked} bytes: the same ({now() - began:.1f} s)")
    else:
        out.say(f"read back {looked} bytes, what was written and both ends of every blank "
                f"sector: the same ({now() - began:.1f} s)")


def restart(rom, how, out):
    """Start the new program. False if the tile is left in its ROM."""
    if how == "stay":
        out.say("the tile is left in its ROM bootloader (--go stay)")
        return False
    try:
        if how == "reset":
            try:
                rom.reset()
                out.say("the tile restarts")
                return True
            except Refused:
                out.say("the ROM bootloader does not know Reset Device; jumping to the "
                        "program instead")
        rom.jump(FLASH_BASE)
        out.say("the ROM bootloader jumps to the program")
        return True
    except Failure as e:
        out.say(f"the program is in the flash, but the tile could not be restarted: {e}")
        return False


def wait_back(hub, before, since, wait, out):
    """Poll the console until the tile on the hub's cable talks again: the
    tile as the console shows it then, or None. `before` is the tile as it
    was shown before the flashing, if it was.

    What the hub remembers from before the restart does not count. The hub
    must have heard the tile since; and the INFO it shows must be the new
    program's: one that says the tile has been up for less time than the
    old program had been, or, where that does not tell them apart, one that
    is shown when the tile has been back for longer than INFOs are apart."""
    end = now() + wait
    alive = None                        # when the tile was first seen to be back
    while True:
        tile = find_tile(hub)
        if (tile and tile.heard is not None
                and tile.heard / 1000.0 < now() - since - HEARD_SLACK):
            if alive is None:
                alive = now()
            if tile.firmware and tile.up is not None and (
                    before is None
                    or (before.up is not None and tile.up < before.up)
                    or now() - alive >= INFO_WAIT):
                return tile
        else:
            alive = None
        if now() >= end:
            return None
        out.progress("waiting for the tile")
        sleep(0.25)


def check_runs(hub, tile, program, out):
    """After a factory file: the tile says which program it runs, and that
    has to be the file's. A program left over in the second slot, which the
    bootloader took for the newer one, would show here."""
    try:
        lines = hub.ask(f"fw {tile.id}", 4.0)
    except HubError as e:
        out.say("which program the tile runs could not be asked: "
                + "; ".join(e.lines + [e.text]))
        return
    except NoAnswer:
        raise Failure("the hub's console stopped answering") from None
    for text in lines:
        m = re.fullmatch(r"tile \d+: runs ([ab]) \w+ ([0-9a-f]{4}) ([0-9a-f]{8}), other .*", text)
        if not m:
            continue
        slot, version, crc = m.group(1).upper(), m.group(2), m.group(3)
        if (slot, crc) != ("A", f"{program.crc:08x}"):
            raise Failure("the tile is back, but it runs another program than the one just "
                          f"flashed:\nthe one in slot {slot}, version {version}, crc {crc}. "
                          f"The file's is for slot A, version\n{program.version:04x}, crc "
                          f"{program.crc:08x}.")
        out.say(f"it runs what was flashed: slot A, version {version}, crc {crc}")
        return
    out.say("which program the tile runs could not be asked: the hub said nothing of it")


IN_ROM = """\
The tile may be in its ROM bootloader now, where the hub does not see it. Its
program was not changed. To get it back, restart it: plug its cable in again,
or `power off` and then `power auto` on the hub's console."""

NO_PROGRAM = """\
The tile has no complete program now, and is still in its ROM bootloader. Run
this again with --rom and the same --baud, and press Enter at both of its
questions without touching the tile; if the ROM does not answer then, do
restart the tile into its ROM as --rom says."""

NOT_BACK = """\
the program was written{checked}, but the hub does not see the tile again.
If its BOOT0 pad is still held at 3.3 V, every restart ends in the ROM
bootloader: let go and restart the tile (plug its cable in again, or
`power off` and `power auto` on the hub's console)."""


def flash(port, image, opt, out, prompt):
    hub = Hub(port, out)
    out.say("hub: " + hub.wake())

    wait_talking(hub, out)
    tile = settled_tile(hub)
    if tile:
        out.say(f"tile {tile.id} is on the hub's cable"
                + (f", by its {tile.port} port" if tile.port else "")
                + (f"; firmware {tile.firmware}" if tile.firmware else ""))
        if not opt.any_port:
            check_port(tile)
    elif opt.rom:
        out.say("the hub sees no tile on its cable (one without a program, or in its ROM\n"
                "bootloader, says nothing), so which port the cable is in cannot be checked:\n"
                "it has to be the tile's W or S")
    else:
        raise Failure("the hub sees no tile on its cable. A tile without a program, or one whose\n"
                      "program does not start, has to be put into its ROM bootloader by hand:\n"
                      "see --rom.")

    program = factory_program(image)
    sectors = -(-len(image) // SECTOR)
    out.say(f"program: {opt.image}, {len(image)} bytes, {sectors} sectors of flash")
    if program:
        out.say(f"a factory file: the bootloader, and in slot A the tile's program, version "
                f"{program.version:04x}, crc {program.crc:08x}")
    else:
        out.say(f"not a factory file: it goes to 0x{FLASH_BASE:08x} as it is. Without the "
                "bootloader there, the\ntile takes no new program through the hub afterwards "
                "(tools/update.py)")
    if opt.check:
        out.say("--check: nothing was changed")
        return

    guess = flash_time(image, opt.baud)
    if opt.quiet_ms:
        quiet_ms = opt.quiet_ms
    elif opt.rom:
        quiet_ms = 30000
    else:
        quiet_ms = min(65535, max(10000, int(4000 + 1300 * guess)))

    rom = None
    state = "untouched"         # what a failure from here on leaves behind
    try:
        if opt.rom:
            quiet_end = by_hand(hub, quiet_ms, out, prompt)
        else:
            quiet_end = to_rom(hub, tile, quiet_ms, out)
        state = "in rom"
        rom = connect(hub, opt, out)
        state = "old program"
        identify(rom, opt, out)
        state = "no program"
        write_program(rom, image, opt, out)
        state = "programmed"
        going = restart(rom, opt.go, out)
        restarted = now()
    except BaseException as e:
        hint = ""
        if state == "in rom":
            hint = IN_ROM
        elif state == "old program":
            try:
                rom.reset()
                hint = "The tile's program was not changed; the tile was restarted."
            except Failure:
                hint = IN_ROM
        elif state == "no program":
            hint = NO_PROGRAM
        try:
            hub.unbridge()
        except Failure as again:
            hint = (hint + "\n" + str(again)).strip()
        if isinstance(e, Failure):
            raise Failure((str(e) + "\n" + hint).strip()) from None
        if hint:
            out.say(hint)
        raise

    hub.unbridge()
    if not going:
        return
    left = max(0.0, quiet_end - now())
    if left > 0.5:
        out.say(f"waiting for the tile (the cables stay quiet for another {left:.0f} s)")
    back = wait_back(hub, tile, restarted, left + BACK_WAIT, out)
    if back is None:
        raise Failure(NOT_BACK.format(checked=" and read back" if opt.verify else ""))
    out.say(f"tile {back.id} is back, firmware {back.firmware}")
    if program:
        check_runs(hub, back, program, out)


def parse(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    image = os.path.normpath(os.path.join(here, "..", "build", "tile", "tile_factory.bin"))
    try:
        image = os.path.relpath(image)
    except ValueError:
        pass                            # another drive
    p = argparse.ArgumentParser(
        description="Flash the tile on the hub's cable, through the hub.",
        epilog="The top of this file says what it does, step by step, and what has "
               "not been tried.")
    p.add_argument("image", nargs="?", default=image,
                   help=f"the tile's factory file: bootloader and program (default: {image})")
    p.add_argument("--port", help="the hub's serial port (default: found by its USB ID)")
    p.add_argument("--check", action="store_true",
                   help="look at the hub, the tile and the file, and change nothing")
    p.add_argument("--rom", action="store_true",
                   help="the tile is put into its ROM bootloader by hand (BOOT0 pad); "
                        "for a blank tile, or one whose program does not get there")
    p.add_argument("--baud", type=int, default=115200,
                   help="speed on the cable while flashing (default 115200; Artery tried "
                        "the ROM from 1200 to 256000)")
    p.add_argument("--orientation", choices=["auto", "straight", "swapped"], default="auto",
                   help="which way round the bridge uses the cable's two wires "
                        "(default: straight, then swapped)")
    p.add_argument("--quiet-ms", type=int, metavar="MS",
                   help="how long the hub and the other tiles keep off the cables "
                        "(default: long enough for the flashing; 30000 with --rom)")
    p.add_argument("--go", choices=["reset", "jump", "stay"], default="reset",
                   help="afterwards: restart the tile (default), have the ROM jump to the "
                        "program, or leave the tile in its ROM")
    p.add_argument("--no-verify", dest="verify", action="store_false",
                   help="do not read the program back")
    p.add_argument("--any-chip", action="store_true",
                   help="go on although the ROM does not name an AT32F405RC")
    p.add_argument("--any-port", action="store_true",
                   help="go on although the hub's cable is not seen in the tile's W or S port")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="show every console line and ROM command")
    opt = p.parse_args(argv)
    if not 1200 <= opt.baud <= 1000000:
        p.error("--baud: 1200 to 1000000 (what the hub's bridge takes)")
    if opt.quiet_ms is not None and not 1 <= opt.quiet_ms <= 65535:
        p.error("--quiet-ms: 1 to 65535")
    return opt


def main(argv=None, open_port=open_port, stream=None, prompt=input):
    opt = parse(argv)
    out = Out(stream or sys.stdout, opt.verbose)
    try:
        image = load_image(opt.image)
        port = open_port(opt.port or find_hub())
        try:
            flash(port, image, opt, out, prompt)
        finally:
            port.close()
    except Failure as e:
        out.say(f"error: {e}")
        return 1
    except KeyboardInterrupt:
        out.say("stopped")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
