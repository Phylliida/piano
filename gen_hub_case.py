#!/usr/bin/env python3
"""Build the printed case for the hub from hubgeom.py.

Writes, into cad/:
  hub_tray.stl       plate, ribs and walls: the slots, window and cable channel
                     that hang it on a tile's E wall, the two USB-C windows,
                     the holes for its lights and buttons, the lip and the
                     three spring tabs that hold the cover
  hub_cover.stl      bottom cover: its ears, the four posts that clamp the
                     board, and the cut-out over the cable channel
  hub_pcb_dummy.stl  a printed stand-in for the board: its outline and
                     thickness, with a block where each USB-C socket and the
                     link header stand on its back
  hub.svg            the hub on a tile's E edge, from above, 2:1

Before writing anything it proves the fits on the solids themselves: the
cover and the board go in and are held, a USB-C plug fits each window, the
hub can be lowered onto a tile's E rails and is held there, and the cable has
an open channel from the tile's header to the hub's.

Print orientations are baked in, as for a tile (gen_case.py): the tray upside
down, plate face on the bed, without support; the cover as used, posts up;
the dummy with its blocks up.

Needs manifold3d and numpy, like gen_case.py.
"""

import argparse
import os

import gen_case as gc
from gen_case import m3d, prism, box, union, profile_along_y
import hubgeom as h
import tilegeom as g
from tilegeom import bbox

HERE = os.path.dirname(os.path.abspath(__file__))

# Stand-ins on the dummy board's back.  Sizes from the parts' drawings, to the
# nearest tenth: good for seeing where a part is, not for mating with it.
SOCKET_BLOCK = (8.9, 7.3)       # an HRO TYPE-C-31-M-12 shell: across, and back from its mouth
HEADER_BLOCK = (10.3, 4.1, 4.3)  # a JST SM06B-GHS-TB: across, back from its mouth, tall
USB_PLUG = (12.35, 6.5)         # the largest overmold the USB-C standard allows


# ------------------------------------------------------------------ parts ---
# Built in the hub's frame (hubgeom): x east from the host tile's E outline
# edge, y the host's, z up with the plate's top at z = 0.
def cylinder(x, y, d, z0, z1):
    return m3d.Manifold.cylinder(z1 - z0, d / 2, circular_segments=gc.SEGMENTS).translate([x, y, z0])


def on_w_wall(part, y):
    """A joint from gen_case's seam frame, on the hub's W wall at y."""
    return part.translate([-g.SEAM_GAP / 2, y, 0])


def tray():
    inner, outer = h.case_inner(), h.case_outer()
    ix0, iy0, ix1, iy1 = bbox(inner)
    ox1, oy1 = bbox(outer)[2:]
    walls = prism(outer, -g.Z_FLOOR, 0) - prism(inner, -g.Z_FLOOR - 1, 1)
    plate = prism(inner, -g.Z_PLATE, 0) \
        - union(cylinder(x, y, d, -g.Z_PLATE - 1, 1) for x, y, d in h.plate_holes()) \
        - union(box(r, -g.Z_PLATE - 1, -g.Z_PLATE + h.BUTTON_POCKET) for r in h.plate_pockets())
    ribs = [box(r, -g.Z_PCB_TOP, -g.Z_PLATE) for r in h.ribs()]
    fences = [box(r, -g.Z_PCB_BOT, -g.Z_PLATE) for r in h.fences()]

    # W lip, and a bump at the foot of each E tab: both as on a tile.  The
    # bump's sloping face starts where an ear's lower corner is.
    lip = box((ix0 - 0.5, iy0, ix0 + g.LIP_W, iy1), -g.Z_FLOOR, -g.Z_COVER_BOT)
    top = -(g.Z_COVER_BOT - h.EAR_CLEAR)
    profile = [(ix1 + 0.5, top), (ix1, top), (ix1 - g.CATCH_D, top - g.CATCH_D),
               (ix1, -g.Z_FLOOR), (ix1 + 0.5, -g.Z_FLOOR)]
    bumps = [profile_along_y(profile, y0, y1) for y0, y1 in h.tab_bumps()]

    # W wall: what a tile's W wall has.  E wall: the slits that free the tabs,
    # from the floor up to the plate.  N wall: a window before each socket.
    slots = [on_w_wall(gc.rail_slot(), y) for y in h.rail_slots()]
    windows = [on_w_wall(gc.latch_window(), y) for y in h.latch_windows()]
    channel = box(h.port_slot(), -g.Z_FLOOR - 1, -g.Z_PCB_BOT)
    slits = [box((ix1 - 0.1, a, ox1 + 0.1, b), -g.Z_FLOOR - 1, -g.Z_PLATE) for a, b in h.tab_slits()]
    usb = [box((x0, iy1 - 0.5, x1, oy1 + 0.5), z0, z1) for x0, x1, z0, z1 in h.usb_windows()]

    return union([walls, plate, lip] + ribs + fences + bumps) - union(slots + windows + [channel] + slits + usb)


