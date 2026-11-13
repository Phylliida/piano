#!/usr/bin/env python3
"""Build the printed case for one tile from tilegeom.py.

Writes, into cad/:
  tray.stl          plate, ribs and walls; the W lip and E bumps that hold the
                    cover; the rails, slots, latches and windows that join it
                    to its neighbours; the cable channels
  cover.stl         bottom cover: catch beams, the posts that clamp the PCB,
                    and a cut-out over each cable channel
  plate_test.stl    the 1.5 mm top plate alone: a short print to check switch
                    fit and measure shrinkage
  pcb_dummy.stl     a printed stand-in for the PCB: its outline, thickness and
                    switch-peg holes, for testing the case before boards exist
  coupon_tray.stl   the same tray, cover and dummy PCB for a 2 x 4-key tile:
  coupon_cover.stl  every snap feature of the full tile (it has no ports), a
  coupon_pcb_dummy.stl  fraction of the print.  Tune the fits on these -- two
                    coupon trays join E to W and N to S like two tiles.
  preview.svg       five tiles in an L: keycaps with their lattice coordinates,
                    ports, cover posts, rails and latches.  1:1 scale in mm.
  joints.svg        two joined tiles cut through a latch and through a rail,
                    8:1, taken from the same solids as the STLs

Before writing anything it proves the fits on the solids themselves: tiles on
the grid clear each other, a tile can be lowered onto its neighbours' rails,
the latches and dovetails hold it there, the cover and the PCB go in.

Print orientations are baked in.  The tray prints upside down, plate face on
the bed: the tile turned 180 degrees about x -- a turn, not a mirror, so the
stepped edges and the lip/catch sides come out the right way round.  Print it
without support: the rails' heads, the hooks and the bumps overhang at 45
degrees or less, and the bar over each latch window is an 11 mm bridge.  Only
the W lip is a plain ledge, 1.5 mm out from its wall; its underside comes out
rough, and it is the face the cover rests on.  The cover prints as used,
posts up.

Needs manifold3d and numpy.  On NixOS numpy may fail to find libz.so.1: put
a zlib lib directory on LD_LIBRARY_PATH.
"""

import argparse
import math
import os
import sys

try:
    import manifold3d as m3d
    import numpy as np
except ImportError as e:
    sys.exit("gen_case.py needs manifold3d and numpy (%s).\nOn NixOS a numpy "
             "error about libz.so.1 means zlib is missing from LD_LIBRARY_PATH." % e)

import tilegeom as g

HERE = os.path.dirname(os.path.abspath(__file__))
SEGMENTS = 48           # facets per full circle
DENSITY = 1.24          # g/cm^3, PLA
DUMMY_PEG_HOLE = 2.0    # the dummy's peg holes: printed holes come out undersize,
                        # so 2.0 printed stands in for the real board's 1.75 drilled


# ------------------------------------------------------------- primitives ---
# Built in the tile's own frame: z up, plate top at z = 0, the tray below it.
def prism(poly, z0, z1):
    return m3d.CrossSection([poly]).extrude(z1 - z0).translate([0, 0, z0])


def box(r, z0, z1):
    return m3d.Manifold.cube([r[2] - r[0], r[3] - r[1], z1 - z0]).translate([r[0], r[1], z0])


def union(parts):
    parts = list(parts)
    return m3d.Manifold.batch_boolean(parts, m3d.OpType.Add) if parts else m3d.Manifold()


def profile_along_y(pts, y0, y1):
    """Extrude an (x, z) profile along y from y0 to y1."""
    if g.area(pts) < 0:
        pts = pts[::-1]
    # rotate 90 about x maps (u, v, w) -> (u, -w, v): profile (x, z), extrusion -y
    return m3d.CrossSection([pts]).extrude(y1 - y0).rotate([90, 0, 0]).translate([0, y1, 0])


def stadium(xc, yc, length, width, z0, z1):
    """Round-ended slot along y."""
    r, half = width / 2, length / 2 - width / 2
    ends = [m3d.Manifold.cylinder(z1 - z0, r, circular_segments=SEGMENTS)
            .translate([xc, yc + s * half, z0]) for s in (-1, 1)]
    return union([box((xc - r, yc - half, xc + r, yc + half), z0, z1)] + ends)


# ----------------------------------------------------------------- joints ---
# Each is built once in its seam's frame (tilegeom: x = n, outward from the
# outer face of the wall that carries the rails; y = t, along the wall) and
# put on a wall by on_seam().  All are symmetric in t.
def on_seam(part, edge, t):
    face = g.seam_face(edge, t)
    if edge in "EW":
        return part.translate([face, t, 0])
    return part.rotate([0, 0, 90]).translate([t, face, 0])


