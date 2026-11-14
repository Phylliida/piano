#!/usr/bin/env python3
"""Tests for tools/mkimage.py.

    python3 tests/test_mkimage.py        (or: make test-tools)

mkimage.py says in Python what core/fw.c says in C: where the slots are and
what a whole image is. Three things keep the two the same:

  NumbersTest   the numbers in mkimage.py are those of core/fw.h, core/proto.h
                and the linker scripts, read from those files.
  CASES         one table of images, right ones and wrong ones, that both
                have to judge the same way: mkimage.parse() in ParseTest,
                fw_image_check() in AgreesWithCTest.
  AgreesWithCTest also spoils images at random and asks both.

fw_image_check() is reached through tests/fwcheck.c, a pipe; without
build/host/fwcheck (`make test-tools` builds it) AgreesWithCTest is skipped.
The files of a real build are looked at if they are there.
"""

import contextlib
import io
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(FIRMWARE, "tools"))

import mkimage  # noqa: E402
from mkimage import (Bad, BOOT_LEN, FACTORY_LEN, FLASH_BASE, HEADER, HEADER_AT,  # noqa: E402
                     IMAGE_MAGIC, IMAGE_MAX, IMAGE_MIN, RAM_BASE, RAM_END, SECTOR, SLOT_ADDR,
                     SLOT_LEN)

FWCHECK = os.path.join(FIRMWARE, "build", "host", "fwcheck")
A, B = SLOT_ADDR


def filler(size, seed):
    return random.Random(seed).getrandbits(8 * size).to_bytes(size, "little") if size else b""


def program(slot=0, size=3000, kind=0, version=0x0102, seed=1):
    """A pretend program as the linker leaves it: the two vectors, the
    header with no length in it, and anything for the rest."""
    b = bytearray(filler(size, seed))
    struct.pack_into("<II", b, 0, RAM_END, SLOT_ADDR[slot] + 0x221)
    HEADER.pack_into(b, HEADER_AT, IMAGE_MAGIC, kind, 0, version, SLOT_ADDR[slot], 0)
    return bytes(b)


def craft(slot=0, size=1000, kind=0, version=0x0102, stack=RAM_END, start=None, base=None,
          length=None, magic=IMAGE_MAGIC, seed=1):
    """`size` bytes with the checksum of all before it in the last four,
    made by hand: an image, unless an argument says something an image
    cannot say."""
    b = bytearray(filler(size, seed))
    struct.pack_into("<II", b, 0, stack, SLOT_ADDR[slot] + 0x221 if start is None else start)
    HEADER.pack_into(b, HEADER_AT, magic, kind, 0, version,
                     SLOT_ADDR[slot] if base is None else base, size if length is None else length)
    struct.pack_into("<I", b, size - 4, zlib.crc32(bytes(b[:size - 4])))
    return bytes(b)


def spoiled(data, at, flip=0x01):
    b = bytearray(data)
    b[at] ^= flip
    return bytes(b)


def boot(size=2000, seed=2):
    """A pretend bootloader."""
    b = bytearray(filler(size, seed))
    struct.pack_into("<II", b, 0, 0x20001000, FLASH_BASE + 0x201)
    if size >= HEADER_AT + 4:
        b[HEADER_AT:HEADER_AT + 4] = b"boot"
    return bytes(b)


# What sits in a slot: the bytes, and the slot they are in.
IMAGES = [
    ("plain", craft(), 0),
    ("for slot B", craft(slot=1), 1),
    ("the hub's", craft(kind=1, version=0xBEEF), 0),
    ("a kind nobody has", craft(kind=7), 0),            # the kind is commit's to judge
    ("the smallest", craft(size=IMAGE_MIN, start=A + 9), 0),
    ("the biggest", craft(size=IMAGE_MAX), 0),
    ("the biggest, in B", craft(slot=1, size=IMAGE_MAX), 1),
    ("stack at the bottom of RAM", craft(stack=RAM_BASE + 4), 0),
    ("start right behind the vectors", craft(start=A + 9), 0),
    ("start in the last halfword", craft(start=A + 1000 - 5), 0),
]

