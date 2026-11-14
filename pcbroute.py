"""A small router for scripts that change a routed board in place.

It knows every pad, track, via, hole and board edge, and offers these ways
to add copper:

    r.line(net, [(x, y), ...])        an explicit polyline, checked exactly
    r.via(net, x, y)                  a via; track_ok() and via_ok() tell beforehand
                                      whether a track or a via would fit
    r.auto(net, a, b)                 a grid search (A*) between two points,
                                      over one or both layers, vias allowed
    r.connect(net, a, b)              the same search between two pieces of a net:
                                      from anywhere on the copper pad a hangs on
                                      to anywhere on the copper pad b hangs on
    r.join(net)                       connect() until the net is one piece

Ground and ties() are for the last two stages of a routing script: a via to
the front's ground pour at every ground pad, then vias between the pours.

All keep the board's own clearances: the larger of the two nets' net-class
clearances, 0.25 mm to holes and to the board edge.  KiCad's DRC stays the
judge (run it afterwards); this only makes a wrong track rare and its cause
immediate.  Coordinates are millimetres in KiCad's frame (x right, y down).
Layers are "B" (B.Cu, where every part is) and "F" (F.Cu).  A through-hole
pad is on both, and a path may start or end at one on either layer.
"""

import heapq
import math

import numpy as np
import pcbnew

GRID = 0.05                 # mm between search nodes
MARGIN = 0.036              # > GRID * sqrt(2) / 2: a step between two free nodes stays clear
EDGE_CLEAR = 0.25
HOLE_CLEAR = 0.25
LAYER = {"F": pcbnew.F_Cu, "B": pcbnew.B_Cu}
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
_REMOVED = []


def _pt_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _seg_seg(ax, ay, bx, by, cx, cy, dx, dy):
    def side(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)
    d1, d2 = side(cx, cy, dx, dy, ax, ay), side(cx, cy, dx, dy, bx, by)
    d3, d4 = side(ax, ay, bx, by, cx, cy), side(ax, ay, bx, by, dx, dy)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(_pt_seg(ax, ay, cx, cy, dx, dy), _pt_seg(bx, by, cx, cy, dx, dy),
               _pt_seg(cx, cy, ax, ay, bx, by), _pt_seg(dx, dy, ax, ay, bx, by))


def _seg_rect(ax, ay, bx, by, x0, y0, x1, y1):
    if (x0 <= ax <= x1 and y0 <= ay <= y1) or (x0 <= bx <= x1 and y0 <= by <= y1):
        return 0.0
    return min(_seg_seg(ax, ay, bx, by, x0, y0, x1, y0), _seg_seg(ax, ay, bx, by, x1, y0, x1, y1),
               _seg_seg(ax, ay, bx, by, x1, y1, x0, y1), _seg_seg(ax, ay, bx, by, x0, y1, x0, y0))


def _lead(centre, node):
    """The way from a pad's centre to the node a path leaves the pad at, less
    the node: straight, or with one bend if that would be at an odd angle.
    The bend lies in the pad: straight out of the node, then 45 degrees in."""
    dx, dy = abs(node[0] - centre[0]), abs(node[1] - centre[1])
    k = min(dx, dy)
    if k <= GRID / 2 + 1e-9 or abs(dx - dy) < 1e-6:
        return [centre]
    return [centre, (round(centre[0] + math.copysign(k, node[0] - centre[0]), 6),
                     round(centre[1] + math.copysign(k, node[1] - centre[1]), 6))]


