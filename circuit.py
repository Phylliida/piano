"""A circuit as data: parts, and which net each of their pins is on.

tile_circuit.py and hub_circuit.py each fill one Circuit; the schematic and
board generators read nothing else.  Connections name the pin by number AND,
optionally, by the name the symbol gives it, e.g. ("U2", "3", "IN"); the
schematic generator refuses to run if the symbol disagrees, so a wrong pin
number fails loudly instead of shipping.
"""

# Footprint libraries: KiCad's standard ones, by nickname.
FP_C0402 = "Capacitor_SMD:C_0402_1005Metric"
FP_C0603 = "Capacitor_SMD:C_0603_1608Metric"
FP_C0805 = "Capacitor_SMD:C_0805_2012Metric"
FP_C1206 = "Capacitor_SMD:C_1206_3216Metric"
FP_R0402 = "Resistor_SMD:R_0402_1005Metric"


class Part:
    def __init__(self, ref, symbol, value, footprint, block, *, lcsc="", mpn="",
                 dnp=False, unit=1):
        self.ref, self.symbol, self.value, self.footprint = ref, symbol, value, footprint
        self.block, self.lcsc, self.mpn, self.dnp, self.unit = block, lcsc, mpn, dnp, unit
        self.pins = {}          # pin number -> net
        self.names = {}         # pin number -> expected pin name (checked)


class Circuit:
    def __init__(self):
        self.parts = {}         # ref -> Part, in the order they were added
        self._counters = {}

    def part(self, prefix, symbol, value, footprint, block, **kw):
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        p = Part("%s%d" % (prefix, self._counters[prefix]), symbol, value, footprint, block, **kw)
        self.parts[p.ref] = p
        return p

    def connect(self, net, *pins):
        """connect("GND", ("U1", "31", "VSS"), ("C3", "2")) -- name optional."""
        for pin in pins:
            ref, num = pin[0], str(pin[1])
            p = self.parts[ref]
            assert num not in p.pins, "%s.%s connected twice (%s, %s)" % (ref, num, p.pins[num], net)
            p.pins[num] = net
            if len(pin) > 2:
                p.names[num] = pin[2]

    def cap(self, value, net1, net2, block, footprint=FP_C0402, **kw):
        c = self.part("C", "Device:C", value, footprint, block, **kw)
        self.connect(net1, (c.ref, 1))
        self.connect(net2, (c.ref, 2))
        return c

    def res(self, value, net1, net2, block, **kw):
        r = self.part("R", "Device:R", value, FP_R0402, block, **kw)
        self.connect(net1, (r.ref, 1))
        self.connect(net2, (r.ref, 2))
        return r

    def nets(self):
        """{net: sorted [(ref, pin)]} -- the single connectivity truth."""
        out = {}
        for p in self.parts.values():
            for pin, net in p.pins.items():
                out.setdefault(net, []).append((p.ref, pin))
        return {n: sorted(v) for n, v in sorted(out.items())}

    def summary(self, blocks):
        ns = self.nets()
        print("%d parts (%d DNP), %d nets, %d pin connections"
              % (len(self.parts), sum(p.dnp for p in self.parts.values()), len(ns),
                 sum(len(v) for v in ns.values())))
        single = [n for n, v in ns.items() if len(v) < 2]
        assert not single, "nets with one pin: %s" % single
        for blk in blocks:
            print("  %-6s %d parts" % (blk, sum(p.block == blk for p in self.parts.values())))
