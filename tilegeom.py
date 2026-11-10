#!/usr/bin/env python3
"""Shared geometry for one tile of the modular hex keyboard.

The single source for every dimension that the PCB and the printed case have
to agree on.  gen_case.py builds the case from it; gen_tile.py should place its
footprints from it instead of keeping its own constants -- two copies of one
fact is how the board and its netlist drifted apart before.

Units are mm.  y points UP and the origin is the centre of key (col 0, row 0).
KiCad's y points down: convert with to_kicad() when emitting a board.

The lattice
-----------
A true hex lattice: all six neighbours of every key are exactly ROW_PITCH away.

    x = col * COL_PITCH                      COL_PITCH = ROW_PITCH * sqrt(3) / 2
    y = (row + (col % 2) / 2) * ROW_PITCH    odd columns sit half a key higher

Keycaps are FLAT-TOP hexagons -- a flat faces every neighbour -- so a 21 mm cap
only needs ROW_PITCH > 21.  The old 25 x 25 grid came from turning the cap 30
degrees, which points its corners at the keys above and below.

The tile
--------
COLS columns of ROWS keys.  COLS is even, so the tile repeats under a plain
shift in x (an odd count puts two unshifted columns side by side at every
seam); equal columns make it repeat in y.  Odd columns stick half a key out of
the top edge and even columns out of the bottom, so those edges step and the
next tile's steps slot in.  Tiles sit on a grid with period PERIOD, in any
shape, holes allowed.  check() proves all of this numerically.

The case -- no tools, no screws
-------------------------------
A printed tray (plate, ribs, walls) and a printed bottom cover.  The PCB drops
in from below; the switch pegs through its holes locate it.  The cover's W edge
tucks over a lip on the W wall, then its E edge swings up until two flexing
catch beams click over bumps on the E wall.  Posts on the cover press the PCB
up against the ribs.  To open: pry the cover's E edge down at a notch.
"""

import contextlib
import math

# ------------------------------------------------------------------ lattice ---
ROW_PITCH = 24.15                           # key spacing, all six directions [Lumatone]
COL_PITCH = ROW_PITCH * math.sqrt(3) / 2    # 20.914 -- derived, never set it
CAP_AF = 21.0                               # keycap across flats [Lumatone]
PRINT_BED = 180.0                           # Bambu A1 Mini: one tile's case is one print


def _set_size(cols, rows):
    global COLS, ROWS, KEYS_PER_TILE, PERIOD
    assert cols % 2 == 0, "COLS must be even or the stagger breaks at every seam"
    COLS, ROWS = cols, rows
    KEYS_PER_TILE = COLS * ROWS
    PERIOD = (COLS * COL_PITCH, ROWS * ROW_PITCH)   # tile-to-tile shift in x and y


_set_size(8, 5)                             # 40 keys = 5 x 8:1 muxes


@contextlib.contextmanager
def size(cols, rows):
    """Temporarily re-size the tile -- e.g. a 2 x 2 test coupon that carries
    every case feature at a fraction of the print time."""
    saved = COLS, ROWS
    _set_size(cols, rows)
    try:
        yield
    finally:
        _set_size(*saved)


# --------------------------------------------------------------- case stack ---
# Depths below the plate's top face.  That face is also the print bed: the tray
# is printed upside down.
PLATE_T = 1.5           # MX switches clip into a 1.5 mm plate
PLATE_TO_PCB = 3.5      # plate underside to PCB top; HE60 uses 3.5 mm standoffs
PCB_T = 1.6
PARTS_CLEAR = 5.0       # PCB back to cover: room for the tallest back-side part,
                        # the cable connectors -- confirm once a part is chosen
COVER_T = 1.6           # bottom cover plate
LIP_T = 2.0             # lip and catch bumps sit under the cover, so the cover
                        # is recessed this far inside the walls
Z_PLATE = PLATE_T
Z_PCB_TOP = Z_PLATE + PLATE_TO_PCB
Z_PCB_BOT = Z_PCB_TOP + PCB_T
Z_COVER_TOP = Z_PCB_BOT + PARTS_CLEAR
Z_COVER_BOT = Z_COVER_TOP + COVER_T
Z_FLOOR = Z_COVER_BOT + LIP_T               # bottom of the walls

