#!/usr/bin/env python3
"""Shared geometry for one tile of the modular hex keyboard.

The single source for every dimension that the PCB and the printed case have
to agree on.  gen_case.py builds the case from it and gen_pcb.py placed the
board's footprints from it -- two copies of one fact is how the board and its
netlist drifted apart before.  The board is routed now, so whatever it was
built on (the outline, the peg holes, the ports, the posts) is frozen.

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
tucks over a lip on the W wall, then its E edge swings up until its catch beams
click over bumps on the E wall.  Posts on the cover press the PCB up against
the ribs.  To open: pry a catch beam's tip down, at the notch in the wall.

Tiles snap together, then the cables go in from underneath.  The stepped N/S
edges interlock, so a tile can only be lowered into place: slots in its W and
S walls slide down over dovetail rails on its neighbours' E and N walls, and a
snap latch on each of those walls springs into a window in the tile.  So build
outward from the south-west tile and take apart from the north-east one; a
firm pull straight up releases the latches.  Then turn the keyboard over: at
every port the cover and both walls are cut away, so a cable drops into an
open channel that runs from one tile's header to the next one's.
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
    """Temporarily re-size the tile -- e.g. the 2 x 4 test coupon, which carries
    the cover's and the joints' features at a fraction of the print time.  The
    size needs an entry in JOINT_SPOTS."""
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

# Bottom cover.  (The routed board's outline is built on LIP_W and CATCH_D.)
COVER_CLEAR = 0.2       # cover edge to the wall's inner face
LIP_W = 1.5             # W lip: how far it reaches under the cover
CATCH_D = 0.9           # E bumps reach under the cover by CATCH_D - COVER_CLEAR;
                        # their retaining face is 45 degrees so a pry releases them
CATCH_L = 5.0           # length of each bump along the edge
BEAM_W = 4.0            # catch beam: a strip of the cover's E edge cut free as a
                        # cantilever, which flexes past a bump
BEAM_SLOT = 1.2         # the cuts that free it from the rest of the cover
PRY_W = 4.5             # notch in the E wall's foot at a beam's tip, to get a
                        # fingernail or a screwdriver under it
POST_D = 4.0            # posts that press the PCB against the ribs
POST_PRELOAD = 0.2      # posts this much too tall, so the cover keeps pressure on
POST_KEEPOUT = 4.0      # radius kept bare on the PCB's back around each post

# Edge ports: a JST GH 6-pin side-entry header on the PCB's back at each edge.
# From the header out to the tile's edge the cover is cut away and the wall is
# open down to the floor, so the cable is laid in from underneath.
PORT_SLOT_W = 14.0      # channel width; the GH 6-pin header is 12.05 mm wide
                        # over its courtyard
CHANNEL_BACK = 1.5      # the cover's cut-out reaches this far behind the header's
                        # opening, so the plug's lock can be seen and reached

# Tile-to-tile joints.  Dovetail rails stand on the E and N walls' outer faces
# and run in slots through the next tile's W and S walls, open at the floor.
# One snap latch per seam stops that tile lifting off again: a strip of the
# E / N wall, cut free from the plate down as a cantilever, with a hook on its
# outer face that springs into a window in the neighbour's wall.
RAIL_W = 5.0            # rail width at its own wall; its flanks spread at 45 degrees
RAIL_REACH = 1.25       # how far it stands out: 0.25 short of the neighbour's
                        # inner face, so it never touches a lip, cover or PCB
RAIL_CLEAR = 0.15       # rail flank to slot flank, square to the flank
RAIL_TOP = 3.0          # the rail's head ends this far below the plate top and
                        # slopes back to the wall at 45 degrees: printable, and
                        # it leads the slot on
RAIL_FLARE = 0.8        # the slot's mouth at the floor, wider by this each side
LATCH_W = 10.0          # latch beam: this much wall
LATCH_SLIT = 1.0        # slit either side of it
LATCH_TIP_T = 0.7       # the beam thins from WALL_T at its root to this at the
                        # floor: spreads the bending, and clears the PCB's edge
LATCH_LIFT = 0.3        # its end stops this short of the floor, clear of the table
LATCH_BITE = 0.6        # hook's overlap with the neighbour's wall
LATCH_ANGLE = 50.0      # its catch face, from the lifting direction.  THE knob:
                        # 90 never lets go, 45 barely holds a hanging tile
LATCH_PLAY = 0.2        # catch face to the window's edge, tile seated
LATCH_FLAT = 0.3        # hook's crest
LATCH_RAMP = 1.5        # height of its lead-in ramp (a ~30 degree overhang)
LATCH_RELIEF = 1.0      # notch in the cover's edge, so the beam can swing in
PLA_E = 3000.0          # MPa; only for the snap forces that check() reports

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


# Hand-placed catches and joints, per tile size.  Along the E wall a place is a
# y; along the N wall it is a column, and the feature sits mid-column.  The W
# and S walls carry the matching slot or window at the same place.
#   e_rails, n_rails    dovetail rails
#   e_latch, n_latch    the seam's snap latch
#   catches             (y_root, y_tip) of each catch beam on the cover's E edge
# 8 x 5: the E port's channel splits the cover's E edge, so a beam ends either
# side of it, tips toward the port.  2 x 4 is the test coupon: the southern
# half of the tile's E wall, and two columns of its N wall.  check() proves
# that nothing here runs into a rib, a port, a wall's end or its neighbour.
JOINT_SPOTS = {
    (8, 5): {"e_rails": (6.0, 100.0), "e_latch": 33.0, "n_rails": (0, 7), "n_latch": 3,
             "catches": ((40.0, 70.33), (114.5, 84.33))},
    (2, 4): {"e_rails": (6.0, 76.5), "e_latch": 33.0, "n_rails": (0,), "n_latch": 1,
             "catches": ((40.0, 70.33),)},
}


def _joints():
    try:
        return JOINT_SPOTS[COLS, ROWS]
    except KeyError:
        raise ValueError("no catches and joints laid out for a %d x %d tile: "
                         "add it to JOINT_SPOTS" % (COLS, ROWS)) from None


def catch_beams():
    """[(y_root, y_tip)] of the catch beams: cantilevers cut from the cover's
    E edge, BEAM_W wide."""
    return list(_joints()["catches"])


def _from_tip(beam, a, b):
    """(y0, y1) of the stretch from a to b behind a beam's tip, toward its root
    (negative: beyond the tip)."""
    root, tip = beam
    d = math.copysign(1.0, tip - root)
    return tuple(sorted((tip - d * a, tip - d * b)))


def beam_cuts():
    """Per beam, the two cuts that free it from the cover, as rectangles: a
    slot along its inner side (gen_case.py rounds its ends) and a cut across
    its tip."""
    x_e = side(cover_outline(), "E")[0]
    out = []
    for beam in catch_beams():
        y0, y1 = _from_tip(beam, -BEAM_SLOT, abs(beam[1] - beam[0]))
        e0, e1 = _from_tip(beam, -BEAM_SLOT, 0.0)
        out.append(((x_e - BEAM_W - BEAM_SLOT, y0, x_e - BEAM_W, y1),
                    (x_e - BEAM_W - BEAM_SLOT / 2, e0, x_e + 1.0, e1)))
    return out


def pry_notches():
    """(y0, y1) of the notch in the E wall's foot at each beam's tip."""
    return [_from_tip(b, -BEAM_SLOT, PRY_W - BEAM_SLOT) for b in catch_beams()]


def catch_bumps():
    """(y0, y1) of each bump on the E wall: just behind its beam's pry notch."""
    near = PRY_W - BEAM_SLOT + 0.5
    return [_from_tip(b, near, near + CATCH_L) for b in catch_beams()]