# And what is no image in its slot; last, a piece of what mkimage says of it.
NO_IMAGES = [
    ("no magic", craft(magic=IMAGE_MAGIC ^ 1), 0, "no image header"),
    ("erased", b"\xff" * 1000, 0, "no image header"),
    ("nothing", b"", 0, "no image header"),
    ("B's image in A", craft(slot=1), 0, None),         # mkimage: an image, for the other slot
    ("A's image in B", craft(), 1, None),
    ("linked for the bootloader's place", craft(base=FLASH_BASE, start=FLASH_BASE + 0x221), 0,
     "where no slot begins"),
    ("linked a word off", craft(base=A + 4), 0, "where no slot begins"),
    ("length not filled in", craft(length=0), 0, "not filled in"),
    ("length below the smallest", craft(length=IMAGE_MIN - 4, start=A + 9), 0,
     "which no image has"),
    ("length above the biggest", craft(size=IMAGE_MAX + 4), 0, "which no image has"),
    ("length huge", craft(length=0xFFFFFFFC), 0, "which no image has"),
    ("length not whole words", craft(size=1002), 0, "which no image has"),
    ("length a word short", craft(length=996), 0, "checksum"),
    ("length a word long", craft(length=1004), 0, "checksum"),
    ("stack at RAM's first word", craft(stack=RAM_BASE), 0, "stack pointer"),
    ("stack behind RAM", craft(stack=RAM_END + 4), 0, "stack pointer"),
    ("stack not on a word", craft(stack=RAM_END - 2), 0, "stack pointer"),
    ("stack 0", craft(stack=0), 0, "stack pointer"),
    ("stack erased", craft(stack=0xFFFFFFFF), 0, "stack pointer"),
    ("stack in flash", craft(stack=A + 0x400), 0, "stack pointer"),
    ("start not Thumb", craft(start=A + 0x220), 0, "start address"),
    ("start in the vectors", craft(start=A + 7), 0, "start address"),
    ("start at the base", craft(start=A + 1), 0, "start address"),
    ("start in the checksum", craft(start=A + 1000 - 3), 0, "start address"),
    ("start behind the image", craft(start=A + 1000 + 1), 0, "start address"),
    ("start in the other slot", craft(start=B + 0x221), 0, "start address"),
    ("start in the bootloader", craft(start=FLASH_BASE + 0x221), 0, "start address"),
    ("start 0", craft(start=0), 0, "start address"),
    ("start erased", craft(start=0xFFFFFFFF), 0, "start address"),
    ("checksum wrong", spoiled(craft(), 999), 0, "checksum"),
    ("a byte wrong", spoiled(craft(), 700, 0x80), 0, "checksum"),
    ("version changed afterwards", spoiled(craft(), HEADER_AT + 6), 0, "checksum"),
]

CASES = ([(name, data, slot, True, None) for name, data, slot in IMAGES]
         + [(name, data, slot, False, why) for name, data, slot, why in NO_IMAGES])


def in_slot(data):
    """All of a slot that begins with `data`."""
    return data + b"\xff" * (SLOT_LEN - len(data))


def judge(data, slot):
    """mkimage's word on a slot that holds `data`: the image, or None."""
    try:
        image = mkimage.parse(in_slot(data))
    except Bad:
        return None
    return image if image.slot == slot else None