SWITCH_CUTOUT = 14.0    # MX plate cutout, square
PEG_DX = 5.08           # switch locating pegs at (+/-PEG_DX, 0) from the key centre
PEG_HOLE = 1.75         # their NPTH holes in the PCB [HE60 MX-1U footprint]

# Insets from the tile's ideal outline.  Key positions never move; only the
# physical parts shrink inside the outline.
SEAM_GAP = 0.3          # total gap between neighbouring tiles' walls
WALL_T = 1.2            # three 0.4 mm perimeters
PCB_CLEAR = 0.3         # PCB edge to the wall, or to the lip / bumps on W and E

RIB_W = 2.0             # plate-to-PCB stiffening ribs
SWITCH_CLEAR = 0.5      # rib to a switch's lower housing (= the cutout)

# Bottom cover.
COVER_CLEAR = 0.2       # cover edge to the wall's inner face
LIP_W = 1.5             # W lip: how far it reaches under the cover
CATCH_D = 0.9           # E bumps reach under the cover by CATCH_D - COVER_CLEAR;
                        # their retaining face is 45 degrees so a pry releases them
CATCH_L = 12.0          # length of each bump along the edge
BEAM_W = 2.0            # catch beam: the strip of cover that flexes past a bump
BEAM_SLOT = 1.2         # slot that frees the beam from the rest of the cover
BEAM_L = 40.0           # free length of each beam (shortened to fit small tiles)
PRY_W = 8.0             # notch in the E wall's foot, to get a fingernail under the cover
POST_D = 4.0            # posts that press the PCB against the ribs
POST_PRELOAD = 0.2      # posts this much too tall, so the cover keeps pressure on
POST_KEEPOUT = 4.0      # radius kept bare on the PCB's back around each post

# Edge ports: a JST GH 6-pin side-entry header on the PCB's back at each edge,
# its cable leaving through a slot in the wall between the PCB and the cover.
PORT_SLOT_W = 14.0      # GH 6-pin header is 12.05 mm wide over its courtyard
PORT_DEPTH = 6.5        # header courtyard, back of the pins to the opening

# Parts under every key, on the PCB's back (tile coordinates, from the key
# centre).  Every key is identical: the switch's LED window is LED_DY south of
# the stem, so each keycap is lit from the same side.
#
# The sensor's two capacitors sit BESIDE it, between the sensor and the switch
# pegs (2.2 mm of otherwise dead space), each on the side of the pin it serves.
# Above the sensor they cost 2 mm of the gap between rows -- enough that the
# 13.5 mm MCU no longer fit anywhere.
LED_DY = 5.08
SENSOR_CAP_DX = 2.7     # from the key centre; courtyard spans 1.75 .. 3.66
SENSOR_CAP_DY = 0.6     # nudged toward the sensor's pins 1 and 2


# --------------------------------------------------------------------- keys ---
def key_xy(col, row):
    """Centre of key (col, row) in tile coordinates."""
    return col * COL_PITCH, (row + (col % 2) / 2) * ROW_PITCH


def keys():
    """[(index, col, row, x, y)] for one tile, column by column."""
    return [(c * ROWS + r, c, r) + key_xy(c, r)
            for c in range(COLS) for r in range(ROWS)]


def tile_origin(u, v):
    """Where tile (u, v) of an arrangement puts its key (0, 0)."""
    return u * PERIOD[0], v * PERIOD[1]


def axial(gcol, grow):
    """Lattice coordinates (a, b) of the key in global column gcol, row grow.

    position = a * (COL_PITCH, ROW_PITCH / 2) + b * (0, ROW_PITCH), so an
    isomorphic layout over ANY arrangement is just  note = a * da + b * db.
    """
    return gcol, grow - gcol // 2


def arrangement_keys(tiles):
    """Every key of tiles placed at grid cells `tiles`:
    [((u, v), index, x, y, a, b)], (a, b) from axial()."""
    out = []
    for u, v in tiles:
        ox, oy = tile_origin(u, v)
        for i, c, r, x, y in keys():
            out.append(((u, v), i, ox + x, oy + y) + axial(u * COLS + c, v * ROWS + r))
    return out


# ------------------------------------------------------------------ outline ---
def _col_span(col):
    """(bottom, top) of column col's strip of key cells."""
    lift = (col % 2) / 2 * ROW_PITCH
    return lift - ROW_PITCH / 2, lift + (ROWS - 0.5) * ROW_PITCH