def catch_strain():
    """Peak strain in each catch beam as it rides over its bump.  A cantilever
    pushed aside by d at distance a from its root: 1.5 * d * w / a^2.  The
    bump is straight and the bent beam is not, so it bears on the bump's
    root-side end."""
    return [1.5 * (CATCH_D - COVER_CLEAR) * BEAM_W / min(abs(y - root) for y in bump) ** 2
            for (root, _), bump in zip(catch_beams(), catch_bumps())]


# ------------------------------------------------------------------- joints ---
# A joint is drawn in its seam's frame: n is the distance outward from the
# outer face of the wall that carries the rails, t runs along the wall.  On E
# and N that wall is this tile's; on W and S it is the neighbour's, SEAM_GAP
# away, and this tile's own wall is n = SEAM_GAP .. SEAM_GAP + WALL_T.  Either
# way n points toward +x or +y.
OPPOSITE = {"E": "W", "W": "E", "N": "S", "S": "N"}


def flat(poly, edge, t=0.0):
    """(position, lo, hi) of one straight stretch of a tile-shaped outline: the
    whole W or E edge, or on N and S the flat of the column at x = t.  Relies
    on the corner order of outline()."""
    if edge in "WE":
        return side(poly, edge)
    col = round(t / COL_PITCH)
    k = 2 * col if edge == "S" else 4 * COLS - 2 - 2 * col
    (xa, y), (xb, _) = poly[k], poly[k + 1]
    return y, min(xa, xb), max(xa, xb)


