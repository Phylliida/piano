"""Hub geometry: the single source of truth for the hub's board and case.

The hub is a box 45 mm wide that hangs on the E edge of a tile just as the
next tile's W side would: the same rail slots, latch window and cable channel
in its W wall, and the same plate, board and cover heights (all tilegeom's).
Its two USB-C sockets look north, so it wants no tile to its north-east.

Units mm, y up.  x runs east from the host tile's E outline edge; y is the
host tile's y.  Everything in the stack (plate on top, board, cover) is as in
tilegeom, and so are the names.

    python3 hubgeom.py          prints the numbers and checks them
"""

import math

import tilegeom as tg
from tilegeom import (SEAM_GAP, WALL_T, PCB_CLEAR, COVER_CLEAR, LIP_W, CATCH_D, CATCH_L,   # noqa: F401
                      RIB_W, POST_D, POST_KEEPOUT, PORT_SLOT_W, HEADER_FACE, CHANNEL_BACK,
                      PLUG_PROTRUSION, CABLE_GAP, PLA_E,
                      Z_PLATE, Z_PCB_TOP, Z_PCB_BOT, Z_COVER_TOP, Z_COVER_BOT, Z_FLOOR,
                      bbox, inside, overlaps, _rect, _rect_inside)

# ------------------------------------------------------------------ outline ---
WIDTH = 45.0
# North to south the hub is as long as the stretch where the host's E wall and
# the W wall of a tile standing there would both be: nothing of it then sits
# where a tile to the south-east could be.
Y_LO = max(tg._col_span(tg.COLS - 1)[0], tg._col_span(0)[0])
HEIGHT = min(tg._col_span(tg.COLS - 1)[1], tg._col_span(0)[1])
HOST_X = tg.side(tg.outline(), "E")[0]      # the hub's x = 0, in the host tile's coordinates

PCB_S = 29.0            # the board's south edge: the box is longer than the board needs

# ------------------------------------------------------------------ sockets ---
# Two HRO TYPE-C-31-M-12 on the board's back, opening north.  Each stands
# just inside the N wall, behind a window big enough for a plug's overmold
# (12.35 x 6.5 at most by the USB-C standard), so the board drops straight in
# and the wall stays closed below the plug.
USB_X = {"USB": 12.0, "PWR": 33.0}
USB_BODY = 3.65         # footprint origin to the socket's mouth
USB_AXIS = 1.65         # the socket's centre line below the board's back face
USB_BEHIND = 0.1        # the mouth, behind the wall's inner face
USB_WINDOW = (14.0, 7.4)
USB_LEG_DX, USB_LEG_DY = 4.32, (-3.13, 1.05)    # the shell's four soldered legs

# ---------------------------------------------------- lights and push buttons ---
# Through-hole parts on the board's top, reaching up through the plate.
UI_Y = 40.0
LEDS = {"STATUS": 8.0, "POWER": 15.0}           # x of each 3 mm LED
BUTTONS = {"USER": 26.0, "BOOT": 36.0}          # x of each 6 mm push button
LED_HOLE = 3.2          # the dome is 3.0; its 3.85 flange stays under the plate
LED_PITCH = 2.54
BUTTON_HOLE = 4.0       # the plunger is 3.5
BUTTON_PADS = (6.5, 4.5)                        # between a button's four legs
BUTTON_BODY = 7.0       # pocket in the plate's underside over each button's 6 mm body ...
BUTTON_POCKET = 0.6     # ... this deep: the body is as tall as the gap under the plate

# ----------------------------------------------------- what holds the board ---
# The board lies against ribs under the plate and is pushed onto them by the
# cover's posts.  With no switch pegs to place it, the N wall and short nubs
# do that: two on a rib across the south, two on each of the W and E walls.
# The rib itself stands FENCE_ROOM off the board, so only the nubs are near.
RIBS_Y = (47.5, 66.5, 88.0)                     # ribs across the whole width
POSTS = ((22.5, 102.5), (8.0, 66.5), (37.0, 66.5), (31.0, 47.5))
POST_PRELOAD = 0.1                              # half a tile's: four posts, and soft tabs
FENCE_T = 1.5                                   # the south fence rib
FENCE_ROOM = 1.0
NUB_W = 3.0                                     # a nub, along the edge it holds
NUB_CLEAR = PCB_CLEAR                           # a nub to the board's edge
NUBS_S_X = (9.0, 36.0)                          # nubs on the south rib ...
NUBS_W_Y = (47.5, 88.0)                         # ... on the W wall, each at a rib's end ...
NUBS_E_Y = (47.5, 79.0)                         # ... and on the E wall