class NumbersTest(unittest.TestCase):
    """mkimage.py's numbers are core/fw.h's."""

    @staticmethod
    def defines(path):
        with open(os.path.join(FIRMWARE, path)) as f:
            text = f.read()
        found = dict(re.findall(r"^#define[ \t]+(\w+)[ \t]+(.+?)[ \t]*(?:/\*.*)?$", text, re.M))

        def value(name):
            expr = re.sub(r"\b[A-Za-z_]\w*\b", lambda m: str(value(m.group())),
                          re.sub(r"\b(0x[0-9A-Fa-f]+|\d+)u\b", r"\1", found[name]))
            assert re.fullmatch(r"[0-9A-Fa-fx+\-*() ]+", expr), expr
            return eval(expr, {"__builtins__": {}})
        return value

    def test_the_chip_and_the_image(self):
        fw = self.defines("core/fw.h")
        for ours, theirs in [
                (FLASH_BASE, "FW_FLASH_BASE"), (SECTOR, "FW_SECTOR"), (BOOT_LEN, "FW_BOOT_LEN"),
                (SLOT_LEN, "FW_SLOT_LEN"), (A, "FW_SLOT_A_ADDR"), (B, "FW_SLOT_B_ADDR"),
                (mkimage.SLOTS_END, "FW_SLOTS_END"), (RAM_BASE, "FW_RAM_BASE"),
                (RAM_END, "FW_RAM_END"), (HEADER_AT, "FW_HEADER_AT"),
                (HEADER.size, "FW_HEADER_LEN"), (IMAGE_MAGIC, "FW_IMAGE_MAGIC"),
                (IMAGE_MIN, "FW_IMAGE_MIN"), (IMAGE_MAX, "FW_IMAGE_MAX")]:
            self.assertEqual(ours, fw(theirs), theirs)
        self.assertEqual(struct.pack("<I", IMAGE_MAGIC), b"PIMG")

    def test_the_header_fields(self):
        fw = self.defines("core/fw.h")
        packed = HEADER.pack(0x11111111, 0x22, 0, 0x3333, 0x44444444, 0x55555555)
        for name, value in [("FW_H_MAGIC", b"\x11" * 4), ("FW_H_KIND", b"\x22\x00"),
                            ("FW_H_VERSION", b"\x33" * 2), ("FW_H_BASE", b"\x44" * 4),
                            ("FW_H_LENGTH", b"\x55" * 4)]:
            self.assertEqual(packed[fw(name):fw(name) + len(value)], value, name)
        self.assertEqual(mkimage.H_LENGTH, fw("FW_H_LENGTH"))

    def test_the_kinds(self):
        with open(os.path.join(FIRMWARE, "core", "proto.h")) as f:
            text = f.read()
        kinds = {int(n): name.lower() for name, n in re.findall(r"KIND_(\w+) = (\d+)", text)}
        self.assertEqual(kinds, mkimage.KINDS)

    def test_the_linker_scripts(self):
        def flash(script):
            with open(os.path.join(FIRMWARE, "port", "at32f405", script)) as f:
                m = re.search(r"FLASH \(rx\)\s*: ORIGIN = (\w+), LENGTH = ([\w -]+)", f.read())
            return int(m.group(1), 16), eval(m.group(2).replace("K", "*1024"), {"__builtins__": {}})

        self.assertEqual(flash("boot.ld"), (FLASH_BASE, BOOT_LEN))
        # a program and the four bytes of its checksum
        self.assertEqual(flash("slot_a.ld"), (A, IMAGE_MAX - 4))
        self.assertEqual(flash("slot_b.ld"), (B, IMAGE_MAX - 4))

    def test_the_slots_fill_the_chip(self):
        self.assertEqual(B, A + SLOT_LEN)
        self.assertEqual(FACTORY_LEN, BOOT_LEN + 2 * SLOT_LEN)
        self.assertEqual(FACTORY_LEN, 0x40000 - 2 * SECTOR)     # a free sector, then the settings
        self.assertEqual(SLOT_LEN % SECTOR, 0)