def seam_face(edge, t=0.0):
    """Where n = 0 lies for the seam on `edge` (at x = t, for N and S)."""
    pos = flat(case_outer(), edge, t)[0]
    return pos - SEAM_GAP if edge in "WS" else pos


def seam_rect(edge, t, half, n0, n1):
    """The rectangle t +/- half, n0 .. n1 of a seam frame, in tile coordinates."""
    f = seam_face(edge, t)
    if edge in "WE":
        return f + n0, t - half, f + n1, t + half
    return t - half, f + n0, t + half, f + n1


def seam_span(edge, t=0.0):
    """(lo, hi) along the wall: where this tile's wall and the one facing it
    across the seam are both flat, on their inner faces."""
    a, b = flat(case_inner(), edge, t), flat(case_inner(), OPPOSITE[edge], t)
    return max(a[1], b[1]), min(a[2], b[2])


def rails():
    """{edge: [t]}: the dovetail rails on the E wall (t = y) and the N wall
    (t = x).  The slots they run in are at the same t on W and S."""
    j = _joints()
    return {"E": list(j["e_rails"]), "N": [c * COL_PITCH for c in j["n_rails"]]}


def latches():
    """{edge: [t]}: the snap latches on E and N; their windows are at the same
    t on W and S."""
    j = _joints()
    return {"E": [j["e_latch"]], "N": [j["n_latch"] * COL_PITCH]}


def rail_half(n):
    """Half-width of a rail at n: its flanks spread at 45 degrees."""
    return RAIL_W / 2 + n


def slot_half(n):
    """Half-width of a rail's slot at n: RAIL_CLEAR off the rail's flank."""
    return rail_half(n) + RAIL_CLEAR * math.sqrt(2)


def rail_slot_top():
    """z of a slot's closed end: RAIL_CLEAR above the rail's bevel where that
    is highest inside the slot, at the wall's outer face.  It is what stops a
    tile sinking below its neighbours when the keyboard is carried."""
    return -(RAIL_TOP - RAIL_REACH + SEAM_GAP - RAIL_CLEAR)


def latch_hook():
    """The hook's outline in the seam frame, [(n, z)] from its foot up: the
    catch face, the crest, the lead-in ramp."""
    cot = 1 / math.tan(math.radians(LATCH_ANGLE))
    reach = SEAM_GAP + LATCH_BITE
    z = -Z_COVER_BOT + LATCH_PLAY       # the catch face crosses the neighbour's outer face here
    z0, z1 = z - SEAM_GAP * cot, z + LATCH_BITE * cot
    return [(0.0, z0), (reach, z1), (reach, z1 + LATCH_FLAT),
            (0.0, z1 + LATCH_FLAT + LATCH_RAMP)]


def latch_window():
    """(z_lo, z_hi) of the window a hook springs into.  The rim of the wall
    left below it, as tall as the lip, is what the hook holds."""
    return -Z_COVER_BOT, latch_hook()[-1][1] + 0.3


def latch_taper(z):
    """How much thinner than WALL_T the latch beam is at height z."""
    return (WALL_T - LATCH_TIP_T) * (-Z_PLATE - z) / (Z_FLOOR - Z_PLATE)


def latch_flex(steps=400):
    """The latch beam with its hook pushed in by LATCH_BITE, far enough to pass
    the neighbour's wall: (peak strain, force on the hook in N if E = PLA_E,
    how far the beam moves in at the PCB's back face).  A tapered cantilever
    loaded at the hook's crest, integrated numerically."""
    hook = latch_hook()
    a = -(hook[1][1] + hook[2][1]) / 2 - Z_PLATE        # root to the crest
    ds = a / steps
    mids = [(k + 0.5) * ds for k in range(steps)]

    def thick(s):
        return WALL_T - latch_taper(-Z_PLATE - s)

    def curv(s):                                        # per unit of force / E
        return (a - s) / (LATCH_W * thick(s) ** 3 / 12)

    def moved(at):
        return sum(curv(s) * (at - s) for s in mids if s < at) * ds

    load = LATCH_BITE / moved(a)                        # force / E
    strain = max(load * curv(s) * thick(s) / 2 for s in mids)
    return strain, load * PLA_E, load * moved(Z_PCB_BOT - Z_PLATE)


