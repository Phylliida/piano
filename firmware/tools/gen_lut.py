#!/usr/bin/env python3
"""Make core/lut_table.c: the table that turns a Hall sensor's reading into
key travel.

A sensor's output follows the magnet's field, and the field is far from
linear in distance: most of the change happens in the last millimetre before
the bottom. The firmware scales a reading to s = 0 (key up) .. 1 (key down)
and looks up the travel x = 0 .. 1 in the table made here.

The model: a cylinder magnet, magnetised along its axis, moving along that
axis straight at the sensor. On the axis its field at distance z from the
near face is

    B(z) = Br/2 * ( (z + L) / sqrt((z + L)^2 + R^2)  -  z / sqrt(z^2 + R^2) )

Br cancels out, since only the shape matters.

THE NUMBERS BELOW ARE GUESSES. Nothing has been measured: not the magnet in
the switch, not its distance from the sensor. Until somebody presses a real
switch against a ruler and fits these, travel is "roughly linear", not
millimetres. To fit them: log a tile's ADC values (`adc <tile> on` on the
hub's console) while lowering a key in known steps, then change the four
constants until model() matches, and run this script again.

    python3 tools/gen_lut.py            writes core/lut_table.c
    python3 tools/gen_lut.py --check    fails if the file is out of date

The generated file is committed, so building the firmware needs no Python.
"""

import math
import os
import sys

# Guesses, in mm. See above.
MAGNET_RADIUS = 1.4      # R
MAGNET_LENGTH = 3.4      # L
GAP_DOWN = 2.6           # magnet face to sensor with the key fully down
TRAVEL = 4.0             # how far the key moves

N = 65                   # table entries; the firmware interpolates between them
FULL = 65535


def field(z):
    r, l = MAGNET_RADIUS, MAGNET_LENGTH
    return (z + l) / math.hypot(z + l, r) - z / math.hypot(z, r)


def model(x):
    """Sensor swing s (0..1) at travel x (0 up .. 1 down)."""
    up = field(GAP_DOWN + TRAVEL)
    down = field(GAP_DOWN)
    return (field(GAP_DOWN + (1 - x) * TRAVEL) - up) / (down - up)


def inverse(s):
    """Travel x at which the swing is s. model() rises all the way, so halve
    the interval until it is found."""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if model(mid) < s:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def table(fn):
    out = [round(fn(i / (N - 1)) * FULL) for i in range(N)]
    out[0], out[-1] = 0, FULL
    assert all(a <= b for a, b in zip(out, out[1:])), "table must not fall"
    return out


def c_array(name, comment, values):
    lines = ["/* %s */" % comment, "const uint16_t %s[LUT_N] = {" % name]
    for i in range(0, len(values), 8):
        lines.append("    " + " ".join("%5d," % v for v in values[i:i + 8]))
    lines.append("};")
    return "\n".join(lines)


def render():
    head = """/* Made by tools/gen_lut.py. Do not edit: change the script and run it again.
 *
 * Model: cylinder magnet on its axis, radius %.2f mm, length %.2f mm,
 * %.2f mm from the sensor with the key down, %.2f mm of travel.
 * These are guesses; see the script. */
#include "lut.h"
""" % (MAGNET_RADIUS, MAGNET_LENGTH, GAP_DOWN, TRAVEL)
    return "\n".join([
        head,
        c_array("lut_travel", "sensor swing (index / 64) -> travel, 0..65535", table(inverse)),
        "",
        c_array("lut_field", "travel (index / 64) -> sensor swing: the model itself, for the simulator",
                table(model)),
        "",
    ])


def main():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core", "lut_table.c")
    text = render()
    if "--check" in sys.argv[1:]:
        with open(path) as f:
            if f.read() != text:
                sys.exit("core/lut_table.c is out of date: run tools/gen_lut.py")
        return
    with open(path, "w") as f:
        f.write(text)


if __name__ == "__main__":
    main()