def cover(preload=h.POST_PRELOAD):
    z0, z1 = -g.Z_COVER_BOT, -g.Z_COVER_TOP
    ears = h.cover_ears()
    body = union([prism(h.cover_outline(), z0, z1)]
                 + [box((a, b, c + 0.5, d), z0, z1) for a, b, c, d in ears["W"]]
                 + [box((a - 0.5, b, c, d), z0, z1) for a, b, c, d in ears["E"]])
    posts = union(cylinder(x, y, g.POST_D, z1, z1 + g.PARTS_CLEAR + preload) for x, y in h.posts())
    return (body - box(h.cover_notch(), z0 - 1, z1 + 1)) + posts


def socket_block(x, y):
    """Where a socket's shell is: from its mouth back, centred on its axis."""
    w, d = SOCKET_BLOCK
    mouth = y + h.USB_BODY
    return box((x - w / 2, mouth - d, x + w / 2, mouth), -g.Z_PCB_BOT - 2 * h.USB_AXIS, -g.Z_PCB_BOT + 0.2)


def header_block():
    w, d, tall = HEADER_BLOCK
    hx, hy = h.link()["header"]
    mouth = hx - g.HEADER_FACE
    return box((mouth, hy - w / 2, mouth + d, hy + w / 2), -g.Z_PCB_BOT - tall, -g.Z_PCB_BOT + 0.2)


def pcb_dummy():
    """The board's outline and thickness, and blocks for the three connectors
    on its back.  The lights and buttons on its top are left off: it prints
    flat on that face."""
    board = prism(h.pcb_outline(), -g.Z_PCB_BOT, -g.Z_PCB_TOP)
    return union([board, header_block()] + [socket_block(x, y) for x, y in h.sockets().values()])


# -------------------------------------------------------------- fit check ---
def host(part):
    """A tile's part, moved into the hub's frame: the tile the hub hangs on."""
    return part.translate([-h.HOST_X, 0, 0])


def cable_space():
    """Where the plugs and the cable lie, from the host's header to the hub's,
    and everything under it down to the table."""
    e = g.port_layout()["E"]
    x0 = e["header"][0] + g.HEADER_FACE - h.HOST_X
    hx, hy = h.link()["header"]
    x1 = hx - g.HEADER_FACE
    assert abs((x1 - x0) - g.CABLE_GAP) < 1e-6 and abs(hy - e["header"][1]) < 1e-6
    half = HEADER_BLOCK[0] / 2 + 0.5
    return box((x0, hy - half, x1, hy + half), -g.Z_FLOOR - 1, -g.Z_PCB_BOT)