# ------------------------------------------------------- what holds the cover ---
# W: the lip, as on a tile.  E: the wall is free here (no tile ever meets it),
# so it carries the springs itself: three tabs, each a strip of wall between
# two slits with a tile's catch bump at its foot.
#
# The first printed tile cover sat COVER_CLEAR from its walls all round, and
# bent its tray.  This one stands COVER_ROOM clear of every wall but for six
# ears: short stretches of its W and E edges that reach out to EAR_CLEAR,
# over the lip and over each tab's bump.  North to south it lies loose.
COVER_ROOM = 1.0
EAR_CLEAR = COVER_CLEAR                         # an ear to the wall
TAB_Y = (20.0, 58.0, 95.0)                      # the tabs, and the cover's ears on both edges
TAB_W = 8.0
TAB_SLIT = 1.0
EAR_W = 8.0


def outline(inset=0.0):
    """The hub's ideal outline shrunk by `inset`, CCW from the SW corner."""
    return [(inset, Y_LO + inset), (WIDTH - inset, Y_LO + inset),
            (WIDTH - inset, HEIGHT - inset), (inset, HEIGHT - inset)]


def case_outer():
    return outline(SEAM_GAP / 2)


def case_inner():
    return outline(SEAM_GAP / 2 + WALL_T)


def cover_outline():
    """The cover without its ears."""
    return outline(SEAM_GAP / 2 + WALL_T + COVER_ROOM)


def cover_ears():
    """Rectangles added to the cover's W and E edges, out to EAR_CLEAR from
    the wall: {"W": [...], "E": [...]}."""
    ix0, _, ix1, _ = bbox(case_inner())
    cx0, _, cx1, _ = bbox(cover_outline())
    spans = [(y - EAR_W / 2, y + EAR_W / 2) for y in TAB_Y]
    return {"W": [(ix0 + EAR_CLEAR, a, cx0, b) for a, b in spans],
            "E": [(cx1, a, ix1 - EAR_CLEAR, b) for a, b in spans]}


def pcb_outline():
    """The board's edge: PCB_CLEAR inside the N wall, clear of the lip on W and
    of the bumps on E so that it drops straight in, and cut short at PCB_S."""
    x0, _, x1, y1 = bbox(outline(SEAM_GAP / 2 + WALL_T + PCB_CLEAR))
    return [(x0 + LIP_W, PCB_S), (x1 - CATCH_D, PCB_S), (x1 - CATCH_D, y1), (x0 + LIP_W, y1)]


def to_kicad(x, y, margin=10.0):
    """Hub coordinates -> KiCad board coordinates (y down)."""
    x0, _, _, y1 = bbox(outline())
    return x - x0 + margin, y1 - y + margin


# --------------------------------------------------------------- tile port ---
def link():
    """The hub's one tile port: {"slot", "header", "facing"} as tilegeom's
    port_layout().  It faces the host's E header straight across the seam, a
    cable's length away, so the cable lies as it does between two tiles."""
    e = tg.port_layout()["E"]
    mouth = e["header"][0] + HEADER_FACE            # the host header's opening
    x = CABLE_GAP - (HOST_X - mouth) + HEADER_FACE
    y = e["header"][1]
    return {"slot": (0.0, y), "header": (x, y), "facing": (-1.0, 0.0)}


def port_slot():
    """The cut in the W wall (and the lip) that the cable lies in."""
    (x, y), h = link()["slot"], PORT_SLOT_W / 2
    return (x - 1, y - h, x + SEAM_GAP / 2 + WALL_T + 0.5 + LIP_W, y + h)