def outline(inset=0.0):
    """The tile's stepped outline, counter-clockwise, moved inward by `inset`.

    Every corner is a right angle and no two edges in a row are collinear:
    each column contributes one flat on the bottom and one on the top, and the
    alternating column heights put a riser between every pair.
    """
    pts = []
    for c in range(COLS):                       # bottom edge, left to right
        bot = _col_span(c)[0]
        pts += [((c - 0.5) * COL_PITCH, bot), ((c + 0.5) * COL_PITCH, bot)]
    for c in reversed(range(COLS)):             # top edge, right to left
        top = _col_span(c)[1]
        pts += [((c + 0.5) * COL_PITCH, top), ((c - 0.5) * COL_PITCH, top)]
    return offset_rectilinear(pts, inset) if inset else pts


def offset_rectilinear(poly, d):
    """Move every edge of a counter-clockwise, all-right-angle polygon inward by d.

    With perpendicular edges the two moved edges meet at  v + d * (n1 + n2),
    n1 and n2 the inward normals either side of v, for convex and reflex
    corners alike.  A flat between two risers shrinks by up to 2d.
    """
    n = len(poly)
    shortest = min(math.dist(poly[k - 1], poly[k]) for k in range(n))
    assert 2 * d < shortest, "inset %.2f would fold the outline" % d
    out = []
    for k in range(n):
        (x0, y0), (x1, y1), (x2, y2) = poly[k - 1], poly[k], poly[(k + 1) % n]
        n1, n2 = _inward(x1 - x0, y1 - y0), _inward(x2 - x1, y2 - y1)
        out.append((x1 + d * (n1[0] + n2[0]), y1 + d * (n1[1] + n2[1])))
    return out


def _inward(dx, dy):
    """Unit normal on the left of (dx, dy): inward for a counter-clockwise polygon."""
    L = math.hypot(dx, dy)
    return -dy / L, dx / L


def side(poly, which):
    """(x, y_lo, y_hi) of the straight W or E edge of a tile-shaped outline."""
    x = (min if which == "W" else max)(p[0] for p in poly)
    ys = [p[1] for p in poly if abs(p[0] - x) < 1e-9]
    return x, min(ys), max(ys)


def case_outer():
    return outline(SEAM_GAP / 2)


def case_inner():
    return outline(SEAM_GAP / 2 + WALL_T)


def cover_outline():
    return outline(SEAM_GAP / 2 + WALL_T + COVER_CLEAR)


def pcb_outline():
    """PCB edge: PCB_CLEAR inside the walls, and on W and E also clear of the
    lip and bumps, so the board drops straight in past them."""
    pts = outline(SEAM_GAP / 2 + WALL_T + PCB_CLEAR)
    x_w, x_e = min(p[0] for p in pts), max(p[0] for p in pts)
    out = []
    for x, y in pts:
        if abs(x - x_w) < 1e-9:
            x += LIP_W
        elif abs(x - x_e) < 1e-9:
            x -= CATCH_D
        out.append((x, y))
    return out


# ----------------------------------------------------------------- features ---
# Rectangles are (x0, y0, x1, y1).
def _rect(cx, cy, w, h):
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def switch_cutouts():
    return [_rect(x, y, SWITCH_CUTOUT, SWITCH_CUTOUT) for _, _, _, x, y in keys()]


def peg_holes():
    """Centres of the switch pegs' holes in the PCB; they locate the board."""
    return [(x + s * PEG_DX, y) for _, _, _, x, y in keys() for s in (-1, 1)]


def ribs():
    """Plate-to-PCB ribs: one along every inner column boundary, one across each
    column between neighbouring keys.  All of them run in the channels between
    switches, so they bear on bare PCB (the top side carries only switches)."""
    out = []
    for c in range(COLS - 1):
        lo = max(_col_span(c)[0], _col_span(c + 1)[0])
        hi = min(_col_span(c)[1], _col_span(c + 1)[1])
        out.append(_rect((c + 0.5) * COL_PITCH, (lo + hi) / 2, RIB_W, hi - lo))
    for c in range(COLS):
        for r in range(ROWS - 1):
            out.append(_rect(c * COL_PITCH, key_xy(c, r)[1] + ROW_PITCH / 2, COL_PITCH, RIB_W))
    return out


