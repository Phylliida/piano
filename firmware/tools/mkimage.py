#!/usr/bin/env python3
"""Program images, and the file a chip is flashed with the first time.

    python3 tools/mkimage.py image tile_a.bin tile_a.img
    python3 tools/mkimage.py factory boot.bin tile_a.img tile_factory.bin
    python3 tools/mkimage.py show FILE...

The Makefile runs the first two; `show` says what a file is.

A chip's flash holds a bootloader and two slots for the program
(core/fw.h). A program is linked for one slot's address and has a header
0x200 bytes in. The linker leaves the header's length at 0:

  image    fills the length in and puts the CRC-32 of everything behind the
           program. That is an image: what tools/update.py sends through the
           hub, into the slot the running program is not in.
  factory  makes the file for a chip's first flashing, by its ROM bootloader
           (a tile: tools/flash_tile.py; the hub: USB DFU), to 0x08000000:
           the bootloader, the image for slot A, and 0xFF from there up to
           the settings sector. The 0xFF is there so that the flashing
           erases all of slot B and both slots' trailers: a program left
           over from before must not be newer than the one just flashed.

update.py and flash_tile.py use this file too, to know what they were
given. The numbers below are core/fw.h's; tests/test_mkimage.py compares
them.

Needs Python 3.8 and nothing else.
"""

import collections
import struct
import sys
import zlib

FLASH_BASE = 0x08000000
SECTOR = 2048
BOOT_LEN = 0x2000
SLOT_LEN = 0x1E800
SLOT_ADDR = (FLASH_BASE + BOOT_LEN, FLASH_BASE + BOOT_LEN + SLOT_LEN)
SLOTS_END = SLOT_ADDR[1] + SLOT_LEN         # 0x0803F000
RAM_BASE = 0x20000000
RAM_END = 0x20019800

HEADER_AT = 0x200
HEADER = struct.Struct("<IBBHII")           # magic, kind, 0, version, base, length
H_LENGTH = 12
IMAGE_MAGIC = 0x474D4950                    # "PIMG"
IMAGE_MIN = HEADER_AT + HEADER.size + 4
IMAGE_MAX = SLOT_LEN - SECTOR               # a slot's last sector is its trailer's

FACTORY_LEN = SLOTS_END - FLASH_BASE

KINDS = {0: "tile", 1: "hub"}               # core/proto.h: KIND_TILE, KIND_HUB

Image = collections.namedtuple("Image", "kind version base slot length crc")


class Bad(Exception):
    """Why the bytes are not what they were taken for."""


def kind_name(kind):
    return KINDS.get(kind, f"kind {kind}")


def slot_name(slot):
    return "AB"[slot]


def header(data):
    """(kind, version, base, length) if `data` has an image header where one
    belongs, or None."""
    if len(data) < HEADER_AT + HEADER.size:
        return None
    magic, kind, _, version, base, length = HEADER.unpack_from(data, HEADER_AT)
    if magic != IMAGE_MAGIC:
        return None
    return kind, version, base, length


def check_vectors(data, base, end):
    """What the chip loads first: the stack pointer, and where to start,
    which has to be in base+8 .. end-1."""
    if len(data) < 8:
        raise Bad("too short to be a program")
    stack, start = struct.unpack_from("<II", data)
    if not (RAM_BASE < stack <= RAM_END and stack % 4 == 0):
        raise Bad(f"its first word, the stack pointer, is 0x{stack:08x}, which is not in RAM")
    if not (start & 1 and base + 8 <= start - 1 < end):
        raise Bad(f"its start address is 0x{start:08x}, which is not in a program at "
                  f"0x{base:08x}")


def parse(data):
    """The image that `data` begins with. What follows it is not looked at.
    The checks are those of fw_image_check() in core/fw.c."""
    h = header(data)
    if h is None:
        raise Bad("no image header at 0x200")
    kind, version, base, length = h
    if base not in SLOT_ADDR:
        raise Bad(f"linked for 0x{base:08x}, where no slot begins")
    if length == 0:
        raise Bad("the header's length is not filled in: a program as the linker "
                  "left it, not an image yet")
    if not IMAGE_MIN <= length <= IMAGE_MAX or length % 4:
        raise Bad(f"the header's length is {length}, which no image has")
    if length > len(data):
        raise Bad(f"the header says {length} bytes and there are {len(data)}")
    check_vectors(data, base, base + length - 4)
    crc, = struct.unpack_from("<I", data, length - 4)
    if crc != zlib.crc32(data[:length - 4]):
        raise Bad("the checksum at its end is not that of its bytes")
    return Image(kind, version, base, SLOT_ADDR.index(base), length, crc)