def on_seams(part, places, edge_of=lambda e: e):
    return [on_seam(part, edge_of(e), t) for e, ts in places.items() for t in ts]


def rail():
    """A dovetail rail from the floor up: 45 degree flanks, and at its head a
    45 degree bevel back to the wall, which prints without support."""
    pts = []
    for n, z_top in ((-0.2, -g.RAIL_TOP + g.RAIL_REACH + 0.2), (g.RAIL_REACH, -g.RAIL_TOP)):
        pts += [(n, s * g.rail_half(n), z) for s in (-1, 1) for z in (-g.Z_FLOOR, z_top)]
    return m3d.Manifold.hull_points(np.array(pts))


def rail_slot():
    """The slot a rail runs in, through the neighbour's wall: the rail's
    section plus RAIL_CLEAR, a flared mouth at the floor, a closed top."""
    n0, n1 = g.SEAM_GAP - 0.5, g.SEAM_GAP + g.WALL_T + 0.02

    def section(z, wider=0.0):
        return [(n, s * (g.slot_half(n) + wider), z) for n in (n0, n1) for s in (-1, 1)]

    z, flare = -g.Z_FLOOR, g.RAIL_FLARE
    body = section(z - 1) + section(g.rail_slot_top())
    mouth = section(z - 1, flare) + section(z, flare) + section(z + 2 * flare)
    return union(m3d.Manifold.hull_points(np.array(pts)) for pts in (body, mouth))


def latch_cuts():
    """What turns a strip of wall into a latch beam: a slit either side, a
    wedge off its inner face so that it thins toward the floor, and its end
    trimmed clear of the table."""
    half, slit = g.LATCH_W / 2, g.LATCH_SLIT
    n0, n1, z0 = -g.WALL_T - 0.1, 0.1, -g.Z_FLOOR - 1
    slits = [box((n0, s * (half + slit / 2) - slit / 2, n1, s * (half + slit / 2) + slit / 2),
                 z0, -g.Z_PLATE) for s in (-1, 1)]
    wedge = [(n0, -g.Z_PLATE), (-g.WALL_T, -g.Z_PLATE), (-g.WALL_T + g.latch_taper(z0), z0), (n0, z0)]
    span = half + slit / 2
    return union(slits + [profile_along_y(wedge, -span, span),
                          box((n0, -span, n1, span), z0, -g.Z_FLOOR + g.LATCH_LIFT)])


def latch_hook():
    """The hook on a latch beam's outer face, rooted 0.2 mm inside the beam."""
    outline = g.latch_hook()
    outline += [(-0.2, outline[-1][1]), (-0.2, outline[0][1])]
    return profile_along_y(outline, -g.LATCH_W / 2, g.LATCH_W / 2)


def latch_window():
    """The window in the neighbour's wall that a hook springs into."""
    half = g.LATCH_W / 2 + 0.5
    return box((g.SEAM_GAP - 0.5, -half, g.SEAM_GAP + g.WALL_T + 0.02, half), *g.latch_window())