def posts():
    """Cover posts, each straight under a rib crossing a column between two keys,
    so the PCB is squeezed between rib and post with nothing to bend it.  The
    PCB layout must keep POST_KEEPOUT bare around each one.  Staggered: even
    columns take gaps 0, 2, ..., odd columns 1, 3, ...  The N/S port column of
    a hand-placed layout (PORT_SPOTS) takes the gaps its headers leave free."""
    spot = PORT_SPOTS.get((COLS, ROWS))
    out = []
    for c in range(COLS):
        gaps = list(range(c % 2, ROWS - 1, 2)) or [0]
        if spot and c == spot["ns_col"]:
            gaps = spot["ns_post_gaps"]
        out += [(c * COL_PITCH, key_xy(c, r)[1] + ROW_PITCH / 2) for r in gaps]
    return out


def catch_beams():
    """[(y_centre, free_length)] of the flexing beams along the cover's E edge:
    two on a long edge, one on a short one."""
    _, lo, hi = side(cover_outline(), "E")
    span = hi - lo
    if span >= 2 * BEAM_L + 20:
        return [(lo + span / 4, BEAM_L), (lo + 3 * span / 4, BEAM_L)]
    return [((lo + hi) / 2, min(BEAM_L, span - 6 - BEAM_SLOT))]


def beam_slots():
    """Stadium slots, as (x_centre, y_centre, length incl. round ends, width)."""
    x_e = side(cover_outline(), "E")[0]
    return [(x_e - BEAM_W - BEAM_SLOT / 2, y, L + BEAM_SLOT, BEAM_SLOT)
            for y, L in catch_beams()]


def catch_bumps():
    """(y0, y1) of each bump on the E wall, at the middle of its beam."""
    return [(y - CATCH_L / 2, y + CATCH_L / 2) for y, _ in catch_beams()]


def pry_notches():
    """(y0, y1) of each pry notch in the E wall's foot, beside its bump."""
    return [(y1 + 1.0, y1 + 1.0 + PRY_W) for _, y1 in catch_bumps()]


def _header_gaps(col):
    """Gap centres in column col with room for a port header: no cover post.
    Falls back to any gap on tiles too small to have a choice."""
    x = col * COL_PITCH
    gaps = [key_xy(col, r)[1] + ROW_PITCH / 2 for r in range(ROWS - 1)]
    taken = [y for px, y in posts() if abs(px - x) < 1e-6]
    ok = [y for r, y in enumerate(gaps)
          if all(abs(y - t) > 1e-6 for t in taken)]
    return ok or gaps


# A header in a gap sits below the gap's centre: the free band runs from the
# lower key's sensor up to the upper key's LED, which reaches further.
PORT_GAP_DY = -2.3
PLUG_PROTRUSION = 3.1   # a mated GHR plug sticks out this far past the header's
                        # face (JST eGH, mated drawing: 7.15 mm back to back,
                        # header body 4.05 mm), and its wire leaves straight back
HEADER_FACE_INSET = 4.0  # header face to the PCB edge, when it faces that edge
HEADER_FACE = 2.45      # footprint origin to the header's opening (KiCad's
                        # JST_GH_SM06B-GHS-TB footprint: body front at y = 2.45)
CABLE_GAP = 50.0        # partner headers' openings this far apart across a seam,
                        # so a stock 50 mm GH cable runs straight between them

# Hand-placed headers, per tile size: header origins in tile coordinates.  For
# 8 x 5 these came from a search over the routed board (2026-10-20): E/W at one
# height and N/S on one column, each plug and its straight cable clear of
# switch pegs, posts and parts, ending at a flat stretch of wall.  The N/S
# column's posts move to the gaps the headers leave free.  Sizes not listed
# here (the test coupon) fall back to the gap search in port_layout().
PORT_SPOTS = {
    (8, 5): {"ew_y": 77.33, "w_x": 25.94, "ns_col": 4, "s_y": 18.58, "ns_post_gaps": (1, 2)},
}