class MakeImageTest(unittest.TestCase):
    def test_fills_in_length_and_checksum(self):
        p = program(size=3000)
        image = mkimage.make_image(p)
        self.assertEqual(len(image), 3004)
        at = HEADER_AT + mkimage.H_LENGTH
        self.assertEqual(image[:at], p[:at])
        self.assertEqual(struct.unpack_from("<I", image, at)[0], 3004)
        self.assertEqual(image[at + 4:3000], p[at + 4:])
        self.assertEqual(struct.unpack_from("<I", image, 3000)[0], zlib.crc32(image[:3000]))

    def test_says_what_it_made(self):
        for slot in (0, 1):
            for kind in (0, 1):
                im = mkimage.parse_image(mkimage.make_image(program(slot, 2000, kind, 0xA1B2)))
                self.assertEqual(im[:5], (kind, 0xA1B2, SLOT_ADDR[slot], slot, 2004))

    def test_pads_to_whole_words(self):
        for size in (3001, 3002, 3003):
            p = program(size=size)
            image = mkimage.make_image(p)
            self.assertEqual(len(image), 3008)
            self.assertEqual(image[size:3004], b"\xff" * (3004 - size))
            self.assertEqual(mkimage.parse_image(image).length, 3008)

    def test_as_big_as_a_slot_takes(self):
        image = mkimage.make_image(program(size=IMAGE_MAX - 4))
        self.assertEqual(len(image), IMAGE_MAX)
        for size in (IMAGE_MAX - 3, IMAGE_MAX, SLOT_LEN):
            with self.assertRaisesRegex(Bad, "a slot has room for"):
                mkimage.make_image(program(size=size))

    def test_not_a_program_for_a_slot(self):
        for data in (b"", boot(), program()[:HEADER_AT + 15], filler(3000, 5)):
            with self.assertRaisesRegex(Bad, "no image header"):
                mkimage.make_image(data)

    def test_not_twice(self):
        with self.assertRaisesRegex(Bad, "filled in already"):
            mkimage.make_image(mkimage.make_image(program()))

    def test_a_program_that_could_not_start(self):
        p = bytearray(program())
        struct.pack_into("<I", p, 0, 0x08001000)        # the stack, in flash
        with self.assertRaisesRegex(Bad, "stack pointer"):
            mkimage.make_image(bytes(p))
        p = bytearray(program(size=600))
        struct.pack_into("<I", p, 4, A + 0x401)         # starts behind its own end
        with self.assertRaisesRegex(Bad, "start address"):
            mkimage.make_image(bytes(p))
        p = bytearray(program())
        struct.pack_into("<I", p, HEADER_AT + 8, A + 0x100)
        with self.assertRaisesRegex(Bad, "where no slot begins"):
            mkimage.make_image(bytes(p))


class ParseTest(unittest.TestCase):
    def test_the_cases(self):
        for name, data, slot, good, why in CASES:
            with self.subTest(name):
                image = judge(data, slot)
                self.assertEqual(image is not None, good)
                if image:
                    kind, _, version, base, length = HEADER.unpack_from(data, HEADER_AT)[1:]
                    crc = struct.unpack_from("<I", data, length - 4)[0]
                    self.assertEqual(image, (kind, version, base, slot, length, crc))
                    self.assertEqual(mkimage.parse_image(data), image)
                if why:
                    with self.assertRaisesRegex(Bad, why):
                        mkimage.parse(in_slot(data))

    def test_the_other_slots_image_is_an_image(self):
        self.assertEqual(mkimage.parse(craft(slot=1)).slot, 1)

    def test_cut_short(self):
        image = craft()
        for n in (999, 996, HEADER_AT + 16):
            with self.assertRaisesRegex(Bad, "the header says 1000 bytes and there are %d" % n):
                mkimage.parse(image[:n])
        with self.assertRaisesRegex(Bad, "no image header"):
            mkimage.parse(image[:HEADER_AT + 15])

    def test_what_follows_an_image(self):
        image = craft()
        self.assertEqual(mkimage.parse(image + b"\x00" * 7), mkimage.parse(image))
        with self.assertRaisesRegex(Bad, "7 bytes follow the image"):
            mkimage.parse_image(image + b"\x00" * 7)

    def test_no_bit_may_change(self):
        image = craft(size=IMAGE_MIN + 40, start=A + 9)
        self.assertIsNotNone(judge(image, 0))
        for at in range(len(image)):
            for bit in range(8):
                self.assertIsNone(judge(spoiled(image, at, 1 << bit), 0), (at, bit))