# ------------------------------------------------------------------ parts ---
def tray(hooks=True):
    """hooks=False leaves the latches' hooks off: fit_check() uses it to show
    that nothing else stands in a tile's way down."""
    inner, outer = g.case_inner(), g.case_outer()
    walls = prism(outer, -g.Z_FLOOR, 0) - prism(inner, -g.Z_FLOOR - 1, 1)
    plate = prism(inner, -g.Z_PLATE, 0) - union(box(r, -g.Z_PLATE - 1, 1) for r in g.switch_cutouts())
    ribs = union(box(r, -g.Z_PCB_TOP, -g.Z_PLATE) for r in g.ribs()) ^ prism(inner, -g.Z_PCB_TOP, -g.Z_PLATE)

    # W lip: a ledge the cover's W edge tucks over.
    x_w, w_lo, w_hi = g.side(inner, "W")
    lip = box((x_w - 0.5, w_lo, x_w + g.LIP_W, w_hi), -g.Z_FLOOR, -g.Z_COVER_BOT)

    # E bumps: a 45 degree retaining face that just meets the cover's bottom
    # corner (so a firm pry releases it), then a ramp down to the wall's foot
    # that pushes the catch beam aside on the way in.
    x_e = g.side(inner, "E")[0]
    top = -(g.Z_COVER_BOT - g.COVER_CLEAR)
    profile = [(x_e + 0.5, top), (x_e, top), (x_e - g.CATCH_D, top - g.CATCH_D),
               (x_e, -g.Z_FLOOR), (x_e + 0.5, -g.Z_FLOOR)]
    bumps = [profile_along_y(profile, y0, y1) for y0, y1 in g.catch_bumps()]

    # Rails on E and N; their slots on W and S, the same distance along.
    rails = on_seams(rail(), g.rails())
    slots = on_seams(rail_slot(), g.rails(), g.OPPOSITE.get)

    # Latch beams on E and N, their windows on W and S.
    beams = on_seams(latch_cuts(), g.latches())
    windows = on_seams(latch_window(), g.latches(), g.OPPOSITE.get)

    # Cable channels: the wall is open from the PCB's back down to the floor.
    channels = [box(r, -g.Z_FLOOR - 1, -g.Z_PCB_BOT) for r in g.port_slots()]
    x_out = g.side(outer, "E")[0]
    pry = [box((x_e - 0.5, y0, x_out + 0.5, y1), -g.Z_FLOOR - 1, -g.Z_COVER_BOT)
           for y0, y1 in g.pry_notches()]

    part = union([walls, plate, ribs, lip] + bumps + rails) - union(slots + beams + windows + channels + pry)
    return part + union(on_seams(latch_hook(), g.latches())) if hooks else part


def cover():
    body = prism(g.cover_outline(), -g.Z_COVER_BOT, -g.Z_COVER_TOP)
    z0, z1 = -g.Z_COVER_BOT - 1, -g.Z_COVER_TOP + 1
    cuts = [box(r, z0, z1) for r in g.cover_notches()]
    for slot, end in g.beam_cuts():
        cuts += [stadium((slot[0] + slot[2]) / 2, (slot[1] + slot[3]) / 2,
                         slot[3] - slot[1], slot[2] - slot[0], z0, z1), box(end, z0, z1)]
    post_h = g.PARTS_CLEAR + g.POST_PRELOAD
    posts = union(m3d.Manifold.cylinder(post_h, g.POST_D / 2, circular_segments=SEGMENTS)
                  .translate([x, y, -g.Z_COVER_TOP]) for x, y in g.posts())
    return (body - union(cuts)) + posts


def plate_test():
    return (prism(g.case_outer(), -g.Z_PLATE, 0)
            - union(box(r, -g.Z_PLATE - 1, 1) for r in g.switch_cutouts()))


def pcb_dummy():
    """The PCB's outline and thickness with its switch-peg holes.  Plastic is far
    bendier than FR4, so it tests fit and post pressure, not stiffness."""
    holes = union(m3d.Manifold.cylinder(g.PCB_T + 2, DUMMY_PEG_HOLE / 2, circular_segments=SEGMENTS)
                  .translate([x, y, -g.Z_PCB_BOT - 1]) for x, y in g.peg_holes())
    return prism(g.pcb_outline(), -g.Z_PCB_BOT, -g.Z_PCB_TOP) - holes


# -------------------------------------------------------------- fit check ---
def fit_check(tray_, cover_, pcb):
    """Prove the fits on the solids themselves.  Returns a summary line."""
    px, py = g.PERIOD
    bare = tray(hooks=False)
    tiny = 1e-3                 # mm^3; anything more is two parts in one place

    def clash(a, b, dx=0.0, dy=0.0, dz=0.0):
        """Volume a shares with b moved by (dx, dy, dz)."""
        return (a ^ b.translate([dx, dy, dz])).volume()

    # In its own tray: the cover and the PCB fit, and the lip and the bumps
    # are in the cover's way out.
    assert clash(cover_, tray_) < tiny, "cover does not fit its tray"
    assert clash(pcb, tray_) < tiny, "PCB does not fit its tray"
    assert clash(cover_, tray_, dz=0.5) > 1.0, "nothing holds the cover in"

    # Tiles on the grid: no part of one is inside another.
    for i, j in [(i, j) for i in (-1, 0, 1) for j in (-1, 0, 1) if (i, j) != (0, 0)]:
        for part, name in ((tray_, "tray"), (cover_, "cover"), (pcb, "PCB")):
            assert clash(part, tray_, i * px, j * py) < tiny, \
                "%s runs into the tray of tile %+d, %+d" % (name, i, j)
    air = min(tray_.min_gap(tray_.translate([dx, dy, 0]), 1.0)
              for dx, dy in ((px, 0), (0, py), (px, py)))
    assert air > 0.1, "neighbouring trays only %.3f mm apart" % air

    for name, ux, uy in (("E", 1, 0), ("N", 0, 1)):
        dx, dy = ux * px, uy * py
        # The way down is clear: but for the hooks, a tile coming down onto
        # its neighbour's rails touches nothing, from 15 mm up to seated.
        for k in range(1, 31):
            assert clash(bare, bare, dx, dy, k * 0.5) < tiny, \
                "%s neighbour fouls %.1f mm above its seat" % (name, k * 0.5)
        # Seated, it is held every way: the hook against lifting, the rails'
        # heads against sinking, the dovetails against pulling away or sliding.
        assert clash(tray_, tray_, dx, dy, 1.0) > 0.5, "%s latch does not hold" % name
        assert clash(bare, bare, dx, dy, -0.5) > 0.05, "%s rails do not carry" % name
        assert clash(bare, bare, dx + 0.5 * ux, dy + 0.5 * uy) > 0.5, "%s dovetails do not hold" % name
        assert clash(bare, bare, dx + 0.5 * uy, dy + 0.5 * ux) > 0.5, "%s rails do not locate" % name
    return ("fit           OK (solids: neighbours %.2f mm apart, a clear way down onto the "
            "rails, held once seated; cover and PCB fit)" % air)