def fit_check(tray_, cover_, pcb):
    """Prove the fits on the solids themselves.  Returns summary lines."""
    tiny = 1e-3                 # mm^3; anything more is two parts in one place

    def clash(a, b, dx=0.0, dy=0.0, dz=0.0):
        """Volume a shares with b moved by (dx, dy, dz)."""
        return (a ^ b.translate([dx, dy, dz])).volume()

    # The board: it sits in the tray, came in straight from below, and cannot
    # move off its place sideways or upward.
    assert clash(tray_, pcb) < tiny, "the board does not fit its tray"
    for k in range(1, 25):
        assert clash(tray_, pcb, dz=-k * 0.5) < tiny, "the board fouls %.1f mm below its seat" % (k * 0.5)
    for name, dx, dy in (("W", -0.5, 0), ("E", 0.5, 0), ("S", 0, -0.5), ("N", 0, 0.5)):
        assert clash(tray_, pcb, dx, dy) > 0.2, "nothing stops the board going %s" % name
    assert clash(tray_, pcb, dz=0.1) > 0.5, "the board does not lie on the ribs"

    # The cover: it fits wherever it lies in its room, the lip holds its W
    # ears and the bumps its E ears there, and its posts reach the board.
    # (Its room is north, south and west: eastward the ears lie against the
    # bumps' sloping faces already, which is what the tabs' spring is for.)
    flat = cover(preload=0.0)
    assert clash(pcb, cover_) < 4 * 3.2 * g.POST_D ** 2 / 4 * h.POST_PRELOAD + tiny, "the cover is in the board"
    assert clash(pcb, flat) < tiny and clash(pcb, flat, dz=0.05) > 1.0, "the posts do not reach the board"
    mid = h.WIDTH / 2
    halves = (("W lip", box((-5, -5, mid, h.HEIGHT + 5), -50, 50), 1.0),
              ("E bumps", box((mid, -5, h.WIDTH + 5, h.HEIGHT + 5), -50, 50), 0.5))
    dx, dy = h.EAR_CLEAR - 0.05, h.COVER_ROOM - 0.05
    for sx, sy in ((0, 0), (0, -dy), (0, dy), (-dx, 0), (-dx, -dy), (-dx, dy)):
        assert clash(tray_, flat, sx, sy) < tiny, "the cover does not fit, moved (%.2f, %.2f)" % (sx, sy)
        low = flat.translate([sx, sy, -0.5])
        for name, half, least in halves:
            assert clash(tray_ ^ half, low) > least, "the %s do not hold the cover, moved (%.2f, %.2f)" % (name, sx, sy)

    # A plug as fat as the standard allows goes through each window.
    oy1 = bbox(h.case_outer())[3]
    for name, (x, y) in h.sockets().items():
        w, t = USB_PLUG
        z = -(g.Z_PCB_BOT + h.USB_AXIS)
        plug = box((x - w / 2, y + h.USB_BODY, x + w / 2, oy1 + 10), z - t / 2, z + t / 2)
        assert clash(tray_, plug) < tiny and clash(cover_, plug) < tiny, "a plug does not fit the %s window" % name

    # On a tile's E edge.  Nothing of the hub is inside the tile; the way down
    # onto its rails is clear but for the latch's hook; seated, the hook holds
    # it down, the rails carry it and the dovetails keep it against the wall.
    full, bare = host(gc.tray()), host(gc.tray(hooks=False))
    host_cover, host_pcb = host(gc.cover()), host(gc.pcb_dummy())
    for part, name in ((tray_, "tray"), (cover_, "cover"), (pcb, "board")):
        for other in (full, host_cover, host_pcb):
            assert clash(other, part) < tiny, "the hub's %s is inside its tile" % name
    air = full.min_gap(tray_, 1.0)
    assert air > 0.1, "hub and tile only %.3f mm apart" % air
    for k in range(1, 31):
        assert clash(bare, tray_, dz=k * 0.5) < tiny, "the hub fouls %.1f mm above its seat" % (k * 0.5)
    assert clash(full, tray_, dz=1.0) > 0.5, "the latch does not hold the hub"
    assert clash(bare, tray_, dz=-0.5) > 0.05, "the rails do not carry the hub"
    assert clash(bare, tray_, dx=0.5) > 0.5, "the dovetails do not hold the hub"
    assert clash(bare, tray_, dy=0.5) > 0.5 and clash(bare, tray_, dy=-0.5) > 0.5, "the rails do not locate the hub"

    # Tiles that may stand around it: south-east and north-east of its tile.
    # (The north-east one would stand right before the sockets.)
    px, py = g.PERIOD
    near = min(tray_.min_gap(full.translate([px, s * py, 0]), 15.0) for s in (-1, 1))
    assert near > 0.1, "a tile beside the hub's place is only %.3f mm from it" % near

    # The cable: open from one header to the other, and from there down.
    space = cable_space()
    for part, name in ((tray_, "the hub's tray"), (cover_, "the hub's cover"), (pcb, "the hub's board"),
                       (full, "the tile's tray"), (host_cover, "the tile's cover"), (host_pcb, "the tile's board")):
        assert clash(part, space) < tiny, "%s is in the cable's way" % name

    return ["fit           OK (solids: board and cover go in and are held, with the cover anywhere "
            "in its %.1f mm of room; a %.2f x %.1f mm plug fits each window)" % (h.COVER_ROOM, *USB_PLUG),
            "on a tile     OK (solids: %.2f mm from the tile's wall, a clear way down onto its rails, "
            "held once seated; cable channel open; tiles south-east and north-east of that tile "
            "clear it by %.2f mm)" % (air, near)]