class FactoryTest(unittest.TestCase):
    def setUp(self):
        self.boot = boot()
        self.image = mkimage.make_image(program(size=5000))

    def test_the_whole_chip_up_to_the_settings(self):
        whole = mkimage.make_factory(self.boot, self.image)
        self.assertEqual(len(whole), FACTORY_LEN)
        self.assertEqual(whole[:2000], self.boot)
        self.assertEqual(whole[2000:BOOT_LEN], b"\xff" * (BOOT_LEN - 2000))
        self.assertEqual(whole[BOOT_LEN:BOOT_LEN + 5004], self.image)
        self.assertEqual(whole[BOOT_LEN + 5004:], b"\xff" * (FACTORY_LEN - BOOT_LEN - 5004))
        self.assertEqual(mkimage.parse_factory(whole), mkimage.parse_image(self.image))

    def test_bootloader_sizes(self):
        mkimage.make_factory(boot(BOOT_LEN), self.image)
        mkimage.make_factory(boot(0x208), self.image)
        with self.assertRaisesRegex(Bad, "it has room for 8192"):
            mkimage.make_factory(boot(BOOT_LEN + 4), self.image)
        with self.assertRaisesRegex(Bad, "too short"):
            mkimage.make_factory(b"", self.image)

    def test_a_bootloader_that_ends_in_ff(self):
        b = boot(1000)[:997] + b"\xff\xff\xff"
        self.assertEqual(mkimage.parse_factory(mkimage.make_factory(b, self.image)),
                         mkimage.parse_image(self.image))

    def test_what_is_not_a_bootloader(self):
        with self.assertRaisesRegex(Bad, "a program for a slot where the bootloader belongs"):
            mkimage.make_factory(self.image, self.image)
        with self.assertRaisesRegex(Bad, "a program for a slot"):
            mkimage.make_factory(program(), self.image)
        with self.assertRaisesRegex(Bad, "stack pointer"):
            mkimage.make_factory(filler(2000, 9), self.image)
        b = bytearray(self.boot)
        struct.pack_into("<I", b, 4, A + 0x201)         # it starts in slot A
        with self.assertRaisesRegex(Bad, "start address"):
            mkimage.make_factory(bytes(b), self.image)

    def test_what_is_not_the_image_for_slot_a(self):
        with self.assertRaisesRegex(Bad, "for slot B"):
            mkimage.make_factory(self.boot, mkimage.make_image(program(slot=1)))
        with self.assertRaisesRegex(Bad, "not filled in"):
            mkimage.make_factory(self.boot, program())
        with self.assertRaisesRegex(Bad, "checksum"):
            mkimage.make_factory(self.boot, spoiled(self.image, 4000))
        with self.assertRaisesRegex(Bad, "follow the image"):
            mkimage.make_factory(self.boot, self.image + b"\xff\xff\xff\xff")
        with self.assertRaisesRegex(Bad, "no image header"):
            mkimage.make_factory(self.boot, self.boot)

    def refused(self, whole, why):
        with self.assertRaisesRegex(Bad, why):
            mkimage.parse_factory(whole)
        if len(whole) == FACTORY_LEN:
            self.assertEqual(mkimage.describe(whole)[:23], "a broken factory file: ")
            self.assertIn(why, mkimage.describe(whole))

    def test_files_that_are_not_factory_files(self):
        whole = mkimage.make_factory(self.boot, self.image)
        self.refused(whole[:-1], "a factory file has 258048")
        self.refused(whole + b"\xff", "a factory file has 258048")
        self.refused(self.image, "a factory file has 258048")
        self.refused(b"\xff" * BOOT_LEN + whole[BOOT_LEN:], "too short to be a program")
        self.refused(b"\xff" * FACTORY_LEN, "too short to be a program")
        self.refused(whole[:BOOT_LEN] + b"\xff" * (FACTORY_LEN - BOOT_LEN),
                     "behind the bootloader: no image header")
        self.refused(spoiled(whole, BOOT_LEN + 3000), "behind the bootloader: the checksum")
        b_image = mkimage.make_image(program(slot=1, size=5000))
        self.refused(whole[:BOOT_LEN] + b_image + whole[BOOT_LEN + 5004:], "linked for slot B")

    def test_nothing_but_the_image_in_the_slots(self):
        whole = mkimage.make_factory(self.boot, self.image)
        for at in (BOOT_LEN + 5004, BOOT_LEN + SLOT_LEN - 16, BOOT_LEN + SLOT_LEN,
                   FACTORY_LEN - 16, FACTORY_LEN - 1):
            with self.subTest(at=at):
                self.refused(spoiled(whole, at), "something follows the image in slot A")

    def test_the_bootloader_has_no_checksum(self):
        # nothing can tell that one of its bytes changed
        whole = spoiled(mkimage.make_factory(self.boot, self.image), 100)
        self.assertEqual(mkimage.parse_factory(whole), mkimage.parse_image(self.image))