class Router:
    def __init__(self, board, keepouts=(), origin=None):
        """keepouts: where the searches place no via: circles (x, y, r), the cover
        posts, and rectangles (x0, y0, x1, y1).
        origin: a point the search grid goes through, for a chip whose pads
        would otherwise lie between its nodes."""
        self.b = board
        self.keepouts = list(keepouts)
        self.origin = origin
        ds = board.GetDesignSettings()
        ns = ds.m_NetSettings
        self.classes = {}
        for name, nc in list(ns.GetNetclasses().items()) + [("Default", ns.GetDefaultNetclass())]:
            self.classes[str(name)] = (pcbnew.ToMM(nc.GetClearance()), pcbnew.ToMM(nc.GetTrackWidth()),
                                       pcbnew.ToMM(nc.GetViaDiameter()), pcbnew.ToMM(nc.GetViaDrill()))
        self._class_of = {}
        # Obstacles per layer: ("R", net, x0, y0, x1, y1) pad, ("S", net, ax, ay, bx, by, hw)
        # track, ("C", net, x, y, r) via.  On both: ("H", x, y, r) hole, ("E", ax, ay, bx, by) edge.
        self.obs = {"F": [], "B": []}
        self.fixed = []
        self.pads = {}
        self.boxes = {}
        self.terminals = []                   # every pad: (ref, number, net, x, y, box, layers)
        self.added = []                       # what this run added, for check()
        self._items = []                      # and the board's item for each, for take()
        mm = pcbnew.ToMM
        for fp in board.GetFootprints():
            for p in fp.Pads():
                pos = p.GetPosition()
                self.pads[(fp.GetReference(), p.GetNumber())] = (mm(pos.x), mm(pos.y), p.GetNetname())
                bb = p.GetBoundingBox()
                box = (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))
                self.boxes[(fp.GetReference(), p.GetNumber())] = box
                if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                    self.fixed.append(("H", mm(pos.x), mm(pos.y), mm(p.GetDrillSize().x) / 2))
                    continue
                on = "".join(l for l in "FB" if p.IsOnLayer(LAYER[l]))
                self.terminals.append((fp.GetReference(), p.GetNumber(), p.GetNetname(),
                                       mm(pos.x), mm(pos.y), box, on))
                for l in on:
                    self.obs[l].append(("R", p.GetNetname()) + box)
        for t in board.GetTracks():
            if t.GetClass() == "PCB_VIA":
                self._note_via(t.GetNetname(), mm(t.GetPosition().x), mm(t.GetPosition().y),
                               mm(t.GetWidth(pcbnew.F_Cu)) / 2)
            else:
                l = "F" if t.GetLayer() == pcbnew.F_Cu else "B"
                self.obs[l].append(("S", t.GetNetname(), mm(t.GetStart().x), mm(t.GetStart().y),
                                    mm(t.GetEnd().x), mm(t.GetEnd().y), mm(t.GetWidth()) / 2))
        xs, ys = [], []
        for d in board.GetDrawings():
            if d.GetLayer() == pcbnew.Edge_Cuts:
                e = ("E", mm(d.GetStart().x), mm(d.GetStart().y), mm(d.GetEnd().x), mm(d.GetEnd().y))
                self.fixed.append(e)
                xs += [e[1], e[3]]
                ys += [e[2], e[4]]
        self.X0, self.Y0 = min(xs) - 0.2, min(ys) - 0.2
        if origin:
            self.X0 = origin[0] - GRID * math.ceil((origin[0] - self.X0) / GRID - 1e-9)
            self.Y0 = origin[1] - GRID * math.ceil((origin[1] - self.Y0) / GRID - 1e-9)
        self.NX = int((max(xs) + 0.2 - self.X0) / GRID) + 1
        self.NY = int((max(ys) + 0.2 - self.Y0) / GRID) + 1

    # ------------------------------------------------------------ net facts ---
    def cls(self, net):
        if net not in self._class_of:
            n = self.b.FindNet(net)
            assert n is not None, "no net %r" % net
            self._class_of[net] = self.classes.get(str(n.GetNetClassName()), self.classes["Default"])
        return self._class_of[net]

    def clearance(self, a, b):
        return max(self.cls(a)[0], self.cls(b)[0])

    def width(self, net):
        return self.cls(net)[1]

    def pad(self, ref, num):
        """Centre of a pad, e.g. r.pad("U1", 9)."""
        x, y, _ = self.pads[(ref, str(num))]
        return (x, y)

    def _note_via(self, net, x, y, r):
        for l in "FB":
            self.obs[l].append(("C", net, x, y, r))

    # ---------------------------------------------------------- exact check ---
    def _gap(self, net, layer, ax, ay, bx, by, hw, skip=None):
        """Every obstacle closer to this track piece than the rules allow."""
        bad = []
        lo_x, hi_x, lo_y, hi_y = min(ax, bx) - 2, max(ax, bx) + 2, min(ay, by) - 2, max(ay, by) + 2
        for o in self.obs[layer] + self.fixed:
            if o is skip:
                continue
            k = o[0]
            if k == "R":
                if o[1] == net or o[4] < lo_x or o[2] > hi_x or o[5] < lo_y or o[3] > hi_y:
                    continue
                d, need = _seg_rect(ax, ay, bx, by, *o[2:6]) - hw, self.clearance(net, o[1])
            elif k == "S":
                if o[1] == net or max(o[2], o[4]) < lo_x or min(o[2], o[4]) > hi_x \
                        or max(o[3], o[5]) < lo_y or min(o[3], o[5]) > hi_y:
                    continue
                d, need = _seg_seg(ax, ay, bx, by, *o[2:6]) - hw - o[6], self.clearance(net, o[1])
            elif k == "C":
                if o[1] == net:
                    continue
                d, need = _pt_seg(o[2], o[3], ax, ay, bx, by) - hw - o[4], self.clearance(net, o[1])
            elif k == "H":
                d, need = _pt_seg(o[1], o[2], ax, ay, bx, by) - hw - o[3], HOLE_CLEAR
            else:
                d, need = _seg_seg(ax, ay, bx, by, *o[1:5]) - hw, EDGE_CLEAR
            if d < need - 1e-6:
                bad.append((round(d, 4), need, o))
        return bad

    def check(self, quiet=False):
        """Exact clearance check of everything this run added.  Returns the problems."""
        out = []
        for it in self.added:
            if it[0] == "S":
                _, net, layer, ax, ay, bx, by, hw, ob = it
                for bad in self._gap(net, layer, ax, ay, bx, by, hw, skip=ob):
                    out.append((net, layer, (round(ax, 3), round(ay, 3), round(bx, 3), round(by, 3))) + bad)
            else:
                _, net, x, y, r = it
                for layer in "FB":
                    for bad in self._gap(net, layer, x, y, x, y, r):
                        out.append((net, "via", (round(x, 3), round(y, 3))) + bad)
        if not quiet:
            for o in out[:40]:
                print("  CLEARANCE", o)
            print("check: %d added items, %d clearance problems" % (len(self.added), len(out)))
        return out

    def track_ok(self, net, pts, layer="B", w=None):
        """Would line() with these arguments keep the rules?"""
        hw = (w or self.width(net)) / 2
        pts = [self.pad(*p) if isinstance(p[0], str) else p for p in pts]
        return not any(self._gap(net, layer, a[0], a[1], b[0], b[1], hw) for a, b in zip(pts, pts[1:]))

    def via_ok(self, net, x, y, size=None, room=0.0, pad_gap=0.15):
        """Would a via here keep the rules with `room` mm to spare, and stay
        pad_gap out of every pad (solder runs down a via in a pad, also one of
        its own net), off the cover posts and apart from its own net's vias?"""
        rad = (size or self.cls(net)[2]) / 2
        if any(self._gap(net, l, x, y, x, y, rad + room) for l in "FB"):
            return False
        for o in self.obs["B"] + self.obs["F"]:
            if o[1] != net:
                continue
            if o[0] == "R" and math.hypot(max(o[2] - x, x - o[4], 0), max(o[3] - y, y - o[5], 0)) < rad + pad_gap:
                return False
            if o[0] == "C" and math.hypot(o[2] - x, o[3] - y) < rad + o[4] + 0.2:
                return False
        return not any(math.hypot(k[0] - x, k[1] - y) < k[2] + rad if len(k) == 3 else
                       k[0] - rad < x < k[2] + rad and k[1] - rad < y < k[3] + rad for k in self.keepouts)

    def _kept_out(self, xs, ys, rad):
        """[rows, cols] bool: True where a via of this radius would stand in a keep-out."""
        X, Y = xs[None, :], ys[:, None]
        m = np.zeros((len(ys), len(xs)), dtype=bool)
        for k in self.keepouts:
            if len(k) == 3:
                m |= ((X - k[0]) ** 2 + (Y - k[1]) ** 2) < (k[2] + rad) ** 2
            else:
                m |= (X > k[0] - rad) & (X < k[2] + rad) & (Y > k[1] - rad) & (Y < k[3] + rad)
        return m

    # ------------------------------------------------------------ add copper ---
    def _track(self, net, layer, a, b, w):
        if abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9:
            return
        t = pcbnew.PCB_TRACK(self.b)
        t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
        t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(b[0]), pcbnew.FromMM(b[1])))
        t.SetWidth(pcbnew.FromMM(w))
        t.SetLayer(LAYER[layer])
        t.SetNet(self.b.FindNet(net))
        self.b.Add(t)
        ob = ("S", net, a[0], a[1], b[0], b[1], w / 2)
        self.obs[layer].append(ob)
        self.added.append(("S", net, layer, a[0], a[1], b[0], b[1], w / 2, ob))
        self._items.append(t)

    def line(self, net, pts, layer="B", w=None):
        """An explicit polyline.  Points may be (x, y) or ("U1", 9)-style pad names."""
        w = w or self.width(net)
        pts = [self.pad(*p) if isinstance(p[0], str) else p for p in pts]
        for a, b in zip(pts, pts[1:]):
            self._track(net, layer, a, b, w)
        return pts[-1]

    def via(self, net, x, y, size=None, drill=None):
        size, drill = size or self.cls(net)[2], drill or self.cls(net)[3]
        v = pcbnew.PCB_VIA(self.b)
        v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetWidth(pcbnew.F_Cu, pcbnew.FromMM(size))
        v.SetDrill(pcbnew.FromMM(drill))
        v.SetNet(self.b.FindNet(net))
        self.b.Add(v)
        self._note_via(net, x, y, size / 2)
        self.added.append(("C", net, x, y, size / 2))
        self._items.append(v)
        return (x, y)

    def take(self, net=None, since=0):
        """Take out again what this run added from added[since] on, for one net
        or for all.  Returns it as put() takes it, to draw it again."""
        out = []
        for k in range(len(self.added) - 1, since - 1, -1):
            it, item = self.added[k], self._items[k]
            if net is not None and it[1] != net:
                continue
            if it[0] == "S":
                self.obs[it[2]].remove(it[8])
                out.append(("S", it[1], it[2], (it[3], it[4]), (it[5], it[6]), 2 * it[7]))
            else:
                for l in "FB":
                    self.obs[l].remove(("C",) + it[1:5])
                out.append(("C", it[1], it[2], it[3], 2 * it[4], pcbnew.ToMM(item.GetDrill())))
            self.b.Remove(item)
            _REMOVED.append(item)             # KiCad crashes if Python frees a removed item
            del self.added[k], self._items[k]
        return out[::-1]

    def put(self, taken):
        """Draw again what take() took out."""
        for it in taken:
            if it[0] == "S":
                self._track(*it[1:])
            else:
                self.via(it[1], it[2], it[3], size=it[4], drill=it[5])

    def remove_tracks(self, pred):
        """Delete existing tracks and vias for which pred(track) is true."""
        gone = [t for t in self.b.GetTracks() if pred(t)]
        for t in gone:
            self.b.Remove(t)
        _REMOVED.extend(gone)                 # KiCad crashes if Python frees a removed item
        if gone:                              # rebuild the obstacle lists from the board
            keep = self.added, self._items
            self.__init__(self.b, self.keepouts, self.origin)
            self.added, self._items = keep
        return len(gone)

    # ---------------------------------------------------------- grid search ---
    def _raster(self, net, layer, hw, win, extra=0.0):
        """[rows, cols] bool over the window: True where this net's track centre may not be."""
        i0, j0, i1, j1 = win
        W, H = i1 - i0, j1 - j0
        blk = np.zeros((H, W), dtype=bool)
        xs = self.X0 + GRID * np.arange(i0, i1)
        ys = self.Y0 + GRID * np.arange(j0, j1)

        def span(lo, hi, o0, n):
            return max(0, int(math.floor((lo - o0) / GRID))), min(n, int(math.ceil((hi - o0) / GRID)) + 1)

        def disc(ax, ay, bx, by, R):
            a, b = span(min(ax, bx) - R, max(ax, bx) + R, xs[0], W)
            c, d = span(min(ay, by) - R, max(ay, by) + R, ys[0], H)
            if a >= b or c >= d:
                return
            X, Y = xs[a:b][None, :], ys[c:d][:, None]
            ux, uy = bx - ax, by - ay
            L = ux * ux + uy * uy
            t = 0.0 if L == 0 else np.clip(((X - ax) * ux + (Y - ay) * uy) / L, 0, 1)
            blk[c:d, a:b] |= ((X - ax - t * ux) ** 2 + (Y - ay - t * uy) ** 2) < R * R

        base = hw + MARGIN + extra
        for o in self.obs[layer] + self.fixed:
            k = o[0]
            if k == "R":
                if o[1] == net:
                    continue
                R = base + self.clearance(net, o[1])
                x0, y0, x1, y1 = o[2:6]
                a, b = span(x0 - R, x1 + R, xs[0], W)
                c, d = span(y0 - R, y1 + R, ys[0], H)
                if a >= b or c >= d:
                    continue
                X, Y = xs[a:b], ys[c:d]
                dx = np.maximum(np.maximum(x0 - X, X - x1), 0)
                dy = np.maximum(np.maximum(y0 - Y, Y - y1), 0)
                blk[c:d, a:b] |= (dx[None, :] ** 2 + dy[:, None] ** 2) < R * R
            elif k == "S":
                if o[1] != net:
                    disc(o[2], o[3], o[4], o[5], base + o[6] + self.clearance(net, o[1]))
            elif k == "C":
                if o[1] != net:
                    disc(o[2], o[3], o[2], o[3], base + o[4] + self.clearance(net, o[1]))
            elif k == "H":
                disc(o[1], o[2], o[1], o[2], base + o[3] + HOLE_CLEAR)
            else:
                disc(o[1], o[2], o[3], o[4], base + EDGE_CLEAR)
        return blk

    def _node(self, p):
        return int(round((p[0] - self.X0) / GRID)), int(round((p[1] - self.Y0) / GRID))

    def auto(self, net, a, b, layers="B", la="B", lb="B", w=None, pad=3.0, window=None,
             via_cost=200.0, turn=3.0, halo=0.15, halo_cost=0.5, cost=None, avoid=(), greedy=1.0,
             max_nodes=4000000):
        """Search a path from a to b and add it.  a and b are (x, y) or ("U1", 9) pads.

        layers   which layers the path may use, "B", "F" or "FB"
        la, lb   the layer each end is on; "FB" for a through-hole pad, which
                 the path may then leave or reach on either
        pad      mm of room around the two ends' bounding box (or give window=(x0, y0, x1, y1))
        via_cost, turn, halo_cost   in grid steps: the price of a via, of a 45-degree bend, and of
                 each step taken within `halo` mm of the minimum clearance
        cost     {"F": 1.0, "B": 1.0}: price of a step per layer
        avoid    extra rectangles to stay out of: (x0, y0, x1, y1, "F" | "B" | "FB")
        greedy   > 1 weights the distance still to go: much faster on long routes, and the
                 path may then be up to that factor longer than the best one
        """
        w = w or self.width(net)
        hw = w / 2
        ends = []
        for p in (a, b):
            if isinstance(p[0], str):
                x, y, pnet = self.pads[(p[0], str(p[1]))]
                assert pnet == net, "%s.%s is on %s, not %s" % (p[0], p[1], pnet, net)
                box = next(o for l in "BF" for o in self.obs[l]
                           if o[0] == "R" and o[1] == net and o[2] <= x <= o[4] and o[3] <= y <= o[5])
                on = "".join(l for l in "FB" if box in self.obs[l])
                ends.append(((x, y), box[2:6], on))
            else:
                ends.append(((float(p[0]), float(p[1])), None, ""))
        (pa, boxa, ona), (pb, boxb, onb) = ends
        if window is None:
            window = (min(pa[0], pb[0]) - pad, min(pa[1], pb[1]) - pad, max(pa[0], pb[0]) + pad, max(pa[1], pb[1]) + pad)
        i0, j0 = self._node((window[0], window[1]))
        i1, j1 = self._node((window[2], window[3]))
        i0, j0, i1, j1 = max(i0, 0), max(j0, 0), min(i1, self.NX - 1) + 1, min(j1, self.NY - 1) + 1
        win = (i0, j0, i1, j1)
        W, H = i1 - i0, j1 - j0
        xs = self.X0 + GRID * np.arange(i0, i1)
        ys = self.Y0 + GRID * np.arange(j0, j1)
        vr = self.cls(net)[2] / 2
        blk, near = {}, {}
        for li, l in enumerate("FB"):
            if l not in layers:
                continue
            m = self._raster(net, l, hw, win)
            n = self._raster(net, l, hw, win, extra=halo) if halo_cost else m
            for r in avoid:
                if l in r[4]:
                    ai, aj = self._node((r[0], r[1]))
                    bi, bj = self._node((r[2], r[3]))
                    m[max(aj - j0, 0):max(bj - j0 + 1, 0), max(ai - i0, 0):max(bi - i0 + 1, 0)] = True
            for p, box, on in ends:                          # an end may sit inside its own pad
                if box is not None and l in on:
                    s = min(hw, (box[2] - box[0]) / 2 - 0.01, (box[3] - box[1]) / 2 - 0.01)
                    cx = (xs >= box[0] + s - 1e-9) & (xs <= box[2] - s + 1e-9)
                    cy = (ys >= box[1] + s - 1e-9) & (ys <= box[3] - s + 1e-9)
                    m[np.ix_(cy, cx)] = False
                ci, cj = self._node(p)
                if 0 <= cj - j0 < H and 0 <= ci - i0 < W:
                    m[cj - j0, ci - i0] = False
            blk[li], near[li] = m.tobytes(), n.tobytes()
        vblk = None
        if len(layers) == 2:
            v = self._raster(net, "F", vr, win) | self._raster(net, "B", vr, win)
            for o in self.obs["B"] + self.obs["F"]:        # no via in a pad, even one of this net
                if o[0] == "R" and o[1] == net:
                    R = vr + 0.05
                    X, Y = xs[None, :], ys[:, None]
                    v |= (np.maximum(np.maximum(o[2] - X, X - o[4]), 0) ** 2
                          + np.maximum(np.maximum(o[3] - Y, Y - o[5]), 0) ** 2) < R * R
            v |= self._kept_out(xs, ys, vr)
            vblk = v.tobytes()
        cost = cost or {}
        lc = [cost.get("F", 1.0), cost.get("B", 1.0)]
        si, sj = self._node(pa)
        gi, gj = self._node(pb)
        si, sj, gi, gj = si - i0, sj - j0, gi - i0, gj - j0
        assert 0 <= si < W and 0 <= sj < H and 0 <= gi < W and 0 <= gj < H
        ls = ["FB".index(c) for c in la if "FB".index(c) in blk]
        lg = {"FB".index(c) for c in lb if "FB".index(c) in blk}
        assert ls and lg, "an end is on a layer the path may not use"
        hmin = min(lc[li] for li in blk)
        goal = gj * W + gi

        def h(i, j):
            dx, dy = abs(i - gi), abs(j - gj)
            return (max(dx, dy) + 0.4142 * min(dx, dy)) * hmin * greedy

        starts = [((sj * W + si) * 2 + l) * 9 + 8 for l in ls]
        best = {start: 0.0 for start in starts}
        parent = {start: None for start in starts}
        heap = [(h(si, sj), n, start) for n, start in enumerate(starts)]
        count = len(starts)
        found = None
        while heap:
            f, _, key = heapq.heappop(heap)
            gc = best[key]
            if f > gc + 1e-9 + h(*divmod(key // 18, W)[::-1]):
                continue
            d = key % 9
            l = (key // 9) % 2
            cell = key // 18
            if cell == goal and l in lg:
                found = key
                break
            j, i = divmod(cell, W)
            bl, nr, c = blk[l], near[l], lc[l]
            for nd in (range(8) if d == 8 else ((d - 1) % 8, d, (d + 1) % 8)):
                di, dj = DIRS[nd]
                ni, nj = i + di, j + dj
                if not (0 <= ni < W and 0 <= nj < H):
                    continue
                nc = nj * W + ni
                if bl[nc]:
                    continue
                ng = gc + (1.4142 if nd & 1 else 1.0) * c + (turn if d != 8 and nd != d else 0.0) \
                    + (halo_cost if nr[nc] else 0.0)
                nkey = (nc * 2 + l) * 9 + nd
                if ng < best.get(nkey, 1e18):
                    best[nkey] = ng
                    parent[nkey] = key
                    count += 1
                    heapq.heappush(heap, (ng + h(ni, nj), count, nkey))
            if vblk is not None and (1 - l) in blk and not vblk[cell] and not blk[1 - l][cell]:
                nkey = (cell * 2 + (1 - l)) * 9 + 8
                ng = gc + via_cost
                if ng < best.get(nkey, 1e18):
                    best[nkey] = ng
                    parent[nkey] = key
                    count += 1
                    heapq.heappush(heap, (ng + h(i, j), count, nkey))
            if count > max_nodes:
                break
        if found is None:
            raise RuntimeError("no route for %s from %s to %s (%d nodes)" % (net, a, b, count))
        cells = []
        key = found
        while key is not None:
            j, i = divmod(key // 18, W)
            cells.append((float(xs[i]), float(ys[j]), (key // 9) % 2))
            key = parent[key]
        cells.reverse()
        runs = [[cells[0]]]
        for cprev, cnext in zip(cells, cells[1:]):
            if cnext[2] != cprev[2]:
                runs.append([cnext])
            elif (cnext[0], cnext[1]) != (cprev[0], cprev[1]):
                runs[-1].append(cnext)
        for n, run in enumerate(runs):
            l = "FB"[run[0][2]]
            pts = [(x, y) for x, y, _ in run]
            if n == 0:
                pts.insert(0, pa)
            if n == len(runs) - 1:
                pts.append(pb)
            keep = [pts[0]]
            for p, q in zip(pts[1:], pts[2:]):             # drop points on a straight run
                o = keep[-1]
                if abs((p[0] - o[0]) * (q[1] - p[1]) - (p[1] - o[1]) * (q[0] - p[0])) > 1e-7:
                    keep.append(p)
            keep.append(pts[-1])
            for p, q in zip(keep, keep[1:]):
                self._track(net, l, p, q, w)
            if n:
                self.via(net, run[0][0], run[0][1])
        return count

    # ------------------------------------------------ the pieces of one net ---
    def route(self, net, src, dst, layers="B", w=None, pad=3.0, window=None, via_cost=200.0, turn=3.0,
              halo=0.15, halo_cost=0.5, cost=None, avoid=(), greedy=1.0, max_nodes=4000000):
        """Search a path from any place in src to any place in dst and add it.

        A place is (x, y, on, box): a point of this net's copper, the layers it
        is on there ("F", "B" or "FB") and, for a pad, its box (else None).  A
        track that fits in a pad may begin anywhere in it, and is drawn on to
        the pad's centre.  A wider one may only begin on the pad's middle line,
        and only where the rules allow it there.
        window   default: the box round src, grown by the distance to the
                 nearest place of dst and by `pad`
        The other arguments are auto()'s.  Returns the number of nodes searched."""
        w = w or self.width(net)
        hw = w / 2
        if window is None:
            x0, y0 = min(e[0] for e in src), min(e[1] for e in src)
            x1, y1 = max(e[0] for e in src), max(e[1] for e in src)
            far = pad + min(math.hypot(max(x0 - e[0], e[0] - x1, 0), max(y0 - e[1], e[1] - y1, 0)) for e in dst)
            window = (x0 - far, y0 - far, x1 + far, y1 + far)
        i0, j0 = self._node((window[0], window[1]))
        i1, j1 = self._node((window[2], window[3]))
        i0, j0, i1, j1 = max(i0, 0), max(j0, 0), min(i1, self.NX - 1) + 1, min(j1, self.NY - 1) + 1
        win = (i0, j0, i1, j1)
        W, H = i1 - i0, j1 - j0
        xs = self.X0 + GRID * np.arange(i0, i1)
        ys = self.Y0 + GRID * np.arange(j0, j1)
        xl, yl = xs.tolist(), ys.tolist()
        vr = self.cls(net)[2] / 2
        blk, near = {}, {}
        for li, l in enumerate("FB"):
            if l not in layers:
                continue
            blk[li] = self._raster(net, l, hw, win)
            near[li] = self._raster(net, l, hw, win, extra=halo) if halo_cost else blk[li]
            for r in avoid:
                if l in r[4]:
                    ai, aj = self._node((r[0], r[1]))
                    bi, bj = self._node((r[2], r[3]))
                    blk[li][max(aj - j0, 0):max(bj - j0 + 1, 0), max(ai - i0, 0):max(bi - i0 + 1, 0)] = True

        def span(lo, hi, o0, n):
            return max(0, int(math.floor((lo - o0) / GRID))), min(n, int(math.ceil((hi - o0) / GRID)) + 1)

        def place(ends):
            """{node and layer: the point the track is drawn on to} for a list of places."""
            out = {}
            for x, y, on, box in ends:
                on = [li for li in blk if "FB"[li] in on]
                if not on:
                    continue
                i, j = self._node((x, y))
                i, j = i - i0, j - j0
                if box is None:
                    if 0 <= i < W and 0 <= j < H:
                        for li in on:
                            if not blk[li][j, i]:
                                out.setdefault((j * W + i) * 2 + li, (x, y))
                    continue
                x0, y0, x1, y1 = box
                rad = min(x1 - x0, y1 - y0) / 2
                if x1 - x0 >= y1 - y0:                      # the pad's middle line
                    mid = (x0 + rad, (y0 + y1) / 2, x1 - rad, (y0 + y1) / 2)
                else:
                    mid = ((x0 + x1) / 2, y0 + rad, (x0 + x1) / 2, y1 - rad)
                fits = hw <= rad + 1e-9
                reach = rad - hw if fits else GRID / 2
                a, b = span(x0, x1, xl[0], W)
                c, d = span(y0, y1, yl[0], H)
                got = [(ii, jj) for jj in range(c, d) for ii in range(a, b)
                       if _pt_seg(xl[ii], yl[jj], *mid) <= reach + 1e-6]
                if fits and not got and 0 <= i < W and 0 <= j < H:
                    got = [(i, j)]                          # a pad between the nodes: the nearest one
                for ii, jj in got:
                    for li in on:
                        if fits:
                            blk[li][jj, ii] = False
                        elif blk[li][jj, ii] or self._gap(net, "FB"[li], xl[ii], yl[jj], x, y, hw):
                            continue
                        out.setdefault((jj * W + ii) * 2 + li, (x, y))
            return out

        goals, starts = place(dst), place(src)
        if not starts or not goals:
            raise RuntimeError("no route for %s: nowhere to %s" % (net, "end" if starts else "begin"))
        vblk = None
        if len(blk) == 2:
            v = self._raster(net, "F", vr, win) | self._raster(net, "B", vr, win)
            for o in self.obs["B"] + self.obs["F"]:        # no via in a pad of this net, nor close to another of its vias
                if o[1] != net or o[0] == "S":
                    continue
                if o[0] == "R":
                    x0, y0, x1, y1, R = o[2], o[3], o[4], o[5], vr + 0.05
                else:
                    x0, y0, x1, y1, R = o[2], o[3], o[2], o[3], vr + o[4] + 0.2
                a, b = span(x0 - R, x1 + R, xl[0], W)
                c, d = span(y0 - R, y1 + R, yl[0], H)
                if a < b and c < d:
                    dx = np.maximum(np.maximum(x0 - xs[a:b], xs[a:b] - x1), 0)
                    dy = np.maximum(np.maximum(y0 - ys[c:d], ys[c:d] - y1), 0)
                    v[c:d, a:b] |= (dx[None, :] ** 2 + dy[:, None] ** 2) < R * R
            v |= self._kept_out(xs, ys, vr)
            vblk = v.tobytes()
        near = {li: n.tobytes() for li, n in near.items()}
        blk = {li: m.tobytes() for li, m in blk.items()}
        cost = cost or {}
        lc = [cost.get("F", 1.0), cost.get("B", 1.0)]
        hmin = min(lc[li] for li in blk)

        # the distance still to go: to the nearest of a few of the goals, less what the others may be nearer
        def steps(ai, aj, bi, bj):
            dx, dy = np.abs(ai - bi), np.abs(aj - bj)
            return np.maximum(dx, dy) + 0.4142 * np.minimum(dx, dy)

        gi = np.array([(k // 2) % W for k in goals])
        gj = np.array([(k // 2) // W for k in goals])
        few = [0]
        off = steps(gi, gj, gi[0], gj[0])
        while len(few) < 32 and off.max() > 3:
            few.append(int(off.argmax()))
            off = np.minimum(off, steps(gi, gj, gi[few[-1]], gj[few[-1]]))
        I, J = np.arange(W)[None, :], np.arange(H)[:, None]
        togo = np.full((H, W), np.inf)
        for k in few:
            togo = np.minimum(togo, steps(I, J, gi[k], gj[k]))
        togo = (np.maximum(togo - off.max(), 0) * (hmin * greedy)).ravel().tolist()

        best, parent, heap = {}, {}, []
        count = 0
        for k in starts:
            best[k * 9 + 8] = 0.0
            parent[k * 9 + 8] = None
            heap.append((togo[k // 2], count, k * 9 + 8))
            count += 1
        heapq.heapify(heap)
        found = None
        while heap:
            f, _, key = heapq.heappop(heap)
            gc = best[key]
            cell = key // 18
            if f > gc + 1e-9 + togo[cell]:
                continue
            if key // 9 in goals:
                found = key
                break
            d = key % 9
            l = (key // 9) % 2
            j, i = divmod(cell, W)
            bl, nr, c = blk[l], near[l], lc[l]
            for nd in (range(8) if d == 8 else ((d - 1) % 8, d, (d + 1) % 8)):
                di, dj = DIRS[nd]
                ni, nj = i + di, j + dj
                if not (0 <= ni < W and 0 <= nj < H):
                    continue
                nc = nj * W + ni
                if bl[nc]:
                    continue
                ng = gc + (1.4142 if nd & 1 else 1.0) * c + (turn if d != 8 and nd != d else 0.0) \
                    + (halo_cost if nr[nc] else 0.0)
                nkey = (nc * 2 + l) * 9 + nd
                if ng < best.get(nkey, 1e18):
                    best[nkey] = ng
                    parent[nkey] = key
                    count += 1
                    heapq.heappush(heap, (ng + togo[nc], count, nkey))
            if vblk is not None and not vblk[cell] and not blk[1 - l][cell]:
                nkey = (cell * 2 + (1 - l)) * 9 + 8
                ng = gc + via_cost
                if ng < best.get(nkey, 1e18):
                    best[nkey] = ng
                    parent[nkey] = key
                    count += 1
                    heapq.heappush(heap, (ng + togo[cell], count, nkey))
            if count > max_nodes:
                break
        if found is None:
            raise RuntimeError("no route for %s (%d nodes)" % (net, count))
        cells = []
        key = found
        while key is not None:
            j, i = divmod(key // 18, W)
            cells.append((round(xl[i], 6), round(yl[j], 6), (key // 9) % 2))
            first, key = key, parent[key]
        cells.reverse()
        runs = [[cells[0]]]
        for cprev, cnext in zip(cells, cells[1:]):
            if cnext[2] != cprev[2]:
                runs.append([cnext])
            elif (cnext[0], cnext[1]) != (cprev[0], cprev[1]):
                runs[-1].append(cnext)
        for n, run in enumerate(runs):
            pts = [(x, y) for x, y, _ in run]
            if n == 0:
                pts[:0] = _lead(starts[first // 9], pts[0])
            if n == len(runs) - 1:
                pts += _lead(goals[found // 9], pts[-1])[::-1]
            keep = [pts[0]]
            for p, q in zip(pts[1:], pts[2:]):             # drop points on a straight run
                o = keep[-1]
                if abs((p[0] - o[0]) * (q[1] - p[1]) - (p[1] - o[1]) * (q[0] - p[0])) > 1e-7:
                    keep.append(p)
            keep.append(pts[-1])
            for p, q in zip(keep, keep[1:]):
                self._track(net, "FB"[run[0][2]], p, q, w)
            if n:
                self.via(net, run[0][0], run[0][1])
        return count

    def pieces(self, net):
        """The net's copper as it hangs together now: a list of (pads, places),
        one per piece.  pads is the set of (ref, number) in it; places lists
        where a track may join it, as route() takes them: its pads, its vias
        and a point every GRID along its tracks."""
        items = [("P", on, x, y, box, (ref, num)) for ref, num, n, x, y, box, on in self.terminals if n == net]
        items += [("S", l) + o[2:7] for l in "FB" for o in self.obs[l] if o[0] == "S" and o[1] == net]
        items += [("C", "FB") + o[2:5] for o in self.obs["F"] if o[0] == "C" and o[1] == net]
        up = list(range(len(items)))

        def root(i):
            while up[i] != i:
                up[i] = up[up[i]]
                i = up[i]
            return i

        def touch(a, b):
            if a[0] > b[0]:
                a, b = b, a
            kinds = a[0] + b[0]
            if not set(a[1]) & set(b[1]):
                return False
            if kinds == "PP":                               # two pads that lie on each other
                (x0, y0, x1, y1), (u0, v0, u1, v1) = a[4], b[4]
                return min(x1, u1) - max(x0, u0) > 1e-6 and min(y1, v1) - max(y0, v0) > 1e-6
            if kinds == "PS":
                return _seg_rect(*b[2:6], *a[4]) <= 1e-6
            if kinds == "CP":
                x0, y0, x1, y1 = b[4]
                return x0 - 1e-6 <= a[2] <= x1 + 1e-6 and y0 - 1e-6 <= a[3] <= y1 + 1e-6
            if kinds == "SS":                               # the middle lines cross, or one's end lies in the other
                return _seg_seg(*a[2:6], *b[2:6]) == 0.0 \
                    or min(_pt_seg(a[2], a[3], *b[2:6]), _pt_seg(a[4], a[5], *b[2:6])) <= b[6] + 1e-6 \
                    or min(_pt_seg(b[2], b[3], *a[2:6]), _pt_seg(b[4], b[5], *a[2:6])) <= a[6] + 1e-6
            if kinds == "CS":
                return _pt_seg(a[2], a[3], *b[2:6]) <= b[6] + 1e-6 \
                    or min(math.hypot(b[2] - a[2], b[3] - a[3]), math.hypot(b[4] - a[2], b[5] - a[3])) <= a[4] + 1e-6
            return math.hypot(a[2] - b[2], a[3] - b[3]) <= min(a[4], b[4])

        def box(it):
            if it[0] == "P":
                return it[4]
            if it[0] == "S":
                return (min(it[2], it[4]) - it[6], min(it[3], it[5]) - it[6],
                        max(it[2], it[4]) + it[6], max(it[3], it[5]) + it[6])
            return (it[2] - it[4], it[3] - it[4], it[2] + it[4], it[3] + it[4])

        boxes = [box(it) for it in items]
        for i in range(len(items)):
            x0, y0, x1, y1 = boxes[i]
            for j in range(i + 1, len(items)):
                u0, v0, u1, v1 = boxes[j]
                if u0 > x1 or u1 < x0 or v0 > y1 or v1 < y0 or root(i) == root(j):
                    continue
                if touch(items[i], items[j]):
                    up[root(i)] = root(j)
        out = {}
        for i, it in enumerate(items):
            pads, places = out.setdefault(root(i), (set(), []))
            if it[0] == "P":
                pads.add(it[5])
                places.append((it[2], it[3], it[1], it[4]))
            elif it[0] == "C":
                places.append((it[2], it[3], "FB", None))
            else:
                n = max(1, int(round(math.hypot(it[4] - it[2], it[5] - it[3]) / GRID)))
                places += [(it[2] + (it[4] - it[2]) * k / n, it[3] + (it[5] - it[3]) * k / n, it[1], None)
                           for k in range(n + 1)]
        return list(out.values())

    def connect(self, net, a, b=None, **kw):
        """Join the piece of the net that pad a is in to the piece pad b is in;
        without b, to whichever other piece the search reaches first.  Nothing
        if they already hang together.  The other arguments are route()'s."""
        ps = self.pieces(net)

        def of(p):
            return next(i for i, (pads, _) in enumerate(ps) if (p[0], str(p[1])) in pads)

        ia = of(a)
        if b is not None:
            if of(b) == ia:
                return 0
            dst = ps[of(b)][1]
        else:
            dst = [e for i, (_, places) in enumerate(ps) if i != ia for e in places]
            if not dst:
                return 0
        return self.route(net, ps[ia][1], dst, **kw)

    def join(self, net, pads=(3.0, 10.0, 200.0), **kw):
        """Route the smallest piece of the net to the rest of it, again and
        again, until the net is one piece.  Each search is tried in a small
        window first, then in larger ones.  Returns the pieces that no path
        was found for, each as its sorted pads."""
        lost = []

        def name(piece):
            return sorted(piece[0]) or [tuple(round(v, 3) for v in piece[1][0][:2])]

        while True:
            ps = self.pieces(net)
            todo = [p for p in ps if name(p) not in lost]
            if len(ps) < 2 or len(todo) < 2 and lost:
                return lost
            src = min(todo, key=lambda p: (len(p[1]), name(p)))
            dst = [e for p in ps if p is not src for e in p[1]]
            for pad in pads:
                try:
                    self.route(net, src[1], dst, pad=pad, **kw)
                    break
                except RuntimeError:
                    continue
            else:
                lost.append(name(src))


# ------------------------------------------------------------ ground pours ---
mm = pcbnew.ToMM
COMPASS = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0),
           "NE": (1, -1), "NW": (-1, -1), "SE": (1, 1), "SW": (-1, 1)}
_RIM = [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in range(0, 360, 30)]


def parts(ps):
    """The outlines of a polygon set, each with its holes and its box: [(set, x0, y0, x1, y1)]."""
    out = []
    for i in range(ps.OutlineCount()):
        one = pcbnew.SHAPE_POLY_SET()
        one.AddOutline(ps.Outline(i))
        for h in range(ps.HoleCount(i)):
            one.AddHole(ps.Hole(i, h))
        bb = one.BBox()
        out.append((one, mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())))
    return out


def pours(b):
    """The copper of the two ground pours, island by island, as it would be if
    none were dropped for want of a connection: {"F": [...], "B": [...]}."""
    zones = [z for z in b.Zones() if not z.GetIsRuleArea()]
    modes = [z.GetIslandRemovalMode() for z in zones]
    for z in zones:
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_NEVER)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    out = {l: [isl for z in zones if z.IsOnLayer(layer) for isl in parts(z.GetFilledPolysList(layer))]
           for l, layer in LAYER.items()}
    for z, m in zip(zones, modes):
        z.SetIslandRemovalMode(m)
    return out


def shrunk(poly, by, back=0.0):
    """A pour's copper drawn in by `by` mm all round, then let out again by `back`."""
    p = pcbnew.SHAPE_POLY_SET(poly)
    p.Unfracture()                              # KiCad keeps a fill's holes as slits; drawn in, a slit would cut
    p.Deflate(pcbnew.FromMM(by), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
    if back:
        p.Inflate(pcbnew.FromMM(back), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
    return p


def inside(poly, x, y):
    return poly.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))


def bends(a, b):
    """The ways from a to b with one bend at most, all at 45 degrees."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    k = min(abs(dx), abs(dy))
    if k < 1e-6 or abs(abs(dx) - abs(dy)) < 1e-6:
        return [[a, b]]
    ux, uy = math.copysign(k, dx), math.copysign(k, dy)
    return [[a, (round(b[0] - ux, 3), round(b[1] - uy, 3)), b], [a, (round(a[0] + ux, 3), round(a[1] + uy, 3)), b]]


class Ground:
    """Ground is the pour on both sides, and the front's is the plane.  This
    gives a ground pad a via into the front plane as near as one fits.

        g = Ground(r)                 fills the pours as they are now
        g = Ground(r, least=20.0)     where long tracks cut the front's pour up: every
                                      island of 20 mm2 and more counts as the plane
        g.pad("C7", 2)                a via beside the pad, straight out of the part if it can be
        g.stitch((x, y), "N S")       the same at a point, in the directions given
        g.join("C7", 2)               a track to the nearest pad or via that has one

    done, missed and tied keep the account: the pads dealt with, those no via
    fitted at, and the points that are tied to the plane."""

    ANY = ("N S E W", "NE NW SE SW")
    BIG = dict(size=0.8, drill=0.4, w=0.5)

    def __init__(self, r, net="GND", least=None):
        self.r, self.net = r, net
        self.pour = pours(r.b)
        area = [isl[0].Area() / 1e12 for isl in self.pour["F"]]
        self.front = {i for i, a in enumerate(area) if a == max(area) or least is not None and a >= least}
        self.done, self.missed, self.tied = set(), [], []

    def pour_at(self, l, x, y, rad):
        """The island of a layer's pour that a disc lies in, whole; None if there is none."""
        pts = [(x, y)] + [(x + rad * cx, y + rad * cy) for cx, cy in _RIM]
        for i, (isl, x0, y0, x1, y1) in enumerate(self.pour[l]):
            if x0 <= x <= x1 and y0 <= y <= y1 and all(inside(isl, px, py) for px, py in pts):
                return i
        return None

    def stitch(self, at, *groups, far=2.0, w=0.3, size=0.6, drill=0.3):
        """A via into the front plane as near a pad as one fits, and a track to it.
        at: the pad, ("C7", 2), or a point in it.  groups: compass points, "N S" then
        "NE SE" ...: the nearest fit of the first group that has one, straight out of
        the pad.  If there is none: the nearest that a track with one bend reaches."""
        r, G = self.r, self.net
        x, y = r.pad(*at) if isinstance(at[0], str) else at
        n = int(round(far / 0.05))
        straight = [[(x, y), (round(x + ux * d, 3), round(y + uy * d, 3))] for group in groups
                    for d in (0.05 * i for i in range(8, n + 1)) for ux, uy in (COMPASS[k] for k in group.split())]
        n = int(round((far + 0.5) / 0.1))
        ends = sorted((max(abs(i), abs(j)) + 0.414 * min(abs(i), abs(j)), i, j) for i in range(-n, n + 1)
                      for j in range(-n, n + 1) if i and j and abs(i) != abs(j))
        bent = [path for _, i, j in ends for path in bends((x, y), (round(x + 0.1 * i, 3), round(y + 0.1 * j, 3)))]
        for paths in straight, bent:
            for room in 0.05, 0.0:              # rather not right at the limit
                for path in paths:
                    vx, vy = path[-1]
                    if r.via_ok(G, vx, vy, size, room) and self.pour_at("F", vx, vy, size / 2 + room) in self.front \
                            and r.track_ok(G, path, w=w):
                        r.line(G, path, w=w)
                        r.via(G, vx, vy, size=size, drill=drill)
                        return vx, vy
        return None

    def join(self, ref, pin, far=4.0, w=0.3):
        """A track from a pad to the nearest pad or via that is tied to the plane."""
        r = self.r
        x, y = r.pad(ref, pin)
        for d, tx, ty in sorted((math.hypot(tx - x, ty - y), tx, ty) for tx, ty in self.tied):
            for path in bends((x, y), (tx, ty)) if d <= far else ():
                if r.track_ok(self.net, path, w=w):
                    r.line(self.net, path, w=w)
                    return tx, ty
        return None

    def outward(self, ref, pin):
        """Compass points for one pad of a two-pad part: straight out of the part, sideways, out at an angle."""
        r = self.r
        (x, y), (ox, oy) = r.pad(ref, pin), r.pad(ref, next(n for f, n in r.pads if f == ref and n != str(pin)))
        if abs(x - ox) > abs(y - oy):
            o = "E" if x > ox else "W"
            return o, "N S", "N%s S%s" % (o, o)
        o = "S" if y > oy else "N"
        return o, "E W", "%sE %sW" % (o, o)

    def pad(self, ref, pin, *groups, **kw):
        """stitch() at a pad, and the account kept."""
        self.done.add((ref, str(pin)))
        at = self.stitch((ref, pin), *(groups or self.outward(ref, pin)), **kw)
        if at is None:
            self.missed.append((ref, pin))
        else:
            self.tied.extend([at, self.r.pad(ref, pin)])
        return at


TIE_NECK = 0.5              # where a pour is narrower than this, it counts as two cells
TIE_REACH = 8.0             # no place of a cell is farther than this from a via, if one fits there
TIE_AREA = 3.0              # mm2: smaller cells are left alone
TIE_PITCH = 0.25


def ties(r, net="GND"):
    """The two pours tied to each other.  The tracks cut each pour into cells
    (mostly the back's); after the ground vias some have none, and long ones
    have one at one end.  Here every cell gets a via to the other side's pour
    wherever it is more than TIE_REACH from the next, if one fits."""
    pour = pours(r.b)
    cell = {l: [c for isl in pour[l] for c in parts(shrunk(isl[0], TIE_NECK / 2, TIE_NECK / 2))] for l in "FB"}
    area = {(l, i): c[0].Area() / 1e12 for l in "FB" for i, c in enumerate(cell[l])}

    def cells_at(l, x, y, rad=0.0):
        """The cells of a layer that a disc touches."""
        pts = [(x, y)] + [(x + rad * cx, y + rad * cy) for cx, cy in _RIM if rad]
        return [i for i, (c, x0, y0, x1, y1) in enumerate(cell[l])
                if x0 - rad <= x <= x1 + rad and y0 - rad <= y <= y1 + rad and any(inside(c, *q) for q in pts)]

    # the places for a via: where both pours are, with room to spare
    core = {l: [shrunk(isl[0], 0.3 + 0.15) for isl in pour[l]] for l in "FB"}
    spots, of = [], {}                          # (x, y, front cell, back cell); cell -> its spots
    for fc in core["F"]:
        for bc in core["B"]:
            both = pcbnew.SHAPE_POLY_SET(bc)
            both.BooleanIntersection(fc)
            for one, x0, y0, x1, y1 in parts(both):
                n0, n1 = math.ceil(x0 / TIE_PITCH), math.floor(x1 / TIE_PITCH)
                m0, m1 = math.ceil(y0 / TIE_PITCH), math.floor(y1 / TIE_PITCH)
                pts = [(n * TIE_PITCH, m * TIE_PITCH) for n in range(n0, n1 + 1) for m in range(m0, m1 + 1)
                       if inside(one, n * TIE_PITCH, m * TIE_PITCH)]
                if not pts:
                    continue
                f, k = cells_at("F", *pts[0]), cells_at("B", *pts[0])
                assert len(f) == 1 and len(k) == 1, "a place in %d front and %d back cells" % (len(f), len(k))
                for x, y in pts:
                    of.setdefault(("F", f[0]), []).append(len(spots))
                    of.setdefault(("B", k[0]), []).append(len(spots))
                    spots.append((x, y, f[0], k[0]))
    has = {}                                    # cell -> the ground vias in it
    for o in r.obs["B"]:
        if o[0] == "C" and o[1] == net:
            for l in "FB":
                for i in cells_at(l, o[2], o[3], o[4]):
                    has.setdefault((l, i), []).append((o[2], o[3]))

    n = 0
    for key in sorted(of, key=lambda k: (k[0] != "B", -area[k])):       # the back's cells first, big ones first
        if area[key] < TIE_AREA:
            continue
        mine = of[key]
        xy = np.array([spots[k][:2] for k in mine])
        near = np.full(len(mine), np.inf)       # each spot's distance to the nearest via of this cell
        for vx, vy in has.get(key, []):
            near = np.minimum(near, np.hypot(xy[:, 0] - vx, xy[:, 1] - vy))
        while not np.isnan(near).all():
            if np.isinf(near[~np.isnan(near)]).all():       # no via yet: begin in the middle
                mid = xy[~np.isnan(near)].mean(0)
                k = int(np.where(np.isnan(near), np.inf, np.hypot(xy[:, 0] - mid[0], xy[:, 1] - mid[1])).argmin())
            else:
                k = int(np.nanargmax(near))
                if near[k] < TIE_REACH:
                    break
            x, y, f, bk = spots[mine[k]]
            if r.via_ok(net, x, y, 0.6, room=0.1):
                r.via(net, x, y, size=0.6, drill=0.3)
                n += 1
                for other in ("F", f), ("B", bk):
                    has.setdefault(other, []).append((x, y))
                near = np.minimum(near, np.hypot(xy[:, 0] - x, xy[:, 1] - y))
            else:
                near[k] = np.nan                # no via fits here after all
    bare = sum(1 for k, a in area.items() if a >= TIE_AREA and k not in has)
    print("ties: %d vias between the pours; %d cells of %d mm2 and more have none" % (n, bare, TIE_AREA))