def parse_image(data):
    """An image file: the image and nothing after it."""
    image = parse(data)
    extra = len(data) - image.length
    if extra:
        raise Bad(f"{extra} byte{' follows' if extra == 1 else 's follow'} the image")
    return image


def make_image(binary):
    """The image made of a program as the linker left it."""
    h = header(binary)
    if h is None:
        raise Bad("no image header at 0x200: not a program linked for a slot")
    if h[3] != 0:
        raise Bad("the header's length is filled in already")
    body = bytearray(binary + b"\xff" * (-len(binary) % 4))
    length = len(body) + 4
    if length > IMAGE_MAX:
        raise Bad(f"{length} bytes; a slot has room for {IMAGE_MAX}")
    struct.pack_into("<I", body, HEADER_AT + H_LENGTH, length)
    image = bytes(body) + struct.pack("<I", zlib.crc32(bytes(body)))
    parse_image(image)
    return image


def check_boot(boot):
    if len(boot) > BOOT_LEN:
        raise Bad(f"the bootloader is {len(boot)} bytes; it has room for {BOOT_LEN}")
    if header(boot) is not None:
        raise Bad("a program for a slot where the bootloader belongs")
    check_vectors(boot, FLASH_BASE, FLASH_BASE + len(boot))


def make_factory(boot, image):
    """The whole chip from 0x08000000 up to the settings sector."""
    check_boot(boot)
    im = parse_image(image)
    if im.slot != 0:
        raise Bad("the image is for slot B; a factory file takes the one for slot A")
    out = boot + b"\xff" * (BOOT_LEN - len(boot)) + image
    return out + b"\xff" * (FACTORY_LEN - len(out))


def parse_factory(data):
    """The image in a factory file."""
    if len(data) != FACTORY_LEN:
        raise Bad(f"{len(data)} bytes; a factory file has {FACTORY_LEN}")
    boot = data[:BOOT_LEN].rstrip(b"\xff")
    check_boot(boot + b"\xff" * (-len(boot) % 4))
    try:
        image = parse(data[BOOT_LEN:])
    except Bad as e:
        raise Bad(f"behind the bootloader: {e}") from None
    if image.slot != 0:
        raise Bad("the image behind the bootloader is linked for slot B")
    if data[BOOT_LEN + image.length:].strip(b"\xff"):
        raise Bad("something follows the image in slot A")
    return image


def describe(data):
    """One line on what `data` is."""
    def program(im):
        return (f"{kind_name(im.kind)} program, version {im.version:04x}, "
                f"{im.length} bytes, crc {im.crc:08x}")

    h = header(data)
    if h is not None:
        if h[3] == 0 and h[2] in SLOT_ADDR:
            return (f"{kind_name(h[0])} program, version {h[1]:04x}, linked for slot "
                    f"{slot_name(SLOT_ADDR.index(h[2]))}; not an image yet (no length, "
                    f"no checksum)")
        try:
            im = parse_image(data)
        except Bad as e:
            return f"a broken image: {e}"
        return f"image for slot {slot_name(im.slot)}: {program(im)}"
    try:
        im = parse_factory(data)
    except Bad as e:
        if len(data) == FACTORY_LEN:
            return f"a broken factory file: {e}"
    else:
        boot = len(data[:BOOT_LEN].rstrip(b"\xff"))
        return f"factory file: a bootloader of {boot} bytes; in slot A a {program(im)}"
    try:
        check_boot(data)
    except Bad:
        return "neither an image nor a factory file"
    return f"a program for 0x{FLASH_BASE:08x} of {len(data)} bytes: the bootloader alone, perhaps"


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise Bad(f"cannot read it: {e.strerror}") from None


def write(path, data):
    try:
        with open(path, "wb") as f:
            f.write(data)
    except OSError as e:
        raise Bad(f"cannot write it: {e.strerror}") from None


USAGE = """\
usage: mkimage.py image PROGRAM.bin OUT.img
       mkimage.py factory BOOT.bin SLOT_A.img OUT.bin
       mkimage.py show FILE..."""


def main(argv=None, stream=None):
    argv = sys.argv[1:] if argv is None else argv
    stream = stream or sys.stdout
    path = None
    try:
        if len(argv) == 3 and argv[0] == "image":
            path = argv[1]
            image = make_image(read(path))
            path = argv[2]
            write(path, image)
        elif len(argv) == 4 and argv[0] == "factory":
            path = argv[1]
            boot = read(path)
            check_boot(boot)
            path = argv[2]
            whole = make_factory(boot, read(path))
            path = argv[3]
            write(path, whole)
        elif len(argv) >= 2 and argv[0] == "show":
            for path in argv[1:]:
                print(f"{path}: {describe(read(path))}", file=stream)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except Bad as e:
        print(f"mkimage: {path}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
