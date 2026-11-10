#!/usr/bin/env python3
"""Draw tile_circuit.py as a KiCad 9 schematic: pcb/tile.kicad_sch.

Every connection is a short wire stub off the pin, ending in a power symbol
(GND, +3V3, ...) or a global label named after the net -- the same style as
HE60's schematic.  Unused pins get a no-connect flag, so KiCad's ERC can tell
"deliberately unused" from "forgotten".

Then it checks itself with KiCad: it runs ERC, exports KiCad's own netlist of
the file it just wrote, and requires that netlist to equal tile_circuit.nets()
exactly.  The symbol positions are cosmetic; that comparison is the proof.

    ./kpython.sh gen_sch.py
"""

import copy
import functools
import json
import math
import os
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

import kisexp as k
from kisexp import S, Sym
import tile_circuit as tc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pcb")
SCH = os.path.join(OUT, "tile.kicad_sch")
PROJECT = "tile"
SYMBOL_DIR = os.environ.get(
    "KICAD9_SYMBOL_DIR",
    "/nix/store/6ayphbhvpaql7s42qnwxpzl731scj0b1-kicad-symbols-e92aabf81d/share/kicad/symbols")
LIB_PATHS = {"tile": os.path.join(OUT, "tile.kicad_sym")}

GRID = 1.27
STUB = 2.54
POWER_NETS = {"GND": "power:GND", "+3V3": "power:+3V3", "+3.3VA": "power:+3.3VA"}
# Which way each power symbol's graphic points with rotation 0 (visual degrees).
POWER_UP = {"power:GND": 270}


def new_uuid():
    return str(uuid.uuid4())


# A rerun keeps the sheet's and each part's UUID: the board links footprints to
# symbols by them, so a routed board still matches after the circuit changes.
try:
    PRIOR = json.load(open(os.path.join(OUT, "symbol_uuids.json")))
except FileNotFoundError:
    PRIOR = {"root": None, "symbols": {}}


def snap(v):
    return round(round(v / GRID) * GRID, 4)


# ---------------------------------------------------------------- libraries ---
class Libs:
    def __init__(self):
        self.trees = {}

    def _lib(self, nick):
        if nick not in self.trees:
            path = LIB_PATHS.get(nick) or os.path.join(SYMBOL_DIR, nick + ".kicad_sym")
            self.trees[nick] = k.parse(open(path).read())
        return self.trees[nick]

    def raw(self, nick, name):
        for s in k.children(self._lib(nick), "symbol"):
            if s[1] == name:
                return s
        raise KeyError("%s:%s not in library" % (nick, name))

    @functools.lru_cache(maxsize=None)
    def embedded(self, lib_id):
        """The symbol as a schematic embeds it: named "nick:name", with any
        `extends` resolved (the parent's body and pins, the child's properties).
        Cached and shared -- callers must not modify it."""
        nick, name = lib_id.split(":", 1)
        sym = copy.deepcopy(self.raw(nick, name))
        ext = k.find(sym, "extends")
        if ext:
            parent = copy.deepcopy(self.raw(nick, ext[1]))
            attrs = [c for c in parent[2:] if k.head(c) not in ("property", "symbol")]
            subs = k.children(parent, "symbol")
            for sub in subs:
                sub[1] = name + sub[1][len(ext[1]):]
            sym = [Sym("symbol"), name] + attrs + k.children(sym, "property") + subs
        sym[1] = lib_id
        return sym

    @functools.lru_cache(maxsize=None)
    def pins(self, lib_id, unit=1):
        """{number: (x, y, angle, name, type)} for a unit (plus the shared unit 0)."""
        sym = self.embedded(lib_id)
        base = lib_id.split(":", 1)[1]
        out = {}
        for sub in k.children(sym, "symbol"):
            u = int(sub[1][len(base) + 1:].split("_")[0])
            if u not in (0, unit):
                continue
            for p in k.children(sub, "pin"):
                at = k.find(p, "at")
                out[str(k.find(p, "number")[1])] = (at[1], at[2], at[3] if len(at) > 3 else 0,
                                                    k.find(p, "name")[1], str(p[1]))
        return out


