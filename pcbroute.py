"""A small router for scripts that change a routed board in place.

It knows every pad, track, via, hole and board edge, and offers three ways
to add copper:

    r.line(net, [(x, y), ...])        an explicit polyline, checked exactly
    r.via(net, x, y)                  a via; track_ok() and via_ok() tell beforehand
                                      whether a track or a via would fit
    r.auto(net, a, b)                 a grid search (A*) between two points,
                                      over one or both layers, vias allowed

All keep the board's own clearances: the larger of the two nets' net-class
clearances, 0.25 mm to holes and to the board edge.  KiCad's DRC stays the
judge (run it afterwards); this only makes a wrong track rare and its cause
immediate.  Coordinates are millimetres in KiCad's frame (x right, y down).
Layers are "B" (B.Cu, where every part is) and "F" (F.Cu).
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


class Router:
    def __init__(self, board, keepouts=()):
        """keepouts: circles (x, y, r) where auto() places no via (the cover posts)."""
        self.b = board
        self.keepouts = list(keepouts)
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
        self.added = []                       # what this run added, for check()
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
                assert p.GetDrillSize().x == 0, "through-hole pad: not handled"
                for l in "FB":
                    if p.IsOnLayer(LAYER[l]):
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
        return all(math.hypot(kx - x, ky - y) >= kr + rad for kx, ky, kr in self.keepouts)

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
        return (x, y)

    def remove_tracks(self, pred):
        """Delete existing tracks and vias for which pred(track) is true."""
        gone = [t for t in self.b.GetTracks() if pred(t)]
        for t in gone:
            self.b.Remove(t)
        _REMOVED.extend(gone)                 # KiCad crashes if Python frees a removed item
        if gone:                              # rebuild the obstacle lists from the board
            keep = self.added
            self.__init__(self.b, self.keepouts)
            self.added = keep
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
        la, lb   the layer each end is on
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
                box = next(o for o in self.obs["B"] if o[0] == "R" and o[2] <= x <= o[4] and o[3] <= y <= o[5])
                ends.append(((x, y), box[2:6]))
            else:
                ends.append(((float(p[0]), float(p[1])), None))
        (pa, boxa), (pb, boxb) = ends
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
            for p, box in ((pa, boxa), (pb, boxb)):          # an end may sit inside its own pad
                if box is not None and l == "B":
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
            for o in self.obs["B"]:                        # no via in a pad, even one of this net
                if o[0] == "R" and o[1] == net:
                    R = vr + 0.05
                    X, Y = xs[None, :], ys[:, None]
                    v |= (np.maximum(np.maximum(o[2] - X, X - o[4]), 0) ** 2
                          + np.maximum(np.maximum(o[3] - Y, Y - o[5]), 0) ** 2) < R * R
            for (kx, ky, kr) in self.keepouts:
                v |= ((xs[None, :] - kx) ** 2 + (ys[:, None] - ky) ** 2) < (kr + vr) ** 2
            vblk = v.tobytes()
        cost = cost or {}
        lc = [cost.get("F", 1.0), cost.get("B", 1.0)]
        si, sj = self._node(pa)
        gi, gj = self._node(pb)
        si, sj, gi, gj = si - i0, sj - j0, gi - i0, gj - j0
        assert 0 <= si < W and 0 <= sj < H and 0 <= gi < W and 0 <= gj < H
        ls, lg = "FB".index(la), "FB".index(lb)
        assert ls in blk and lg in blk, "an end is on a layer the path may not use"
        hmin = min(lc[li] for li in blk)
        goal = gj * W + gi

        def h(i, j):
            dx, dy = abs(i - gi), abs(j - gj)
            return (max(dx, dy) + 0.4142 * min(dx, dy)) * hmin * greedy

        start = ((sj * W + si) * 2 + ls) * 9 + 8
        best = {start: 0.0}
        parent = {start: None}
        heap = [(h(si, sj), 0, start)]
        count = 0
        found = None
        while heap:
            f, _, key = heapq.heappop(heap)
            gc = best[key]
            if f > gc + 1e-9 + h(*divmod(key // 18, W)[::-1]):
                continue
            d = key % 9
            l = (key // 9) % 2
            cell = key // 18
            if cell == goal and l == lg:
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