def cover_notch():
    """The cable channel cut out of the cover: from just behind the header's
    opening out through the W edge."""
    lay, h = link(), PORT_SLOT_W / 2
    (hx, hy) = lay["header"]
    return (lay["slot"][0] - 1, hy - h, hx - (HEADER_FACE - CHANNEL_BACK), hy + h)


def channel():
    """Where the board's back must stay bare: the cover is open there and the
    plug and cable lie under it."""
    n = cover_notch()
    x0 = bbox(pcb_outline())[0]
    return (x0, n[1], n[2], n[3])


# ----------------------------------------------------------------- sockets ---
def sockets():
    """{name: (x, y)} of each socket's footprint origin; it opens toward +y."""
    y = bbox(case_inner())[3] - USB_BEHIND - USB_BODY
    return {name: (x, y) for name, x in USB_X.items()}


def socket_legs():
    """Where the sockets' shell legs come through the board."""
    return [(x + sx * USB_LEG_DX, y + dy) for x, y in sockets().values()
            for sx in (-1, 1) for dy in USB_LEG_DY]


def usb_windows():
    """[(x0, x1, z0, z1)] of the windows in the N wall."""
    zc = -(Z_PCB_BOT + USB_AXIS)
    w, h = USB_WINDOW
    return [(x - w / 2, x + w / 2, zc - h / 2, zc + h / 2) for x, _ in sockets().values()]


# ------------------------------------------------------ lights and buttons ---
def leds():
    """{name: (x, y)} of each LED's centre.  Its legs are LED_PITCH apart in x."""
    return {name: (x, UI_Y) for name, x in LEDS.items()}


def buttons():
    """{name: (x, y)} of each push button's centre."""
    return {name: (x, UI_Y) for name, x in BUTTONS.items()}


def button_legs():
    dx, dy = BUTTON_PADS[0] / 2, BUTTON_PADS[1] / 2
    return [(x + sx * dx, y + sy * dy) for x, y in buttons().values() for sx in (-1, 1) for sy in (-1, 1)]


def led_legs():
    return [(x + s * LED_PITCH / 2, y) for x, y in leds().values() for s in (-1, 1)]


def plate_holes():
    """[(x, y, diameter)] through the plate."""
    return [(x, y, LED_HOLE) for x, y in leds().values()] + \
           [(x, y, BUTTON_HOLE) for x, y in buttons().values()]


def plate_pockets():
    """Rectangles let BUTTON_POCKET into the plate's underside."""
    return [_rect(x, y, BUTTON_BODY, BUTTON_BODY) for x, y in buttons().values()]


# ----------------------------------------------------- ribs, posts, fences ---
def ribs():
    """Plate-to-board ribs as rectangles: across the width where the board's
    top is bare, and a pad over the post between the sockets."""
    x0, _, x1, _ = bbox(case_inner())
    out = [(x0, y - RIB_W / 2, x1, y + RIB_W / 2) for y in RIBS_Y]
    out.append(_rect(*POSTS[0], POST_D + 1.0, POST_D + 1.0))
    return out


def posts():
    """Cover posts, each under a rib.  The board keeps POST_KEEPOUT bare of
    parts around each one."""
    return list(POSTS)


def nub_spans(centres):
    return [(c - NUB_W / 2, c + NUB_W / 2) for c in centres]


def fences():
    """Rectangles that reach from the plate down to the board's back face: a
    rib across the south, FENCE_ROOM off the board's edge, and the nubs that
    stand NUB_CLEAR off it, on that rib and on the W and E walls."""
    ix0, _, ix1, _ = bbox(case_inner())
    px0, py0, px1, _ = bbox(pcb_outline())
    y = py0 - NUB_CLEAR
    out = [(ix0, y - FENCE_ROOM - FENCE_T, ix1, y - FENCE_ROOM)]
    out += [(a, y - FENCE_ROOM, b, y) for a, b in nub_spans(NUBS_S_X)]
    out += [(ix0, a, px0 - NUB_CLEAR, b) for a, b in nub_spans(NUBS_W_Y)]
    out += [(px1 + NUB_CLEAR, a, ix1, b) for a, b in nub_spans(NUBS_E_Y)]
    return out