def port_layout():
    """{edge: {"slot": (x, y), "header": (x, y), "facing": (dx, dy)}}.

    Tile sizes in PORT_SPOTS use their hand-placed headers, with partners
    CABLE_GAP apart; the rest use the gap search below.

    The JST GH cable lies in the 5 mm under the PCB and crosses the seam
    through one slot in each tile's wall, so facing slots must line up: E and W
    slots share a height, N and S slots share a column.  Each header sits in
    the nearest gap between keys with room for it and its plug, opening toward
    its slot; the cable bends from there to the slot, over low parts.

    Neighbouring tiles' edge columns alternate in height, so wherever the E
    header sits in a gap, the W slot of the next tile meets a key: the cable
    passes under that key's switch peg (pegs reach ~1.4 mm below the board).
    """
    spot = PORT_SPOTS.get((COLS, ROWS))
    if spot:
        x_ns = spot["ns_col"] * COL_PITCH
        lo, hi = _col_span(spot["ns_col"])
        x_w, y = spot["w_x"], spot["ew_y"]
        x_e = x_w - 2 * HEADER_FACE + PERIOD[0] - CABLE_GAP
        y_s = spot["s_y"]
        y_n = y_s - 2 * HEADER_FACE + PERIOD[1] - CABLE_GAP
        return {
            "E": {"slot": ((COLS - 0.5) * COL_PITCH, y), "header": (x_e, y), "facing": (1.0, 0.0)},
            "W": {"slot": (-0.5 * COL_PITCH, y), "header": (x_w, y), "facing": (-1.0, 0.0)},
            "N": {"slot": (x_ns, hi), "header": (x_ns, y_n), "facing": (0.0, 1.0)},
            "S": {"slot": (x_ns, lo), "header": (x_ns, y_s), "facing": (0.0, -1.0)},
        }
    inset = SEAM_GAP / 2 + WALL_T + PCB_CLEAR
    half = PORT_DEPTH / 2
    # E: the header looks straight at its slot from the last column's best gap.
    mid = (ROWS - 0.5) * ROW_PITCH / 2
    y_ew = min(_header_gaps(COLS - 1), key=lambda y: abs(y - mid)) + PORT_GAP_DY
    x_e = (COLS - 0.5) * COL_PITCH
    e_head = (x_e - inset - CATCH_D - HEADER_FACE_INSET - half, y_ew)
    # W: the first column has a key at that height, so its header sits in the
    # nearest gap and looks along the edge toward the slot.
    x_w = -0.5 * COL_PITCH
    y_wg = min(_header_gaps(0), key=lambda y: abs(y - y_ew)) + PORT_GAP_DY
    w_face = (0.0, math.copysign(1.0, y_ew - y_wg))
    w_head = (x_w + inset + LIP_W + 6.5, y_wg)
    # N and S: one middle even column; each header looks along the column to
    # its edge from that column's highest / lowest roomy gap.
    c = COLS // 2 - (COLS // 2) % 2
    gaps = _header_gaps(c)
    lo, hi = _col_span(c)
    x_ns = c * COL_PITCH
    return {
        "E": {"slot": (x_e, y_ew), "header": e_head, "facing": (1.0, 0.0)},
        "W": {"slot": (x_w, y_ew), "header": w_head, "facing": w_face},
        "N": {"slot": (x_ns, hi), "header": (x_ns, max(gaps) + PORT_GAP_DY), "facing": (0.0, 1.0)},
        "S": {"slot": (x_ns, lo), "header": (x_ns, min(gaps) + PORT_GAP_DY), "facing": (0.0, -1.0)},
    }


def ports():
    """{edge: (x, y)}: where each port's cable crosses the tile edge."""
    return {e: p["slot"] for e, p in port_layout().items()}


def port_slots():
    """Wall cut-outs the cables pass through, as rectangles reaching past the wall."""
    out, h, t = [], PORT_SLOT_W / 2, SEAM_GAP / 2 + WALL_T + 0.5
    for edge, (x, y) in ports().items():
        if edge == "E":
            out.append((x - t, y - h, x + 1, y + h))
        elif edge == "W":
            out.append((x - 1, y - h, x + t, y + h))
        elif edge == "N":
            out.append((x - h, y - t, x + h, y + 1))
        else:
            out.append((x - h, y - 1, x + h, y + t))
    return out


# ----------------------------------------------------------------- helpers ---
def inside(x, y, poly):
    """Even-odd point-in-polygon."""
    hit = False
    for k in range(len(poly)):
        (x0, y0), (x1, y1) = poly[k - 1], poly[k]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def bbox(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def area(poly):
    return sum(x0 * y1 - x1 * y0
               for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1])) / 2


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _grow(r, d):
    return r[0] - d, r[1] - d, r[2] + d, r[3] + d