class DescribeTest(unittest.TestCase):
    def test_what_each_file_is(self):
        image = mkimage.make_image(program(size=5000, version=0x0203))
        crc = "%08x" % zlib.crc32(image[:-4])
        said = "tile program, version 0203, 5004 bytes, crc " + crc
        self.assertEqual(mkimage.describe(image), "image for slot A: " + said)
        self.assertEqual(mkimage.describe(mkimage.make_factory(boot(), image)),
                         "factory file: a bootloader of 2000 bytes; in slot A a " + said)
        self.assertEqual(mkimage.describe(program(slot=1, kind=1, version=0x0203)),
                         "hub program, version 0203, linked for slot B; not an image yet "
                         "(no length, no checksum)")
        self.assertEqual(mkimage.describe(mkimage.make_image(program(slot=1, kind=1))),
                         "image for slot B: hub program, version 0102, 3004 bytes, crc %08x"
                         % zlib.crc32(mkimage.make_image(program(slot=1, kind=1))[:-4]))
        self.assertEqual(mkimage.describe(boot()),
                         "a program for 0x08000000 of 2000 bytes: the bootloader alone, perhaps")

    def test_what_is_broken_or_nothing(self):
        image = mkimage.make_image(program())
        self.assertEqual(mkimage.describe(spoiled(image, 1000)),
                         "a broken image: the checksum at its end is not that of its bytes")
        self.assertEqual(mkimage.describe(image[:-4]),
                         "a broken image: the header says 3004 bytes and there are 3000")
        self.assertEqual(mkimage.describe(image + b"\x00"),
                         "a broken image: 1 byte follows the image")
        self.assertEqual(mkimage.describe(image + b"\x00\x00"),
                         "a broken image: 2 bytes follow the image")
        self.assertEqual(mkimage.describe(mkimage.make_image(program(kind=9))),
                         "image for slot A: kind 9 program, version 0102, 3004 bytes, crc %08x"
                         % zlib.crc32(mkimage.make_image(program(kind=9))[:-4]))
        for data in (b"", b"hello", filler(3000, 4), b"\xff" * 3000, b"\x00" * 3000):
            self.assertEqual(mkimage.describe(data), "neither an image nor a factory file")
        self.assertEqual(mkimage.describe(b"\x00" * FACTORY_LEN),
                         "a broken factory file: its first word, the stack pointer, is "
                         "0x00000000, which is not in RAM")


class CommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def path(self, name, data=None):
        path = os.path.join(self.dir, name)
        if data is not None:
            with open(path, "wb") as f:
                f.write(data)
        return path

    def held(self, name):
        with open(self.path(name), "rb") as f:
            return f.read()

    def run_it(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = mkimage.main(list(argv), stream=out)
        return code, out.getvalue(), err.getvalue()

    def test_image(self):
        p = program()
        self.assertEqual(self.run_it("image", self.path("p.bin", p), self.path("p.img")),
                         (0, "", ""))
        self.assertEqual(self.held("p.img"), mkimage.make_image(p))

    def test_factory(self):
        image = mkimage.make_image(program())
        code = self.run_it("factory", self.path("boot.bin", boot()), self.path("a.img", image),
                           self.path("whole.bin"))
        self.assertEqual(code, (0, "", ""))
        self.assertEqual(self.held("whole.bin"), mkimage.make_factory(boot(), image))

    def test_show(self):
        image = mkimage.make_image(program())
        a, b = self.path("a.img", image), self.path("boot.bin", boot())
        code, out, err = self.run_it("show", a, b)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "%s: %s\n%s: %s\n" % (a, mkimage.describe(image),
                                                   b, mkimage.describe(boot())))

    def test_what_goes_wrong_is_said_and_nothing_is_written(self):
        image = mkimage.make_image(program())
        p, b = self.path("p.bin", program()), self.path("boot.bin", boot())
        a, bad = self.path("a.img", image), self.path("bad.img", spoiled(image, 900))
        slot_b = self.path("b.img", mkimage.make_image(program(slot=1)))
        gone, out = self.path("gone"), self.path("out")
        nowhere = os.path.join(self.dir, "no", "such", "place")
        for argv, said in [
                (("image", gone, out), gone + ": cannot read it: No such file or directory"),
                (("image", a, out), a + ": the header's length is filled in already"),
                (("image", b, out), b + ": no image header at 0x200: not a program linked "
                                        "for a slot"),
                (("image", p, nowhere), nowhere + ": cannot write it: "),
                (("factory", gone, a, out), gone + ": cannot read it: "),
                (("factory", a, a, out), a + ": a program for a slot where the bootloader "
                                             "belongs"),
                (("factory", b, gone, out), gone + ": cannot read it: "),
                (("factory", b, bad, out), bad + ": the checksum at its end"),
                (("factory", b, slot_b, out), slot_b + ": the image is for slot B"),
                (("factory", b, p, out), p + ": the header's length is not filled in"),
                (("show", a, gone), gone + ": cannot read it: ")]:
            with self.subTest(argv=argv):
                code, _, err = self.run_it(*argv)
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith("mkimage: " + said), err)
                self.assertEqual(err.count("\n"), 1)
                self.assertFalse(os.path.exists(out))

    def test_usage(self):
        for argv in ((), ("image",), ("image", "a"), ("image", "a", "b", "c"), ("factory", "a", "b"),
                     ("show",), ("make", "a", "b"), ("--help",)):
            code, out, err = self.run_it(*argv)
            self.assertEqual((code, out), (2, ""), argv)
            self.assertTrue(err.startswith("usage: mkimage.py image PROGRAM.bin OUT.img\n"))