# ------------------------------------------------------------ W wall joints ---
def rail_slots():
    """y of the slots the host's two E rails run in."""
    return tg.rails()["E"]


def latch_windows():
    """y of the window the host's E latch springs into."""
    return tg.latches()["E"]


# -------------------------------------------------------------- cover tabs ---
def tabs():
    """[(y0, y1)] of the E wall's catch tabs."""
    return [(y - TAB_W / 2, y + TAB_W / 2) for y in TAB_Y]


def tab_slits():
    """[(y0, y1)] of the slits that free the tabs."""
    return [s for a, b in tabs() for s in ((a - TAB_SLIT, a), (b, b + TAB_SLIT))]


def tab_bumps():
    """[(y0, y1)] of the bump at each tab's foot."""
    return [(y - CATCH_L / 2, y + CATCH_L / 2) for y in TAB_Y]


def tab_flex():
    """(strain, newtons) as a tab is pushed out far enough to let the cover
    pass: a cantilever of the wall's thickness, from the plate to its bump."""
    d = CATCH_D - EAR_CLEAR
    a = (Z_COVER_BOT - EAR_CLEAR + CATCH_D) - Z_PLATE       # the plate's underside to the bump's crest
    return 1.5 * d * WALL_T / a ** 2, 3 * PLA_E * (TAB_W * WALL_T ** 3 / 12) * d / a ** 3