# -------------------------------------------------------------------- ports ---
# A header in a gap sits below the gap's centre: the free band runs from the
# lower key's sensor up to the upper key's LED, which reaches further.
PORT_GAP_DY = -2.3
PLUG_PROTRUSION = 3.1   # a mated GHR plug sticks out this far past the header's
                        # face (JST eGH, mated drawing: 7.15 mm back to back,
                        # header body 4.05 mm), and its wire leaves straight back
HEADER_FACE = 2.45      # footprint origin to the header's opening (KiCad's
                        # JST_GH_SM06B-GHS-TB footprint: body front at y = 2.45)
CABLE_GAP = 50.0        # partner headers' openings this far apart across a seam,
                        # so a stock 50 mm GH cable runs straight between them

# Hand-placed headers, per tile size: header origins in tile coordinates.  For
# 8 x 5 these came from a search over the routed board (2026-10-20): E/W at one
# height and N/S on one column, each plug and its straight cable clear of
# switch pegs, posts and parts, ending at a flat stretch of wall.  The N/S
# column's posts move to the gaps the headers leave free.  Sizes not listed
# here (the test coupon) have no ports.
PORT_SPOTS = {
    (8, 5): {"ew_y": 77.33, "w_x": 25.94, "ns_col": 4, "s_y": 18.58, "ns_post_gaps": (1, 2)},
}


def port_layout():
    """{edge: {"slot": (x, y), "header": (x, y), "facing": (dx, dy)}} -- empty
    for a tile size without hand-placed headers (PORT_SPOTS).

    Each header opens toward its own edge, and its partner on the next tile is
    CABLE_GAP away straight across the seam.  So facing channels line up: E
    and W share a height, N and S share a column.  "slot" is where the cable
    crosses the tile's edge.

    Neighbouring tiles' edge columns alternate in height, so wherever the E
    header sits in a gap, the W channel of the next tile meets a key: the
    cable passes under that key's switch peg (pegs reach ~1.4 mm below the
    board).
    """
    spot = PORT_SPOTS.get((COLS, ROWS))
    if not spot:
        return {}
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


def ports():
    """{edge: (x, y)}: where each port's cable crosses the tile edge."""
    return {e: p["slot"] for e, p in port_layout().items()}


def port_slots():
    """Wall cut-outs the cables lie in, as rectangles reaching past the wall --
    and on W past the lip as well, which would otherwise bar the channel."""
    out, h, t = [], PORT_SLOT_W / 2, SEAM_GAP / 2 + WALL_T + 0.5
    for edge, (x, y) in ports().items():
        if edge == "E":
            out.append((x - t, y - h, x + 1, y + h))
        elif edge == "W":
            out.append((x - 1, y - h, x + t + LIP_W, y + h))
        elif edge == "N":
            out.append((x - h, y - t, x + h, y + 1))
        else:
            out.append((x - h, y - 1, x + h, y + t))
    return out