BUILD = os.path.join(FIRMWARE, "build")
BUILT = [os.path.join(BUILD, *p) for p in (
    ("boot", "boot.bin"), ("tile", "tile_a.bin"), ("tile", "tile_a.img"), ("tile", "tile_b.img"),
    ("tile", "tile_factory.bin"), ("hub", "hub_a.bin"), ("hub", "hub_a.img"), ("hub", "hub_b.img"),
    ("hub", "hub_factory.bin"))]


@unittest.skipUnless(all(os.path.exists(p) for p in BUILT), "no build of the firmware here")
class RealBuildTest(unittest.TestCase):
    """What `make boot tile hub` left in build/, if it ran."""

    @staticmethod
    def held(*path):
        return mkimage.read(os.path.join(BUILD, *path))

    def test_the_images(self):
        with open(os.path.join(FIRMWARE, "core", "proto.h")) as f:
            version = int(re.search(r"#define FW_VERSION\s+(\w+?)u?\s", f.read()).group(1), 16)
        for kind, name in ((0, "tile"), (1, "hub")):
            a = mkimage.parse_image(self.held(name, name + "_a.img"))
            b = mkimage.parse_image(self.held(name, name + "_b.img"))
            self.assertEqual((a.kind, a.slot, a.version), (kind, 0, version))
            self.assertEqual((b.kind, b.slot, b.version), (kind, 1, version))
            self.assertEqual(a.length, b.length)
            self.assertNotEqual(a.crc, b.crc)           # linked for another address
            self.assertEqual(mkimage.make_image(self.held(name, name + "_a.bin")),
                             self.held(name, name + "_a.img"))

    def test_the_factory_files(self):
        boot_bin = self.held("boot", "boot.bin")
        mkimage.check_boot(boot_bin)
        for name in ("tile", "hub"):
            whole = self.held(name, name + "_factory.bin")
            image = self.held(name, name + "_a.img")
            self.assertEqual(mkimage.parse_factory(whole), mkimage.parse_image(image))
            self.assertEqual(whole, mkimage.make_factory(boot_bin, image))

    def test_the_bootloader_starts_in_itself(self):
        # and a program in its own slot, in a place the vector table is not
        stack, start = struct.unpack_from("<II", self.held("boot", "boot.bin"))
        self.assertTrue(RAM_BASE < stack <= RAM_END)
        self.assertTrue(FLASH_BASE + HEADER_AT <= start < FLASH_BASE + BOOT_LEN)
        for name in ("tile", "hub"):
            start = struct.unpack_from("<I", self.held(name, name + "_b.img"), 4)[0]
            self.assertTrue(B + HEADER_AT + HEADER.size <= start < B + IMAGE_MAX)