# ------------------------------------------------------------------- check ---
def check():
    """Raise if any of this cannot be built.  Returns notes worth printing."""
    notes = []
    inner, pcb, cover = bbox(case_inner()), bbox(pcb_outline()), bbox(cover_outline())
    assert Y_LO == 0.0
    lay = link()
    hx, hy = lay["header"]
    notes.append("link header at (%.2f, %.2f), its mouth at x = %.2f" % (hx, hy, hx - HEADER_FACE))
    assert pcb[0] + 1 < hx - HEADER_FACE and hx + 3.3 < pcb[2], "the link header is off the board"
    assert pcb[1] < hy - 7 and hy + 7 < pcb[3]

    # Sockets: on the board, their windows in the wall and apart.
    for (x, y), (x0, x1, z0, z1) in zip(sockets().values(), usb_windows()):
        assert pcb[0] + 1.0 < x - USB_LEG_DX - 0.5 and x + USB_LEG_DX + 0.5 < pcb[2] - 1.0
        assert y + USB_BODY - pcb[3] <= 0.5, "a socket overhangs the board too far"
        assert inner[0] + 2.0 <= x0 and x1 <= inner[2] + -2.0, "a window is too near a corner"
        assert -Z_COVER_BOT < z0 and z1 < -Z_PLATE - 1.0, "a window leaves too little wall"
    w = sorted(usb_windows())
    assert w[1][0] - w[0][1] >= 5.0, "the windows are too close together"

    # What stands on the board's top must miss the ribs; what the posts press
    # on must be bare; posts must stand on the cover, clear of its notch.
    top = [_rect(x, y, 5.0, 4.5) for x, y in leds().values()] + \
          [_rect(x, y, 9.5, 7.5) for x, y in buttons().values()] + \
          [_rect(x, y, 1.6, 2.6) for x, y in socket_legs()]
    for r in ribs():
        assert not any(overlaps(r, t) for t in top), "a rib lands on a part: %s" % (r,)
    notch = cover_notch()
    for x, y in posts():
        box = _rect(x, y, POST_D + 2.0, POST_D + 2.0)
        assert _rect_inside(box, cover_outline()) and not overlaps(box, notch), "post (%s, %s) is off the cover" % (x, y)
        assert _rect_inside(_rect(x, y, POST_D, POST_D), pcb_outline()), "post (%s, %s) is off the board" % (x, y)
        assert any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in ribs()), "post (%s, %s) has no rib over it" % (x, y)
        legs = socket_legs() + button_legs() + led_legs()
        assert all(math.hypot(x - a, y - b) > POST_KEEPOUT + 1.0 for a, b in legs), "post (%s, %s) is on a leg" % (x, y)
    for x, y, d in plate_holes():
        assert not any(r[0] - d / 2 - 1 < x < r[2] + d / 2 + 1 and r[1] - d / 2 - 1 < y < r[3] + d / 2 + 1
                       for r in ribs() + fences()), "a plate hole is on a rib"

    # W wall: slots, window, channel and nubs apart; E wall: tabs and nubs apart.
    half = tg.slot_half(SEAM_GAP + WALL_T) + tg.RAIL_FLARE
    west = [(y - half, y + half) for y in rail_slots()] + \
           [(y - tg.LATCH_W / 2 - 1, y + tg.LATCH_W / 2 + 1) for y in latch_windows()] + \
           [(port_slot()[1], port_slot()[3])] + nub_spans(NUBS_W_Y)
    east = [(a - TAB_SLIT, b + TAB_SLIT) for a, b in tabs()] + nub_spans(NUBS_E_Y)
    # Nothing that stands on the E wall may hold a tab: ribs and the south rib end clear of the slits.
    fixed = [(r[1], r[3]) for r in ribs() + fences()[:1] if r[2] >= inner[2] - 1e-9]
    for a, b in tabs():
        assert all(d <= a - TAB_SLIT - 0.5 or b + TAB_SLIT + 0.5 <= c for c, d in fixed), \
            "a rib ends on the tab at y = %.1f" % ((a + b) / 2)
    for name, spans, room in (("W", west, 1.0), ("E", east, 1.0)):
        spans = sorted(spans)
        assert all(b[0] - a[1] >= room for a, b in zip(spans, spans[1:])), "%s wall: features overlap: %s" % (name, spans)
        assert spans[0][0] > 0.5 and spans[-1][1] < HEIGHT - WALL_T - 1.0, "%s wall: a feature runs off its end" % name
    notes.append("S rail slot: %.2f mm wide at its mouth, which ends %.2f mm from the hub's S face"
                 % (2 * half, min(rail_slots()) - half))
    # The notch cuts the cover nearly in two: each part needs a tab of its own,
    # and an ear on the lip.  An ear covers its bump wherever the cover lies,
    # and no W ear hangs over the channel, where the lip is cut away.
    assert any(b < notch[1] for _, b in tabs()) and any(a > notch[3] for a, _ in tabs())
    ears = cover_ears()
    assert len(ears["E"]) == len(tab_bumps())
    for (_, a, _, b), (c, d) in zip(ears["E"], tab_bumps()):
        assert a + COVER_ROOM <= c and d <= b - COVER_ROOM, "an ear can slide off its bump"
    slot = port_slot()
    for x0, a, x1, b in ears["W"]:
        assert b + COVER_ROOM + 1.0 <= slot[1] or slot[3] <= a - COVER_ROOM - 1.0, "a W ear is over the channel"
        assert x1 - x0 > 0 and bbox(case_inner())[0] + LIP_W - x0 >= 1.0, "a W ear hardly reaches over the lip"
    for x0, y0, x1, y1 in fences()[1:]:
        assert x1 > x0 and y1 > y0
    strain, force = tab_flex()
    assert strain < 0.012, "a cover tab bends too far: %.2f %%" % (100 * strain)
    notes.append("cover tabs: %.2f %% strain, %.1f N each to push aside" % (100 * strain, force))
    assert WIDTH <= tg.PRINT_BED and HEIGHT <= tg.PRINT_BED
    return notes


def main():
    for n in check():
        print(n)
    pcb = bbox(pcb_outline())
    print("hub %.1f x %.3f mm; board %.2f x %.2f mm, x %.2f..%.2f, y %.2f..%.3f"
          % (WIDTH, HEIGHT, pcb[2] - pcb[0], pcb[3] - pcb[1], pcb[0], pcb[2], pcb[1], pcb[3]))
    for name, (x, y) in sockets().items():
        print("socket %-4s origin (%.2f, %.3f)" % (name, x, y))
    print("windows", [tuple(round(v, 2) for v in w) for w in usb_windows()])
    print("channel (no parts on the back)", tuple(round(v, 2) for v in channel()))


if __name__ == "__main__":
    main()