# ----------------------------------------------------------------- export ---
def for_print(part, flip, xy_scale):
    """Optionally turn 180 degrees about x (plate face down), scale x/y, sit on z = 0."""
    if flip:
        part = part.rotate([180, 0, 0])
    part = part.scale([xy_scale, xy_scale, 1.0])
    lo = part.bounding_box()[:3]
    return part.translate([-lo[0], -lo[1], -lo[2]])


def write_stl(path, part, name):
    mesh = part.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    t = np.asarray(mesh.tri_verts)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = n, np.stack([a, b, c], axis=1)
    with open(path, "wb") as fh:
        fh.write(name.encode()[:80].ljust(80, b" "))
        fh.write(np.uint32(len(t)).tobytes())
        fh.write(rec.tobytes())
    return len(t)


def emit(out, name, part, flip, xy_scale):
    assert part.status() == m3d.Error.NoError, "%s: %s" % (name, part.status())
    assert len(part.decompose()) == 1, "%s is in %d pieces" % (name, len(part.decompose()))
    part = for_print(part, flip, xy_scale)
    path = os.path.join(out, name + ".stl")
    ntri = write_stl(path, part, "hexkb %s %dx%d" % (name, g.COLS, g.ROWS))
    x0, y0, z0, x1, y1, z1 = part.bounding_box()
    print("%-16s %6d triangles, %5.1f x %5.1f x %4.1f mm, ~%3.0f g PLA  -> %s"
          % (name, ntri, x1 - x0, y1 - y0, z1 - z0, part.volume() / 1000 * DENSITY,
             os.path.relpath(path, HERE)))