# ---------------------------------------------------------------- schematic ---
def _effects(size=1.27, hide=False, justify=None, bold=False):
    font = S("font", S("size", size, size))
    if bold:
        font.append(S("bold", Sym("yes")))
    e = S("effects", font)
    if justify:
        e.append(S("justify", *[Sym(j) for j in justify.split()]))
    if hide:
        e.append(S("hide", Sym("yes")))
    return e


def _prop(key, value, x, y, hide=False, angle=0, justify=None):
    return S("property", key, value, S("at", x, y, angle), _effects(hide=hide, justify=justify))


class Sheet:
    def __init__(self, libs, paper="A1"):
        self.libs = libs
        self.root = PRIOR["root"] or new_uuid()
        self.paper = paper
        self.items = []
        self.used = set()
        self.symbol_uuid = {}       # ref -> uuid (gen_pcb.py links footprints by it)
        self.n_pwr = 0

    # -- placement -----------------------------------------------------------
    def pin_xy(self, lib_id, unit, at, rot, number):
        x, y, a, _, _ = self.libs.pins(lib_id, unit)[number]
        r = math.radians(rot)
        vx, vy = x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)
        out = (a + 180 + rot) % 360           # visual direction away from the body
        return (snap(at[0] + vx), snap(at[1] - vy)), out

    def _instance(self, lib_id, ref, value, at, rot=0, unit=1, footprint="", dnp=False,
                  extra=(), hide_ref=False, hide_value=False, in_bom=True):
        self.used.add(lib_id)
        u = PRIOR["symbols"].get(ref) or new_uuid()
        pins = self.libs.pins(lib_id, unit)
        ys = [-p[1] for p in pins.values()] or [0]
        top, bot = at[1] + min(ys) - 3.81, at[1] + max(ys) + 3.81
        node = S("symbol", S("lib_id", lib_id), S("at", at[0], at[1], rot), S("unit", unit),
                 S("exclude_from_sim", Sym("no")), S("in_bom", Sym("yes" if in_bom else "no")),
                 S("on_board", Sym("yes")), S("dnp", Sym("yes" if dnp else "no")), S("uuid", u),
                 _prop("Reference", ref, at[0], snap(top), hide=hide_ref),
                 _prop("Value", value, at[0], snap(bot), hide=hide_value),
                 _prop("Footprint", footprint, at[0], at[1], hide=True),
                 _prop("Datasheet", "", at[0], at[1], hide=True),
                 _prop("Description", "", at[0], at[1], hide=True))
        for key, val in extra:
            node.append(_prop(key, val, at[0], at[1], hide=True))
        for num in pins:
            node.append(S("pin", num, S("uuid", new_uuid())))
        node.append(S("instances", S("project", PROJECT,
                                     S("path", "/" + self.root, S("reference", ref), S("unit", unit)))))
        self.items.append(node)
        return u

    def place(self, part, at, rot=0):
        """Place a tile_circuit Part and draw every one of its connections."""
        at = (snap(at[0]), snap(at[1]))
        pins = self.libs.pins(part.symbol, part.unit)
        for num, want in part.names.items():
            got = pins[num][3]
            assert got.replace(" ", "") == want, \
                "%s pin %s is %r in the symbol, circuit expects %r" % (part.ref, num, got, want)
        missing = set(part.pins) - set(pins)
        assert not missing, "%s: pins %s not in symbol %s" % (part.ref, missing, part.symbol)
        extra = [("LCSC", part.lcsc)] if part.lcsc else []
        if part.mpn:
            extra.append(("MPN", part.mpn))
        self.symbol_uuid[part.ref] = self._instance(
            part.symbol, part.ref, part.value, at, rot, part.unit, part.footprint, part.dnp, extra)
        for num in pins:
            end, out = self.pin_xy(part.symbol, part.unit, at, rot, num)
            net = part.pins.get(num)
            if net is None:
                self.items.append(S("no_connect", S("at", *end), S("uuid", new_uuid())))
            else:
                self.attach(net, end, out)

    def attach(self, net, end, out):
        dx, dy = round(math.cos(math.radians(out))), -round(math.sin(math.radians(out)))
        tip = (snap(end[0] + dx * STUB), snap(end[1] + dy * STUB))
        self.wire(end, tip)
        if net in POWER_NETS:
            self.power(POWER_NETS[net], tip, out)
        else:
            self.label(net, tip, out)

    def wire(self, a, b):
        self.items.append(S("wire", S("pts", S("xy", *a), S("xy", *b)),
                            S("stroke", S("width", 0), S("type", Sym("default"))), S("uuid", new_uuid())))

    def power(self, lib_id, at, out):
        """A power symbol whose graphic points `out`, away from the pin."""
        up = POWER_UP.get(lib_id, 90)
        self.n_pwr += 1
        prefix = "#FLG" if lib_id.endswith("PWR_FLAG") else "#PWR"
        value = self.libs.raw("power", lib_id.split(":")[1])
        value = next(p[2] for p in k.children(value, "property") if p[1] == "Value")
        self._instance(lib_id, "%s%03d" % (prefix, self.n_pwr), value, at, (out - up) % 360,
                       hide_ref=True, in_bom=False)

    def label(self, net, at, out):
        just = "right" if out in (180, 270) else "left"
        self.items.append(S("global_label", net, S("shape", Sym("passive")), S("at", at[0], at[1], out),
                            S("fields_autoplaced", Sym("yes")), _effects(justify=just), S("uuid", new_uuid()),
                            S("property", "Intersheetrefs", "${INTERSHEET_REFS}", S("at", at[0], at[1], 0),
                              _effects(justify=just, hide=True))))

    def text(self, s, at, size=2.54, bold=True):
        self.items.append(S("text", s, S("exclude_from_sim", Sym("no")), S("at", at[0], at[1], 0),
                            _effects(size=size, bold=bold, justify="left bottom"), S("uuid", new_uuid())))

    def write(self, path):
        libsyms = [self.libs.embedded(lib_id) for lib_id in sorted(self.used)]
        tree = S("kicad_sch", S("version", 20250114), S("generator", "eeschema"),
                 S("generator_version", "9.0"), S("uuid", self.root), S("paper", self.paper),
                 S("title_block", S("title", tc.TITLE), S("rev", "0.1"),
                   S("comment", 1, "Generated by gen_sch.py from tile_circuit.py -- edit those, not this file")),
                 S("lib_symbols", *libsyms), *self.items,
                 S("sheet_instances", S("path", "/", S("page", "1"))), S("embedded_fonts", Sym("no")))
        with open(path, "w") as fh:
            fh.write(k.dump(tree) + "\n")