def cover_notches():
    """Rectangles cut out of the cover's edge: the cable channel from just
    behind each header's opening out to the edge, and a relief behind each
    latch beam so it can swing in."""
    out, h = [], PORT_SLOT_W / 2
    for lay in port_layout().values():
        (hx, hy), (fx, fy), (sx, sy) = lay["header"], lay["facing"], lay["slot"]
        back = HEADER_FACE - CHANNEL_BACK
        xs, ys = sorted((hx + fx * back, sx + fx)), sorted((hy + fy * back, sy + fy))
        out.append((xs[0] - h * abs(fy), ys[0] - h * abs(fx),
                    xs[1] + h * abs(fy), ys[1] + h * abs(fx)))
    for edge, ts in latches().items():
        out += [seam_rect(edge, t, LATCH_W / 2 + LATCH_SLIT / 2,
                          -WALL_T - COVER_CLEAR - LATCH_RELIEF, 0.0) for t in ts]
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

    # 3. Ports face their partners, and their channels fit their edges.
    pt = ports()
    if pt:
        def at(t, e):
            ox, oy = tile_origin(*t)
            return ox + pt[e][0], oy + pt[e][1]

        # A cable crosses straight from one tile's channel into the next one's.
        assert math.dist(at((0, 0), "E"), at((1, 0), "W")) < eps, "E/W channels miss"
        assert math.dist(at((0, 0), "N"), at((0, 1), "S")) < eps, "N/S channels miss"
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
        assert PORT_SLOT_W < COL_PITCH - 2 * wall, "N/S channel wider than its step"
        assert PORT_SLOT_W < (ROWS - 0.5) * p - 2 * wall, "E/W channel longer than the shared edge"

    # 4. Tray: switches clear the walls and ribs.
    inner = case_inner()
    keepout = [_grow(r, SWITCH_CLEAR) for r in switch_cutouts()]
    assert all(_rect_inside(r, inner) for r in keepout), "switch hits a wall"
    assert not any(overlaps(r, k) for r in ribs() for k in keepout), "rib hits a switch"

    # 5. Cover: posts sit under ribs, on bare PCB away from the sensors, clear
    #    of every cut in the cover; the PCB drops in past the lip and bumps;
    #    the catch beams bend little enough to survive repeated opening.
    pcb, cov = pcb_outline(), cover_outline()
    ribset = ribs()
    notches = cover_notches()
    cuts = notches + [r for pair in beam_cuts() for r in pair]
    for x, y in posts():
        assert any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in ribset), "post not under a rib"
        assert min(math.dist((x, y), k[3:]) for k in keys()) >= POST_KEEPOUT + 5.0, \
            "post too close to a sensor and its capacitors"
        assert _rect_inside(_rect(x, y, 2 * POST_KEEPOUT, 2 * POST_KEEPOUT), pcb), "post off the PCB"
        post = _rect(x, y, POST_D, POST_D)
        assert _rect_inside(post, cov), "post off the cover"
        assert not any(overlaps(_grow(post, 1.0), c) for c in cuts), "post at a cut in the cover"
    x_w, x_e = side(inner, "W")[0], side(inner, "E")[0]
    assert side(pcb, "W")[0] >= x_w + LIP_W + PCB_CLEAR - eps, "PCB catches the lip"
    assert side(pcb, "E")[0] <= x_e - CATCH_D - PCB_CLEAR + eps, "PCB catches the bumps"
    _, lo, hi = side(cov, "E")
    for (root, tip), strain, (_, end) in zip(catch_beams(), catch_strain(), beam_cuts()):
        # The corner of cover behind a beam's root carries its bending moment.
        assert min(root - lo, hi - root) >= BEAM_W, "catch beam rooted too near the cover's corner"
        # The cut across the tip either lies inside the edge or opens into a channel.
        in_channel = any(c[0] <= end[0] and c[1] <= end[1] + eps and end[3] <= c[3] + eps
                         for c in notches)
        assert in_channel or (lo + 3 <= end[1] and end[3] <= hi - 3), "catch beam's tip runs off the edge"
        # PLA is the brittle case: keep a flex that repeats near 1 %.
        assert strain < 0.012, "catch beam strain %.1f%% -- too high for PLA" % (100 * strain)
    assert all(inside(x, y, pcb) for x, y in peg_holes()), "switch peg off the PCB"

    # 6. Joints.  Along each wall, everything keeps its distance from the wall's
    #    ends and from everything else; nothing that cuts a wall meets a rib.
    assert RAIL_REACH < SEAM_GAP + WALL_T, "rail reaches past the neighbour's wall, into its lip"
    grip = RAIL_REACH - SEAM_GAP - RAIL_CLEAR * math.sqrt(2)
    assert grip > 0.5, "dovetail too shallow to hold tiles together"
    assert rail_slot_top() < -Z_PLATE, "rail slot cuts into the plate"
    hook = latch_hook()
    assert hook[0][1] > -Z_FLOOR + LATCH_LIFT + 1.0, "latch hook too near the beam's end"
    assert hook[-1][1] < -Z_PCB_BOT, "latch hook as high as the PCB"
    strain, force, at_pcb = latch_flex()
    # The beams stand upright in the print and bend across its layers.
    assert strain < 0.008, "latch strain %.1f%% -- too high across layers" % (100 * strain)
    assert at_pcb + 0.1 < PCB_CLEAR + latch_taper(-Z_PCB_BOT), "latch beam reaches the PCB's edge"
    slit = LATCH_W / 2 + LATCH_SLIT
    r_rail, r_latch = rails(), latches()

    def on_wall(edge):
        """[(what, lo, hi, the flat it has to fit)] of everything on one wall,
        along the wall.  E and N carry rails (on their outer face) and latch
        beams; W and S the rails' slots, at their flared mouths, and the
        latches' windows."""
        if edge in "EN":
            sizes = (edge, rail_half(RAIL_REACH), case_outer(), slit)
        else:
            sizes = (OPPOSITE[edge], slot_half(SEAM_GAP) + RAIL_FLARE, inner, LATCH_W / 2 + 0.5)
        male, h_rail, rail_on, h_latch = sizes
        out = [("rail", t - h_rail, t + h_rail, flat(rail_on, edge, t)) for t in r_rail[male]]
        out += [("latch", t - h_latch, t + h_latch, flat(inner, edge, t)) for t in r_latch[male]]
        if edge in pt:
            c = pt[edge][edge in "EW"]
            out.append(("port", c - PORT_SLOT_W / 2, c + PORT_SLOT_W / 2, None))
        if edge == "E":
            out += [("bump", y0, y1, None) for y0, y1 in catch_bumps()]
            out += [("pry", y0, y1, None) for y0, y1 in pry_notches()]
        return out

    for edge in "ENWS":
        things = on_wall(edge)
        for what, a, b, fits in things:
            assert fits is None or (fits[1] + 1.5 <= a and b <= fits[2] - 1.5), \
                "%s at %.1f too near the %s wall's end" % (what, (a + b) / 2, edge)
        for i, (wa, a0, a1, _) in enumerate(things):
            for wb, b0, b1, _ in things[i + 1:]:
                if {wa, wb} == {"pry", "port"}:
                    continue                # a pry notch may open into the port's channel
                gap = 0.4 if {wa, wb} == {"pry", "bump"} else 1.0
                assert a1 + gap <= b0 or b1 + gap <= a0, \
                    "%s and %s too close on the %s wall" % (wa, wb, edge)
    for edge in "EN":
        other = OPPOSITE[edge]
        through = [seam_rect(edge, t, slit, -WALL_T - 0.5, 0.0) for t in r_latch[edge]]
        through += [seam_rect(other, t, slot_half(SEAM_GAP + WALL_T), SEAM_GAP, SEAM_GAP + WALL_T + 0.5)
                    for t in r_rail[edge]]
        assert not any(overlaps(_grow(c, 0.5), r) for c in through for r in ribset), \
            "a rib ends at a latch or a rail slot"
    relief = LATCH_W / 2 + LATCH_SLIT / 2
    for t in r_latch["E"]:
        assert all(t + relief + 1.0 <= min(b) or max(b) + 1.0 <= t - relief for b in catch_beams()), \
            "latch relief cuts into a catch beam"

    # 7. Fits the printer, and the caps fit the lattice.
    bx0, by0, bx1, by1 = bbox(case_outer())
    bx1, by1 = bx1 + RAIL_REACH, by1 + RAIL_REACH
    assert bx1 - bx0 <= PRINT_BED and by1 - by0 <= PRINT_BED, "case exceeds the bed"
    assert ROW_PITCH > CAP_AF, "caps collide"

    beams = catch_beams()
    n_rails = sum(len(v) for v in r_rail.values())
    return [
        "tile          %d x %d = %d keys, %d muxes (8:1)"
        % (COLS, ROWS, KEYS_PER_TILE, math.ceil(KEYS_PER_TILE / 8)),
        "pitch         %.2f mm all six ways (columns %.3f apart)" % (p, COL_PITCH),
        "case          %.2f x %.2f x %.2f mm with its rails (bed %.0f)"
        % (bx1 - bx0, by1 - by0, Z_FLOOR, PRINT_BED),
        "cap gap       %.2f mm; caps overhang the tile edge by %.2f mm"
        % (p - CAP_AF, CAP_AF / math.sqrt(3) - COL_PITCH / 2),
        "tiling        OK (%d samples, no overlap; area = %d cells)"
        % (samples, KEYS_PER_TILE),
        "lattice       OK (2x2 tiles, %d neighbour pairs across seams, all %.2f mm)"
        % (seam_pairs, p),
        "ports         OK (E/W channels at y=%.1f, N/S channels on column %d)"
        % (pt["E"][1], round(pt["N"][0] / COL_PITCH) + 1) if pt else
        "ports         none at this size",
        "cover         OK (%d posts under ribs; %d catch beam%s, %.1f mm bite, %.1f%% strain)"
        % (len(posts()), len(beams), "s" * (len(beams) > 1), CATCH_D - COVER_CLEAR,
           100 * max(catch_strain())),
        "joints        OK (%d rails, %.2f mm dovetail grip; %d latches, %.1f mm bite, "
        "%.1f%% strain, ~%.1f N on a hook in PLA)"
        % (n_rails, grip, sum(len(v) for v in r_latch.values()), LATCH_BITE, 100 * strain, force),
    ]


if __name__ == "__main__":
    print("\n".join(check()))