@unittest.skipUnless(os.path.exists(FWCHECK), "build/host/fwcheck is not built")
class AgreesWithCTest(unittest.TestCase):
    """fw_image_check() and mkimage.parse() say the same of every slot."""

    def setUp(self):
        self.c = subprocess.Popen([FWCHECK], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(self.c.wait)
        self.addCleanup(self.c.stdout.close)
        self.addCleanup(self.c.stdin.close)

    def c_judge(self, data, slot):
        """fw_image_check()'s word on a slot that holds `data`, as judge()
        gives mkimage's."""
        self.c.stdin.write(b"%d %s\n" % (slot, data.hex().encode()))
        self.c.stdin.flush()
        words = self.c.stdout.readline().split()
        if words == [b"bad"]:
            return None
        self.assertEqual(words[0], b"ok")
        kind, version, length, crc = (int(w, 16) for w in words[1:])
        return mkimage.Image(kind, version, SLOT_ADDR[slot], slot, length, crc)

    def test_the_cases(self):
        for name, data, slot, good, _ in CASES:
            with self.subTest(name):
                image = self.c_judge(data, slot)
                self.assertEqual(image is not None, good)
                self.assertEqual(image, judge(data, slot))

    def test_the_real_images(self):
        for name in ("tile", "hub"):
            for slot in (0, 1):
                path = os.path.join(BUILD, name, "%s_%s.img" % (name, "ab"[slot]))
                if not os.path.exists(path):
                    self.skipTest("no build of the firmware here")
                data = mkimage.read(path)
                self.assertEqual(self.c_judge(data, slot), mkimage.parse_image(data))
                self.assertIsNone(self.c_judge(data, 1 - slot))

    def test_spoiled_at_random(self):
        """Images with one to three things changed, and the checksum made
        right again for most: what is left for both to find is the change."""
        rng = random.Random(20261113)
        edges = {
            0: [0, 4, RAM_BASE, RAM_BASE + 4, RAM_END - 4, RAM_END - 2, RAM_END, RAM_END + 4,
                0xFFFFFFFF, A],
            4: [0, 1, 6, 7, 8, 9, 10, 11, 0x211, 0x221, 0x220, 0xFFFFFFFF, B + 0x221, A + 0x221],
            HEADER_AT + 8: [A, B, A + 4, FLASH_BASE, 0, B + SLOT_LEN],
            HEADER_AT + 12: [0, 4, IMAGE_MIN - 4, IMAGE_MIN, IMAGE_MIN + 4, IMAGE_MAX,
                             IMAGE_MAX + 4, SLOT_LEN, 0xFFFFFFFC, 0xFFFFFFFF],
        }
        seen = {True: 0, False: 0}
        for round_ in range(600):
            slot = rng.randrange(2)
            size = rng.choice((IMAGE_MIN, IMAGE_MIN + 4, 600, 1000, 2052, 4096))
            b = bytearray(craft(slot, size, start=SLOT_ADDR[slot] + 9, seed=round_))
            for _ in range(rng.choice((0, 1, 1, 1, 2, 3))):
                what = rng.randrange(6)
                if what == 0:                       # any bit
                    b[rng.randrange(size)] ^= 1 << rng.randrange(8)
                elif what == 1:                     # a bit of the vectors or the header
                    at = rng.choice((0, 4, HEADER_AT, HEADER_AT + 4, HEADER_AT + 8, HEADER_AT + 12))
                    b[at + rng.randrange(4)] ^= 1 << rng.randrange(8)
                else:                               # a field at one of its edges
                    at = rng.choice(sorted(edges))
                    value = rng.choice(edges[at])
                    if at == 4 and value < 0x1000:              # as an offset in the image
                        value += SLOT_ADDR[slot] + rng.choice((0, size - 12, size - 8, size - 4))
                    elif at == HEADER_AT + 12 and value < 8:    # about the true length
                        value = size + rng.choice((-8, -4, -2, 1, 4, 8)) * (value // 4 + 1)
                    struct.pack_into("<I", b, at, value & 0xFFFFFFFF)
            length = struct.unpack_from("<I", b, HEADER_AT + 12)[0]
            if rng.randrange(4) and 8 <= length <= size and length % 4 == 0:
                struct.pack_into("<I", b, length - 4, zlib.crc32(bytes(b[:length - 4])))
            where = slot if rng.randrange(8) else 1 - slot
            ours, theirs = judge(bytes(b), where), self.c_judge(bytes(b), where)
            self.assertEqual(ours, theirs, "round %d" % round_)
            seen[ours is not None] += 1
        # the rounds were worth it: plenty of both
        self.assertGreater(seen[True], 60)
        self.assertGreater(seen[False], 200)


if __name__ == "__main__":
    unittest.main()