# ---------------------------------------------------------------- preview ---
def preview_svg(path, tiles):
    """Tiles, keycaps labelled with lattice (a, b), ports, cover posts, rails
    (blue) and latches (green), 1:1 mm."""
    outer, pcb = g.case_outer(), g.pcb_outline()
    pad = 10.0
    allpts = [(ox + x, oy + y) for ox, oy in (g.tile_origin(u, v) for u, v in tiles)
              for x, y in g.outline()]
    x0, y0, x1, y1 = g.bbox(allpts)
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w, h = x1 - x0, y1 - y0

    def pts(poly, ox=0.0, oy=0.0):
        return " ".join("%.3f,%.3f" % (ox + x, -(oy + y)) for x, y in poly)

    r_cap = g.CAP_AF / math.sqrt(3)
    hexagon = [(r_cap * math.cos(math.radians(60 * k)), r_cap * math.sin(math.radians(60 * k)))
               for k in range(6)]
    fills = ("#e6eef8", "#f7ede1")
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" height="%.1fmm" '
           'viewBox="%.3f %.3f %.3f %.3f" font-family="sans-serif">' % (w, h, x0, -y1, w, h),
           '<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="#fff"/>' % (x0, -y1, w, h)]
    for u, v in tiles:
        ox, oy = g.tile_origin(u, v)
        out.append('<polygon points="%s" fill="%s" stroke="#445" stroke-width="0.4"/>'
                   % (pts(outer, ox, oy), fills[(u + v) % 2]))
        out.append('<polygon points="%s" fill="none" stroke="#99a" stroke-width="0.25" '
                   'stroke-dasharray="1.5 1"/>' % pts(pcb, ox, oy))
        for x, y in g.posts():
            out.append('<circle cx="%.3f" cy="%.3f" r="%.2f" fill="#556"/>'
                       % (ox + x, -(oy + y), g.POST_D / 2))
        for edge, (x, y) in g.ports().items():
            rw, rh = (g.PORT_SLOT_W, 2.4) if edge in "NS" else (2.4, g.PORT_SLOT_W)
            out.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="#d0563b"/>'
                       % (ox + x - rw / 2, -(oy + y) - rh / 2, rw, rh))
        joints = [(g.seam_rect(e, t, g.rail_half(g.RAIL_REACH), 0, g.RAIL_REACH), "#2d5fa8")
                  for e, ts in g.rails().items() for t in ts]
        joints += [(g.seam_rect(e, t, g.LATCH_W / 2, -g.WALL_T, g.SEAM_GAP + g.LATCH_BITE), "#2f8f5b")
                   for e, ts in g.latches().items() for t in ts]
        for (rx0, ry0, rx1, ry1), colour in joints:
            out.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="%s"/>'
                       % (ox + rx0, -(oy + ry1), rx1 - rx0, ry1 - ry0, colour))
    for _, _, x, y, a, b in g.arrangement_keys(tiles):
        out.append('<polygon points="%s" fill="#fff" fill-opacity="0.85" stroke="#778" '
                   'stroke-width="0.3"/>' % pts(hexagon, x, y))
        out.append('<text x="%.3f" y="%.3f" font-size="3.2" text-anchor="middle" '
                   'fill="#334">%d,%d</text>' % (x, -y + 1.1, a, b))
    for u, v in tiles:
        ox, oy = g.tile_origin(u, v)
        cx, cy = ox + g.PERIOD[0] / 2 - g.COL_PITCH / 2, oy + g.PERIOD[1] / 2
        out.append('<text x="%.3f" y="%.3f" font-size="14" font-weight="bold" '
                   'text-anchor="middle" fill="#334" fill-opacity="0.18">tile %d,%d</text>'
                   % (cx, -cy + 5, u, v))
    out.append("</svg>")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