# ---------------------------------------------------------------- drawing ---
TINTS = ("#9db7dd", "#5f7fb0", "#86b386")           # tray, cover, board ...
HOST_TINTS = ("#ecc3a6", "#c0825c", "#86b386")      # ... and the tile's
CUT_X, CUT_TAB = 37.0, h.TAB_Y[1]                   # where two of the three sections are cut


def hub_svg(path, parts, host_parts, scale=3.0):
    """The hub on its tile's E edge: a plan from above, drawn from hubgeom,
    and three sections cut through the solids `parts` (tray, cover, board)
    and `host_parts`.  The sections line up with the plan."""
    x0, x1 = -8.0, h.WIDTH + 1.0                    # the plan's window: a strip of the tile, and the hub
    y0, y1 = -1.0, h.HEIGHT + 5.0
    deep = g.Z_FLOOR + 2.0                          # a section's depth, with a margin either side
    pad, head = 3.0, 4.0
    cut_latch = h.latch_windows()[0]
    wide, tall = x1 - x0, y1 - y0
    w = pad + wide + pad + deep + pad
    hgt = head + tall + 2 * (head + deep) + head
    px, py = pad, head                              # the plan's corner on the page
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" height="%.1fmm" '
           'viewBox="0 0 %.3f %.3f" font-family="sans-serif">' % (w * scale, hgt * scale, w, hgt),
           '<rect width="%.3f" height="%.3f" fill="#fff"/>' % (w, hgt)]

    def X(x):
        return px + x - x0

    def Y(y):
        return py + y1 - y

    def rect(r, fill, extra=""):
        out.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="%s"%s/>'
                   % (X(r[0]), Y(r[3]), r[2] - r[0], r[3] - r[1], fill, extra))

    def poly(pts, extra):
        out.append('<polygon points="%s" %s/>' % (" ".join("%.3f,%.3f" % (X(x), Y(y)) for x, y in pts), extra))

    def text(x, y, words, size=1.3, anchor="middle", extra=""):
        out.append('<text x="%.3f" y="%.3f" font-size="%.2f" text-anchor="%s" fill="#334"%s>%s</text>'
                   % (x, y, size, anchor, extra, words))

    # ---- plan
    out.append('<clipPath id="plan"><rect x="%.3f" y="%.3f" width="%.3f" height="%.3f"/></clipPath>'
               % (px, py, wide, tall))
    out.append('<g clip-path="url(#plan)">')
    rect((x0, y0, x1, y1), "#fafafa")
    ox0, oy0, ox1, oy1 = bbox(h.case_outer())
    ix0, iy0, ix1, iy1 = bbox(h.case_inner())
    rect((x0 - 1, y0 - 1, -g.SEAM_GAP / 2, y1 + 1), HOST_TINTS[0])                 # the tile
    rect((ox0, oy0, ox1, oy1), TINTS[0])
    rect((ix0, iy0, ix1, iy1), "#fff")
    for r in h.ribs() + h.fences():
        rect(r, "#d5dfee")
    rect((ix0, iy0, ix0 + g.LIP_W, iy1), "#b9cbe6")                                # the lip
    rect((ox0, h.port_slot()[1], h.port_slot()[2], h.port_slot()[3]), "#fff")
    for a, b_ in h.tab_slits():
        rect((ix1 - 0.1, a, ox1 + 0.1, b_), "#fff")
    for (a, b_) in h.tab_bumps():
        rect((ix1 - g.CATCH_D, a, ix1, b_), "#e08a2e")
    for xa, xb, _, _ in h.usb_windows():
        rect((xa, iy1 - 0.1, xb, oy1 + 0.1), "#fff")
    for y in h.rail_slots():                                                       # the tile's rails
        half = g.rail_half(g.RAIL_REACH)
        rect((-g.SEAM_GAP / 2, y - half, -g.SEAM_GAP / 2 + g.RAIL_REACH, y + half), "#2d5fa8")
    for y in h.latch_windows():                                                    # ... and its latch
        rect((-g.SEAM_GAP / 2 - g.WALL_T, y - g.LATCH_W / 2, g.SEAM_GAP / 2 + g.LATCH_BITE, y + g.LATCH_W / 2), "#2f8f5b")
    poly(h.pcb_outline(), 'fill="%s" fill-opacity="0.45" stroke="#4d7a4d" stroke-width="0.15"' % TINTS[2])
    for x, y in h.sockets().values():
        rect((x - SOCKET_BLOCK[0] / 2, y + h.USB_BODY - SOCKET_BLOCK[1], x + SOCKET_BLOCK[0] / 2, y + h.USB_BODY), "#8a8f98")
    hx, hy = h.link()["header"]
    mouth = hx - g.HEADER_FACE
    rect((mouth, hy - HEADER_BLOCK[0] / 2, mouth + HEADER_BLOCK[1], hy + HEADER_BLOCK[0] / 2), "#8a8f98")
    rect((x0 - 1, hy - 1.6, mouth, hy + 1.6), "#d0563b", ' fill-opacity="0.8"')    # the cable
    for x, y, d in h.plate_holes():
        out.append('<circle cx="%.3f" cy="%.3f" r="%.2f" fill="#fff" stroke="#334" stroke-width="0.2"/>'
                   % (X(x), Y(y), d / 2))
    for x, y in h.posts():
        out.append('<circle cx="%.3f" cy="%.3f" r="%.2f" fill="#556"/>' % (X(x), Y(y), g.POST_D / 2))
    for pts in parts[1].slice(-(g.Z_COVER_TOP + g.Z_COVER_BOT) / 2).to_polygons():
        poly(pts, 'fill="none" stroke="%s" stroke-width="0.25" stroke-dasharray="1 0.6"' % TINTS[1])
    out.append("</g>")
    for name, x in h.USB_X.items():
        text(X(x), Y(oy1) - 1.2, name, extra=' font-weight="bold"')
    for name, (x, y) in list(h.leds().items()) + list(h.buttons().items()):
        text(X(x), Y(y) + 4.6, name.lower(), size=1.1)
    text(X(mouth - 12), Y(hy) - 2.2, "cable to the tile", size=1.1)
    text(X(x0) + 0.4, Y(y1) + 1.6, "tile", size=1.1, anchor="start")
    text(px, py - 1.2, "from above", size=1.1, anchor="start")
    text(px, hgt - 1.2, "from above: the board green, the cover dashed, its posts dark, the tabs' bumps orange; "
         "the tile's rails blue, its latch dark green", size=1.1, anchor="start")

    # ---- sections, from the solids
    solids = list(zip(host_parts, HOST_TINTS)) + list(zip(parts, TINTS))
    near = box((x0, -1e3, 1e3, 1e3), -1e3, 1e3)

    def paths(cuts, place):
        for polys, tint in cuts:
            d = " ".join("M" + " L".join("%.3f %.3f" % place(*pt) for pt in pl) + " Z" for pl in polys)
            if d:
                out.append('<path d="%s" fill="%s" fill-rule="evenodd"/>' % (d, tint))

    def frame(bx, by, bw, bh, title):
        out.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="none" stroke="#445" '
                   'stroke-width="0.08"/>' % (bx, by, bw, bh))
        text(bx, by - 1.2, title, size=1.1, anchor="start")

    # along the hub: depth to the right, y as in the plan
    sx = px + wide + pad
    out.append('<clipPath id="along"><rect x="%.3f" y="%.3f" width="%.3f" height="%.3f"/></clipPath>'
               '<g clip-path="url(#along)">' % (sx, py, deep, tall))
    out.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="#fafafa"/>' % (sx, py, deep, tall))
    paths([(part.rotate([0, -90, 0]).slice(CUT_X).to_polygons(), tint) for part, tint in zip(parts, TINTS)],
          lambda depth, y: (sx + 1.0 + depth, Y(y)))
    out.append("</g>")
    frame(sx, py, deep, tall, "cut at x = %.0f" % CUT_X)

    # across it: x as in the plan, depth downward
    for k, (y, title) in enumerate(((CUT_TAB, "cut across a tab, y = %.0f: the cover's ears on the lip and on the tab's bump" % CUT_TAB),
                                    (cut_latch, "cut across the tile's latch, y = %.0f: its hook in the hub's window; "
                                                "here the cover stands %.0f mm off both walls" % (cut_latch, h.COVER_ROOM)))):
        sy = py + tall + head + k * (head + deep)
        out.append('<clipPath id="across%d"><rect x="%.3f" y="%.3f" width="%.3f" height="%.3f"/></clipPath>'
                   '<g clip-path="url(#across%d)">' % (k, px, sy, wide, deep, k))
        out.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="#fafafa"/>' % (px, sy, wide, deep))
        paths([((part ^ near).rotate([90, 0, 0]).slice(y).to_polygons(), tint) for part, tint in solids],
              lambda x, depth: (X(x), sy + 1.0 + depth))
        out.append("</g>")
        frame(px, sy, wide, deep, title)
    out.append("</svg>")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(HERE, "cad"))
    ap.add_argument("--xy-scale", type=float, default=1.0,
                    help="shrink compensation from the tile's plate_test print, e.g. 1.003")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print("\n".join(g.check() + h.check()))
    parts = tray(), cover(), pcb_dummy()
    print("\n".join(fit_check(*parts)) + "\n")
    gc.emit(args.out, "hub_tray", parts[0], True, args.xy_scale)
    gc.emit(args.out, "hub_cover", parts[1], False, args.xy_scale)
    gc.emit(args.out, "hub_pcb_dummy", parts[2], True, args.xy_scale)
    svg = os.path.join(args.out, "hub.svg")
    hub_svg(svg, parts, [host(p) for p in (gc.tray(), gc.cover(), gc.pcb_dummy())])
    print("hub           on a tile, from above and in section -> %s" % os.path.relpath(svg, HERE))


if __name__ == "__main__":
    main()