def _rect_inside(r, poly):
    """Rectangle inside a rectilinear polygon: corners in, no polygon vertex in."""
    corners = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    return (all(inside(x, y, poly) for x, y in corners)
            and not any(r[0] < x < r[2] and r[1] < y < r[3] for x, y in poly))


def to_kicad(x, y, margin=10.0):
    """Tile coordinates -> KiCad board coordinates (y down), with the ideal
    outline's top-left corner `margin` mm in from the sheet origin."""
    x0, _, _, y1 = bbox(outline())
    return x - x0 + margin, y1 - y + margin


# ------------------------------------------------------------------- check ---
def check():
    """Assert what the PCB and the case both rely on.  Returns summary lines."""
    eps = 1e-6
    p = ROW_PITCH
    ol = outline()

    # 1. Tiling.  The outline holds exactly KEYS_PER_TILE lattice cells and the
    #    period has the same area, so if shifted copies never overlap they
    #    cover the plane with no gaps.
    cell = COL_PITCH * ROW_PITCH
    assert abs(area(ol) - KEYS_PER_TILE * cell) < 1e-6, "outline area"
    assert abs(PERIOD[0] * PERIOD[1] - KEYS_PER_TILE * cell) < 1e-6, "period area"
    assert all(inside(x, y, ol) for _, _, _, x, y in keys()), "key outside its tile"
    shifts = [(i * PERIOD[0], j * PERIOD[1])
              for i in (-1, 0, 1) for j in (-1, 0, 1) if (i, j) != (0, 0)]
    x0, y0, x1, y1 = bbox(ol)
    samples = 0
    y = y0 + 0.37
    while y < y1:
        x = x0 + 0.53
        while x < x1:
            if inside(x, y, ol):
                samples += 1
                assert not any(inside(x - sx, y - sy, ol) for sx, sy in shifts), \
                    "neighbouring tiles overlap at (%.2f, %.2f)" % (x, y)
            x += 2.9
        y += 2.9

    # 2. The lattice survives the seams: every pair of keys closer than 1.5 pitches
    #    is exactly one pitch apart, inside a tile and across seams alike.
    ks = arrangement_keys([(0, 0), (1, 0), (0, 1), (1, 1)])
    assert len({k[4:] for k in ks}) == len(ks), "two keys share lattice coordinates"
    for _, _, x, y, a, b in ks:
        assert abs(x - a * COL_PITCH) < eps and abs(y - (a / 2 + b) * p) < eps
    seam_pairs = 0
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            d = math.dist(ks[i][2:4], ks[j][2:4])
            if d < 1.5 * p:
                assert abs(d - p) < eps, "neighbour off the lattice: %.3f mm" % d
                seam_pairs += ks[i][0] != ks[j][0]

    # 3. Ports face their partners, and their slots fit their edges.
    pt = ports()

    def at(t, e):
        ox, oy = tile_origin(*t)
        return ox + pt[e][0], oy + pt[e][1]

    # A cable crosses straight from one tile's slot into the next one's.
    assert math.dist(at((0, 0), "E"), at((1, 0), "W")) < eps, "E/W slots miss"
    assert math.dist(at((0, 0), "N"), at((0, 1), "S")) < eps, "N/S slots miss"
    if (COLS, ROWS) in PORT_SPOTS:
        lay = port_layout()

        def opening(t, e):
            ox, oy = tile_origin(*t)
            (hx, hy), (fx, fy) = lay[e]["header"], lay[e]["facing"]
            return ox + hx + fx * HEADER_FACE, oy + hy + fy * HEADER_FACE
        for a, b in (("E", "W"), ("N", "S")):
            shift = (1, 0) if a == "E" else (0, 1)
            d = math.dist(opening((0, 0), a), opening(shift, b))
            assert abs(d - CABLE_GAP) < eps, "%s/%s headers %.2f mm apart" % (a, b, d)
    wall = SEAM_GAP / 2 + WALL_T
    assert PORT_SLOT_W < COL_PITCH - 2 * wall, "N/S slot wider than its step"
    assert PORT_SLOT_W < (ROWS - 0.5) * p - 2 * wall, "E/W slot longer than the shared edge"

    # 4. Tray: switches clear the walls and ribs.
    inner = case_inner()
    keepout = [_grow(r, SWITCH_CLEAR) for r in switch_cutouts()]
    assert all(_rect_inside(r, inner) for r in keepout), "switch hits a wall"
    assert not any(overlaps(r, k) for r in ribs() for k in keepout), "rib hits a switch"

    # 5. Cover: posts sit under ribs, on bare PCB away from the sensors, clear
    #    of the beam slots; the PCB drops in past the lip and bumps; the beams
    #    bend little enough to survive repeated opening.
    pcb, cov = pcb_outline(), cover_outline()
    ribset = ribs()
    for x, y in posts():
        assert any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in ribset), "post not under a rib"
        assert min(math.dist((x, y), k[3:]) for k in keys()) >= POST_KEEPOUT + 5.0, \
            "post too close to a sensor and its capacitors"
        assert _rect_inside(_rect(x, y, 2 * POST_KEEPOUT, 2 * POST_KEEPOUT), pcb), "post off the PCB"
        assert _rect_inside(_rect(x, y, POST_D, POST_D), cov), "post off the cover"
        for sx, sy, L, w in beam_slots():
            assert not overlaps(_rect(x, y, POST_D, POST_D), _rect(sx, sy, w, L)), "post in a beam slot"
    x_w, x_e = side(inner, "W")[0], side(inner, "E")[0]
    assert side(pcb, "W")[0] >= x_w + LIP_W + PCB_CLEAR - eps, "PCB catches the lip"
    assert side(pcb, "E")[0] <= x_e - CATCH_D - PCB_CLEAR + eps, "PCB catches the bumps"
    _, lo, hi = side(cov, "E")
    for (y, L), (n0, n1) in zip(catch_beams(), pry_notches()):
        assert lo + 3 <= y - L / 2 and y + L / 2 <= hi - 3, "beam runs off the edge"
        assert n1 <= y + L / 2, "pry notch beyond the flexing part of the beam"
        # fixed-fixed beam pushed aside at mid-span: peak strain = 12 * d * w / L^2.
        # PLA is the brittle case: keep a flex that repeats under ~1.5 %.
        strain = 12 * (CATCH_D - COVER_CLEAR) * BEAM_W / L ** 2
        assert strain < 0.015, "beam strain %.1f%% -- too high for PLA" % (100 * strain)
    assert all(inside(x, y, pcb) for x, y in peg_holes()), "switch peg off the PCB"

    # 6. Fits the printer, and the caps fit the lattice.
    bx0, by0, bx1, by1 = bbox(case_outer())
    assert bx1 - bx0 <= PRINT_BED and by1 - by0 <= PRINT_BED, "case exceeds the bed"
    assert ROW_PITCH > CAP_AF, "caps collide"

    beams = catch_beams()
    return [
        "tile          %d x %d = %d keys, %d muxes (8:1)"
        % (COLS, ROWS, KEYS_PER_TILE, math.ceil(KEYS_PER_TILE / 8)),
        "pitch         %.2f mm all six ways (columns %.3f apart)" % (p, COL_PITCH),
        "case          %.2f x %.2f x %.2f mm (bed %.0f)"
        % (bx1 - bx0, by1 - by0, Z_FLOOR, PRINT_BED),
        "cap gap       %.2f mm; caps overhang the tile edge by %.2f mm"
        % (p - CAP_AF, CAP_AF / math.sqrt(3) - COL_PITCH / 2),
        "tiling        OK (%d samples, no overlap; area = %d cells)"
        % (samples, KEYS_PER_TILE),
        "lattice       OK (2x2 tiles, %d neighbour pairs across seams, all %.2f mm)"
        % (seam_pairs, p),
        "ports         OK (E/W slots at y=%.1f, N/S slots on column %d)"
        % (pt["E"][1], round(pt["N"][0] / COL_PITCH) + 1),
        "cover         OK (%d posts under ribs; %d catch beam%s, %.1f mm bite, %.1f%% strain)"
        % (len(posts()), len(beams), "s" * (len(beams) > 1), CATCH_D - COVER_CLEAR,
           100 * max(12 * (CATCH_D - COVER_CLEAR) * BEAM_W / L ** 2 for _, L in beams)),
    ]


if __name__ == "__main__":
    print("\n".join(check()))