def joints_svg(path, parts):
    """Two tiles joined E to W, cut three ways through the solids `parts`
    (tray, cover, PCB) and drawn 8:1 on a 1 mm grid."""
    scale, half, pad, head = 8.0, 7.0, 2.0, 2.2
    x_mid = g.seam_face("E") + g.SEAM_GAP / 2
    y_latch, y_rail = g.latches()["E"][0], g.rails()["E"][0]
    z_plan = -(g.Z_PCB_BOT + g.Z_COVER_TOP) / 2
    tints = (("#9db7dd", "#5f7fb0", "#86b386"), ("#ecc3a6", "#c0825c", "#86b386"))
    near = box((x_mid - half - 1, -1e3, x_mid + half + 1, 1e3), -1e3, 1e3)
    solids = [(part.translate([dx, 0, 0]) ^ near, tint)
              for dx, row in zip((0.0, g.PERIOD[0]), tints) for part, tint in zip(parts, row)]

    def across(y):          # the cut at this y, as (x, z)
        return lambda part: [[(x, -v) for x, v in poly]
                             for poly in part.rotate([90, 0, 0]).slice(y).to_polygons()]

    def plan(part):         # the cut at z_plan, as (x, y)
        return [[(x, y) for x, y in poly] for poly in part.slice(z_plan).to_polygons()]

    views = (("across a latch: the hook in the window, over the neighbour's rim",
              across(y_latch), -g.Z_FLOOR - 1.0, 1.0),
             ("across a rail: its head under the closed end of the slot",
              across(y_rail), -g.Z_FLOOR - 1.0, 1.0),
             ("a rail from above: the dovetail in its slot, cut %.0f mm down" % -z_plan,
              plan, y_rail - half, y_rail + half))
    w = len(views) * (2 * half + pad) + pad
    h = max(v1 - v0 for _, _, v0, v1 in views) + head + 2 * pad
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" height="%.1fmm" '
           'viewBox="0 0 %.3f %.3f" font-family="sans-serif">' % (w * scale, h * scale, w, h),
           '<rect width="%.3f" height="%.3f" fill="#fff"/>' % (w, h)]
    for k, (title, cut, v0, v1) in enumerate(views):
        ox, oy = pad + k * (2 * half + pad), pad + head
        # u, v on the page: u = ox + x - (x_mid - half), v = oy + v1 - value
        out.append('<clipPath id="view%d"><rect x="%.3f" y="%.3f" width="%.3f" height="%.3f"/>'
                   '</clipPath>' % (k, ox, oy, 2 * half, v1 - v0))
        out.append('<g clip-path="url(#view%d)">' % k)
        out.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="#fafafa"/>'
                   % (ox, oy, 2 * half, v1 - v0))
        for part, tint in solids:
            d = " ".join("M" + " L".join("%.3f %.3f" % (ox + x - x_mid + half, oy + v1 - v)
                                         for x, v in poly) + " Z" for poly in cut(part))
            if d:
                out.append('<path d="%s" fill="%s" fill-rule="evenodd"/>' % (d, tint))
        grid = ["M%.3f %.3f v%.3f" % (ox + half + i, oy, v1 - v0) for i in range(-int(half), int(half) + 1)]
        grid += ["M%.3f %.3f h%.3f" % (ox, oy + v1 - j, 2 * half)
                 for j in range(math.ceil(v0), math.floor(v1) + 1)]
        out.append('<path d="%s" stroke="#000" stroke-opacity="0.12" stroke-width="0.03" fill="none"/>'
                   % " ".join(grid))
        out.append("</g>")
        out.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="none" stroke="#445" '
                   'stroke-width="0.05"/>' % (ox, oy, 2 * half, v1 - v0))
        words = title.split(": ")
        for i, line in enumerate(words):
            out.append('<text x="%.3f" y="%.3f" font-size="0.62"%s fill="#334">%s</text>'
                       % (ox, pad + 0.6 + 0.85 * i, ' font-weight="bold"' if i == 0 else "", line))
        for dx, anchor, label in ((0.25, "start", "tile"), (2 * half - 0.25, "end", "its E neighbour")):
            out.append('<text x="%.3f" y="%.3f" font-size="0.55" text-anchor="%s" fill="#334">%s</text>'
                       % (ox + dx, oy + v1 - v0 + 0.8, anchor, label))
    out.append('<text x="%.3f" y="%.3f" font-size="0.55" text-anchor="end" fill="#667">grid 1 mm; '
               'tray, cover (darker) and PCB (green) of each tile</text>' % (w - pad, h - 0.35))
    out.append("</svg>")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(HERE, "cad"))
    ap.add_argument("--xy-scale", type=float, default=1.0,
                    help="shrink compensation from the test print, e.g. 1.003")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print("\n".join(g.check()))
    parts = tray(), cover(), pcb_dummy()
    print(fit_check(*parts) + "\n")
    emit(args.out, "tray", parts[0], True, args.xy_scale)
    emit(args.out, "cover", parts[1], False, args.xy_scale)
    emit(args.out, "plate_test", plate_test(), True, args.xy_scale)
    emit(args.out, "pcb_dummy", parts[2], False, args.xy_scale)
    svg = os.path.join(args.out, "joints.svg")
    joints_svg(svg, parts)
    print("joints        a latch and a rail in section -> %s" % os.path.relpath(svg, HERE))
    with g.size(2, 4):
        g.check()
        parts = tray(), cover(), pcb_dummy()
        fit_check(*parts)
        emit(args.out, "coupon_tray", parts[0], True, args.xy_scale)
        emit(args.out, "coupon_cover", parts[1], False, args.xy_scale)
        emit(args.out, "coupon_pcb_dummy", parts[2], False, args.xy_scale)

    tiles = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1)]
    svg = os.path.join(args.out, "preview.svg")
    preview_svg(svg, tiles)
    print("preview       %d tiles, %d keys -> %s"
          % (len(tiles), len(tiles) * g.KEYS_PER_TILE, os.path.relpath(svg, HERE)))

    # Columns 0 and COLS-2 are both unshifted, so their holes line up across --
    # and both spans stay under 150 mm, the common caliper size.
    span_x = (g.COLS - 2) * g.COL_PITCH + g.SWITCH_CUTOUT
    span_y = (g.ROWS - 1) * g.ROW_PITCH + g.SWITCH_CUTOUT
    print("\nmeasure on plate_test (expected at --xy-scale 1):")
    print("  across: far edges of the bottom holes in columns 1 and %d   %.2f mm"
          % (g.COLS - 1, span_x))
    print("  along:  far edges of the end holes in column 1             %.2f mm" % span_y)
    print("  --xy-scale = expected / measured")


if __name__ == "__main__":
    main()
