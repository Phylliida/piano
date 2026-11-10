#!/usr/bin/env python3
"""Build the printed case for one tile from tilegeom.py.

Writes, into cad/:
  tray.stl          plate, ribs, walls, W lip, E catch bumps, cable slots
  cover.stl         bottom cover: catch beams and the posts that clamp the PCB
  plate_test.stl    the 1.5 mm top plate alone: a short print to check switch
                    fit and measure shrinkage
  pcb_dummy.stl     a printed stand-in for the PCB: its outline, thickness and
                    switch-peg holes, for testing the case before boards exist
  coupon_tray.stl   the same tray, cover and dummy PCB for a 2 x 2-key tile --
  coupon_cover.stl  every feature, a fraction of the print.  Tune the fits on
  coupon_pcb_dummy.stl  these.
  preview.svg       five tiles in an L: keycaps with their lattice coordinates,
                    ports, cover posts.  1:1 scale in mm.

Print orientations are baked in.  The tray prints upside down, plate face on
the bed: the tile turned 180 degrees about x -- a turn, not a mirror, so the
stepped edges and the lip/catch sides come out the right way round.  The
cover prints as used, posts up.

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
    return m3d.Manifold.batch_boolean(list(parts), m3d.OpType.Add)


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


# ------------------------------------------------------------------ parts ---
def tray():
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
    bumps = union(profile_along_y(profile, y0, y1) for y0, y1 in g.catch_bumps())

    slots = union(box(r, -g.Z_COVER_TOP, -g.Z_PCB_BOT) for r in g.port_slots())
    x_out = g.side(outer, "E")[0]
    pry = union(box((x_e - 0.5, y0, x_out + 0.5, y1), -g.Z_FLOOR - 1, -g.Z_COVER_BOT)
                for y0, y1 in g.pry_notches())
    return union([walls, plate, ribs, lip, bumps]) - slots - pry


def cover():
    body = prism(g.cover_outline(), -g.Z_COVER_BOT, -g.Z_COVER_TOP)
    slots = union(stadium(x, y, L, w, -g.Z_COVER_BOT - 1, -g.Z_COVER_TOP + 1)
                  for x, y, L, w in g.beam_slots())
    post_h = g.PARTS_CLEAR + g.POST_PRELOAD
    posts = union(m3d.Manifold.cylinder(post_h, g.POST_D / 2, circular_segments=SEGMENTS)
                  .translate([x, y, -g.Z_COVER_TOP]) for x, y in g.posts())
    return (body - slots) + posts


def plate_test():
    return (prism(g.case_outer(), -g.Z_PLATE, 0)
            - union(box(r, -g.Z_PLATE - 1, 1) for r in g.switch_cutouts()))


def pcb_dummy():
    """The PCB's outline and thickness with its switch-peg holes.  Plastic is far
    bendier than FR4, so it tests fit and post pressure, not stiffness."""
    holes = union(m3d.Manifold.cylinder(g.PCB_T + 2, DUMMY_PEG_HOLE / 2, circular_segments=SEGMENTS)
                  .translate([x, y, -g.Z_PCB_BOT - 1]) for x, y in g.peg_holes())
    return prism(g.pcb_outline(), -g.Z_PCB_BOT, -g.Z_PCB_TOP) - holes


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
    part = for_print(part, flip, xy_scale)
    path = os.path.join(out, name + ".stl")
    ntri = write_stl(path, part, "hexkb %s %dx%d" % (name, g.COLS, g.ROWS))
    x0, y0, z0, x1, y1, z1 = part.bounding_box()
    print("%-16s %6d triangles, %5.1f x %5.1f x %4.1f mm, ~%3.0f g PLA  -> %s"
          % (name, ntri, x1 - x0, y1 - y0, z1 - z0, part.volume() / 1000 * DENSITY,
             os.path.relpath(path, HERE)))


# ---------------------------------------------------------------- preview ---
def preview_svg(path, tiles):
    """Tiles, keycaps labelled with lattice (a, b), ports and cover posts, 1:1 mm."""
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


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(HERE, "cad"))
    ap.add_argument("--xy-scale", type=float, default=1.0,
                    help="shrink compensation from the test print, e.g. 1.003")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print("\n".join(g.check()) + "\n")
    emit(args.out, "tray", tray(), True, args.xy_scale)
    emit(args.out, "cover", cover(), False, args.xy_scale)
    emit(args.out, "plate_test", plate_test(), True, args.xy_scale)
    emit(args.out, "pcb_dummy", pcb_dummy(), False, args.xy_scale)
    with g.size(2, 2):
        g.check()
        emit(args.out, "coupon_tray", tray(), True, args.xy_scale)
        emit(args.out, "coupon_cover", cover(), False, args.xy_scale)
        emit(args.out, "coupon_pcb_dummy", pcb_dummy(), False, args.xy_scale)

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