# ------------------------------------------------------------------- layout ---
def layout(sheet):
    P = tc.PARTS
    by_block = {}
    for p in P.values():
        by_block.setdefault(p.block, []).append(p)

    def grid(parts, x0, y0, cols, dx, dy):
        for i, p in enumerate(parts):
            sheet.place(p, (x0 + (i % cols) * dx, y0 + (i // cols) * dy))

    # Keys: one cell per key, laid out like the tile itself (columns across,
    # rows up the page), each with its sensor and two capacitors.
    sheet.text("KEYS: Hall sensor under every switch; 100n on its supply, 4.7n on its output (as HE60)",
               (20.32, 322.58))
    keys = by_block["keys"]
    for i in range(len(tc.KEYS)):
        _, c, r, _, _ = tc.KEYS[i]
        x, y = 30.48 + c * 60.96, 330.2 + (tc.g.ROWS - 1 - r) * 50.8
        he, c1, c2 = keys[3 * i: 3 * i + 3]
        sheet.place(he, (x, y + 7.62))
        sheet.place(c1, (x + 25.4, y + 7.62))
        sheet.place(c2, (x + 40.64, y + 7.62))

    sheet.text("MUXES: one 8:1 analog mux per key row; S0-S2 shared, outputs to the MCU's ADC",
               (20.32, 233.68))
    muxes = by_block["muxes"]
    for m in range(len(muxes) // 2):
        am, cap = muxes[2 * m], muxes[2 * m + 1]
        sheet.place(am, (50.8 + m * 96.52, 271.78))
        sheet.place(cap, (88.9 + m * 96.52, 271.78))

    sheet.text("MCU: AT32F405, 12 MHz crystal, decoupling, reset and boot, SWD header (DNP)",
               (330.2, 20.32))
    mcu = by_block["mcu"]
    sheet.place(mcu[0], (406.4, 116.84))
    grid(mcu[1:], 495.3, 50.8, 4, 22.86, 33.02)

    sheet.text("PORTS: JST GH, one per edge, own UART each; 220R series (see tile_circuit.py)",
               (640.08, 20.32))
    ports = by_block["ports"]
    for e in range(4):
        j, r1, r2 = ports[3 * e: 3 * e + 3]
        y = 45.72 + e * 45.72
        sheet.place(j, (680.72, y))
        sheet.place(r1, (749.3, y - 2.54))
        sheet.place(r2, (774.7, y - 2.54))

    sheet.text("POWER: 7-20 V in -> TVS -> 3.7 V buck -> 3.3 V LDO; a ferrite splits off +3.3VA",
               (20.32, 20.32))
    grid(by_block["power"], 33.02, 55.88, 8, 33.02, 50.8)
    for i, net in enumerate(tc.PWR_FLAG_NETS):
        at = (snap(33.02 + i * 33.02), snap(180.34))
        sheet.power("power:PWR_FLAG", at, 90)
        sheet.wire(at, (at[0], snap(at[1] + STUB)))
        tip = (at[0], snap(at[1] + STUB))
        if net in POWER_NETS:
            sheet.power(POWER_NETS[net], tip, 270)
        else:
            sheet.label(net, tip, 270)


# ------------------------------------------------------------------- checks ---
def kicad_cli(*args):
    return subprocess.run([os.path.join(HERE, "kcli.sh")] + list(args), capture_output=True, text=True)


def check(path):
    xml = path.replace(".kicad_sch", ".net.xml")
    r = kicad_cli("sch", "export", "netlist", "--format", "kicadxml", "-o", xml, path)
    assert os.path.exists(xml), "netlist export failed:\n" + r.stdout + r.stderr
    root = ET.parse(xml).getroot()
    got = {}
    unconnected = {}            # KiCad's net name for each deliberately unused pin
    for n in root.iter("net"):
        name = n.get("name").lstrip("/")
        if name.startswith("unconnected-"):
            for x in n.iter("node"):
                unconnected.setdefault(x.get("ref"), {})[x.get("pin")] = n.get("name")
            continue
        got[name] = sorted((x.get("ref"), x.get("pin")) for x in n.iter("node")
                           if not x.get("ref").startswith("#"))
    want = tc.nets()
    bad = {n for n in set(got) | set(want) if got.get(n) != want.get(n)}
    os.remove(xml)
    print("netlist: KiCad reads %d nets, circuit has %d -> %s"
          % (len(got), len(want), "IDENTICAL" if not bad else "%d DIFFER" % len(bad)))
    for n in sorted(bad)[:10]:
        print("   ", n, "kicad:", got.get(n), "circuit:", want.get(n))

    rpt = path.replace(".kicad_sch", ".erc.json")
    kicad_cli("sch", "erc", "--format", "json", "--severity-all", "-o", rpt, path)
    data = json.load(open(rpt))
    counts = {}
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            key = (v["severity"], v["type"])
            counts[key] = counts.get(key, 0) + 1
    print("ERC: %s" % (", ".join("%d %s %s" % (n, s, t) for (s, t), n in sorted(counts.items())) or "clean"))
    errors = sum(n for (sev, _), n in counts.items() if sev == "error")
    return not bad and not errors, unconnected


def main():
    os.makedirs(OUT, exist_ok=True)
    sheet = Sheet(Libs())
    layout(sheet)
    sheet.write(SCH)
    print("wrote %s: %d parts" % (os.path.relpath(SCH, HERE), len(sheet.symbol_uuid)))
    ok, unconnected = check(SCH)
    # gen_pcb.py links footprints to symbols by these, and gives unused pads
    # the same placeholder nets, so DRC's schematic-parity check agrees.
    with open(os.path.join(OUT, "symbol_uuids.json"), "w") as fh:
        json.dump({"root": sheet.root, "symbols": sheet.symbol_uuid, "unconnected": unconnected},
                  fh, indent=1)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
