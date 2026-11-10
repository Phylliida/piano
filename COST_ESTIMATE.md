# Hexagonal Hall-effect keyboard — parts and cost estimate

Reference product: **Lumatone Keyboard, $4,950** (verified from
`orders.lumatone.io/products.json`). 280 illuminated hex keys, "Non-Contact
Hall-Effect Sensor" sensing, 92 × 35.5 × 4.6 cm, 10.8 kg, 5 A @ 12 V
(all from `lumatone.io/details-specs`).

The interesting finding up front: **the Lumatone is already a Hall-effect
instrument.** Your Wooting-switch premise is not an approximation of the
Lumatone's sensing — it is the same sensing principle, done on a modern
production board. That removes the main technical risk.

---

## 1. Headline numbers

One instrument, 280 keys, full analog sensing, one prototype run:

**280 keys, printing the keycaps and case yourself: $387 for the first unit,
$313 each at volume 25.** The mechanicals stop being money and become *time*
instead — see the print budget below.

| switch choice | switches cost | **first unit** | per key |
|---|---:|---:|---:|
| **Wooting Lekker L60 v2.5 $0.428** (recommended) | $119.96 | **$387** | $1.12 |
| Gateron Aurora dual-rail $0.318 | $105.00 | $356 | $1.01 |
| TTC KTEK New Star $0.27 | $75.60 | $343 | $0.96 |

Versus $4,950 for a Lumatone. Same key count, same sensing principle,
roughly **1/13 the price**.

### Why I recommend the Wooting switches despite the $44 premium

The cheapest in-stock switch is $0.27 (TTC KTEK New Star) and Wooting's Lekker
L60 v2.5 is $0.4284. Paying 59% more for switches sounds wrong on a
cost-cutting build, but three things make it the right call here:

1. **Exactly 280.** Wooting sells 70-packs, so 4 packs = 280 switches = $119.96
   with free shipping (cart-verified: subtotal = total). No leftovers, no
   partial pack. The Aurora route needs 3 × 110 = 330 switches and still lands
   under MK.com's $120 free-shipping bar.
2. **Magnet consistency is your calibration.** This is the real reason. Your
   sensing accuracy depends on every key's magnet producing the same field at
   the same travel. A cheap switch with sloppy magnet placement or strength
   spread means your per-key calibration has to absorb that variance — and if
   the spread is wide enough, keys feel different from each other. Wooting
   controls this tightly because it has to.
3. **It is the one part you cannot reflow your way out of.** The PCB, sensors
   and firmware are all iterable. If the switches are inconsistent, you
   re-buy them.

$44 buys a known-quantity magnet and removes the largest unknown from the
sensing chain. That is a good trade on a $387 instrument. Buy the **v2.5**
90-pack variant if you prefer 90s — the v2 90-pack is out of stock.

### The print budget, which is now the real constraint

| | volume | time | filament |
|---|---:|---:|---:|
| 280 caps, plated 72 per plate (4 plates) | 616 cm³ | **38 h** | 764 g |
| case, 6–10 printed sections | 79 cm³ | **4 h** | 79 g |
| plate swaps | — | **0.5 h** | — |
| **total** | 695 cm³ | **~43 h** | 843 g = $21 |

43 hours of continuous printing on one machine — nearly two days where the
printer is unavailable for anything else. Plating is what keeps this sane: the
same 280 caps printed one at a time would spend most of their time in travel and
layer changes rather than extruding.

Switch prices I could actually verify, cheapest first:

92 SKUs are catalogued in `research/magnetic_switch_prices.csv`. The relevant band:

| switch | pack | $/switch | note |
|---|---:|---:|---|
| Outemu Peach V3 Pro Silent | 10 / $2.50 | $0.250 | cheapest anywhere, but 28 packs |
| TTC KTEK New Star | each | $0.270 | cheapest in stock, single SKU |
| Gateron Aurora dual-rail | 110 / $35 | $0.318 | in stock; 50g/70g, heavy |
| **Wooting Lekker L60 v2.5** | **70 / $29.99** | **$0.428** | **recommended** |
| Wooting Lekker Tikken Medium | 90 / $49.99 | $0.555 | clicky |
| Gateron Magnetic Jade Pro | 18 / $11.52 | $0.640 | Divinikey |
| Gateron Magnetic Jade Ultra | 90 / $82.80 | $0.920 | PIIFOX |
| Gateron KS-20 Magnetic White | 35 / $67.90 | $1.940 | worst value found |

Every magnetic switch is **passive** — the magnet is in the slider, the sensing
is on your PCB. That is why the Hall sensor line exists at all, and it is the
same architecture the Lumatone uses.

Smaller instruments get *worse per-key* because the case, PCB and PCBA charges
are near-fixed:

| keys | materials | first unit | per key | board | print |
|---|---|---|---|---|---|
| 280 | $310 | $387 | $1.12 | one 462 × 325 mm 4-layer | 43 h |
| 140 | $177 | $238 | $1.28 | one 325 × 231 mm 4-layer | 21 h |
| 70 | $111 | $164 | $1.62 | one 325 × 231 mm — **2-layer will do here** | 10 h |

**Build three 70-key units instead of one 280-key unit and the per-key cost
collapses**, because at that size the board fits in 2-layer and the case
becomes a simple frame rather than a large enclosure.

Conversely, if you want the full 280 keys, go straight to a single large board.
The four-panel split in my first pass was based on a board-size limit that does
not exist.

---

## 2. Where the money actually goes (280 keys)

| line item | qty | unit | extended | basis |
|---|---:|---:|---:|---|
| Magnetic Hall switches | 280 | $0.428 | $119.96 | Wooting Lekker L60 v2.5, 4 x 70-pack, free ship |
| Hall sensors (SS49E, TO-92S) | 280 | $0.112 | $31.36 | JLCPCB parts API |
| Analog mux 16:1 (TMUX1308) | 18 | $0.106 | $1.91 | JLCPCB parts API |
| Front-end MCU (STM32G031) | 4 | $0.948 | $3.79 | JLCPCB parts API |
| 12 MHz crystals | 4 | $0.112 | $0.45 | JLCPCB parts API |
| USB hub (CH334R) | 1 | $0.350 | $0.35 | JLCPCB parts API |
| USB-C receptacle | 1 | $0.031 | $0.03 | JLCPCB parts API |
| RGB LEDs | 280 | $0.070 | $19.60 | estimate |
| LED matrix driver (IS31FL3731) | 2 | $0.962 | $1.92 | JLCPCB parts API |
| Passives R/C | 900 | $0.004 | $3.60 | estimate |
| PCB, 4-layer, 1556 cm² total | — | $0.040/cm² | $62.23 | estimate |
| Power supply + regulators | 1 | $14.00 | $14.00 | estimate |
| Keycaps, hexagonal, printed | 280 | $0.068 | $19.10 | filament only, 34 h printing |
| Case, printed frame + base | 1 | $1.98 | $1.98 | filament only, 4 h printing |
| Misc hardware | 1 | $30.00 | $30.00 | estimate |
| **materials** | | | **$310.27** | |
| PCBA: setup + soldering + placement + feeder loading + fixtures + X-ray | | | $76.68 | JLCPCB published fees |
| **first unit** | | | **$386.95** | |

Printing the mechanicals removes $308 from the build, and the cost profile
changes shape completely. **PCBA labour ($76.68) is now 23% of the total** —
the largest single line after switches — because it is a fixed one-off charge
against a much smaller parts bill. If you hand-solder the switches and the
through-hole parts yourself, that line largely disappears, at the cost of an
evening with an iron.

The entire sensing chain — sensor + mux + ADC-bearing MCU — is **$0.21 per
key**. Everything clever about this project is cheap.

Volume note: the PCBA charges are per-order, not per-board. Build 25 and they
amortise to $3.07/unit.

---

## 3. The electronics

### Sensing chain, per key
```
switch magnet  →  SS49E Hall sensor  →  TMUX1308 16:1 analog mux  →  STM32G031 ADC
                        $0.112                  $0.0066/key                12-bit
```

**Why this works.** A 16:1 mux plus a 12-bit ADC is noise-free enough by a wide
margin. Rough numbers: an N52 magnet 2–3 mm off an SS49E gives roughly 1000 G,
the SS49E puts out ~1.4 mV/G at 5 V, so full travel swings on the order of a
volt into a 3.3 V ADC — thousands of counts over 4 mm. Lumatone's own spec
caps polyphonic aftertouch at 15 simultaneous keys; per-key analog travel is a
strict superset of that.

**Scan rate.** 16 mux channels per ADC, ~10 µs per channel including mux
settling, 4 boards × 70 keys → **~2 kHz per key**. Wooting's own analog
keyboards run far below that, so there is headroom to burn.

**Zero-drift is the real engineering problem, not resolution.** SS49E quiescent
output drifts with temperature. Fix: a $0.02 NTC per board for compensation,
plus per-key auto-zero at boot while no key is pressed. This is the same class
of problem and the same fix that Hall-effect keyboards already use in
production.

**Magnetic crosstalk is the other one.** On a 24.15 mm hex pitch, each sensor
sees a few percent of its neighbours' field. It is deterministic, not noise, so
it calibrates out — but expect to spend bench time on it. **This is the single
most likely thing to force a redesign**, and it is why you prototype one panel
first.

### Firmware
Do not try to extend QMK's ADC driver to 280 channels — it is built for a
couple of knobs and will fight you. Run bare-metal on the STM32G031s with
TinyUSB, and have each board emit MIDI. Inter-MCU coordination is a $0.35
CH334R USB hub: the host sees one MIDI device and no firmware has to know about
the others. Prior art worth reading: `Charading/Shego75HE` on GitHub — a
Hall-effect board with custom magnetic QMK firmware.

Cost of the whole computing stack for 280 keys: **$10.29.** It is a rounding
error. Do not optimise it.

---

## 4. The mechanical problem, which is where the money and risk are

### PCB size
Each key needs 5.05 cm² of board at 24.15 mm hex pitch, so 280 keys is
**~1556 cm²** including bezel and interconnect.

I initially assumed JLCPCB capped boards at 300 × 300 mm and that the design
would have to tile into four panels. **That was wrong.** Their published
capabilities page gives:

> Maximum Dimensions — FR4(4-layer): **663 × 593 mm** ... 4-layer FR4 PCBs can
> reach a maximum size of **1016 × 596 mm**

So a single **462 × 325 mm** 4-layer board carries all 280 keys with room to
spare. This materially simplifies the build:

- no inter-panel connectors, no mechanical alignment tolerance in the case
- one board, one assembly order, one reflow pass
- no flex between zones

Two caveats remain. The large-board surcharge (>650 cm²) still applies —
$57.46 in the table above. And PCBA **fixtures** get bigger with board size,
which the estimate does not model (the $16.42 figure is JLCPCB's standard
fixture charge); a full-bed panel may exceed it.

JLCPCB also offers 2-layer FR4 up to 1020 × 600 mm, which would be a real
saving *if* you can route 280 analog channels plus the LED matrix on two
layers. That is plausible with a ground pour and a mostly-gridded layout, but
I have not verified the price difference. Worth a quote — it could be the
single largest cost saving left in the design.

Everything below still shows four sub-boards of 231 × 325 mm because that is
the layout the estimate was computed against; a single-board order changes
almost nothing in the money (the PCB line is priced per cm², and the panels
were already edge-tiled), but it does remove the three $0.60 interconnect
parts and simplifies assembly.

### Keycaps
Nobody sells hexagonal keycaps — Lumatone's are proprietary with integral
torsion-spring mounts. Printing them is the right call: 2.2 cm³ each, 7.5 min
each, $0.068 each in filament.

**Plated for a Bambu A1 Mini (180 × 180 mm bed):** at 24.15 mm column pitch and
18.19 mm row pitch you fit **8 × 9 = 72 caps per plate**, occupying 168 × 170 mm
of the 180 mm bed. So 280 caps is **3 full plates plus one of 64** — four plates
total, and the last one is still 89% full. Cap time is ~38 h plus ~0.5 h of
plate swaps.

**One thing to get right, because it can quietly break the sensing.** FDM holds
about ±0.2 mm; resin holds about ±0.1 mm. A keycap that sits 0.2 mm higher on
one key than its neighbour changes the magnet-to-sensor gap by 0.2 mm, which
changes the field strength and therefore the calibration curve. You have per-key
calibration, so this is survivable — but it converts a *design* problem into a
*per-unit calibration* problem, and if the spread gets bad enough the keys will
feel inconsistent.

Practical consequence: **use resin if you can** (or print caps at a fine layer
height and seat them on a consistent datum). The magnet gap should be set by the
switch body and the PCB, not by the keycap — if the cap is what positions the
magnet, cap tolerance becomes sensing tolerance. Design so the cap only
transmits force and light.

Layer lines are also a tactile issue on something your fingers rest on for
hours. Resin, or PETG sanded smooth, beats raw PLA.

### Case — and the A1 Mini is where it hurts
Because the switches mount on the PCB, **the PCB is the top plate** — no
separate switch plate needed. You need a bottom tray and an edge frame.

A 462 × 325 mm case obviously will not fit a 180 × 180 bed. The split that works
for the A1 Mini is **3 × 2 = 6 tray panels of 154 × 108 mm** (comfortably inside
the bed with room for a brim), plus frame rails along the outer edges — call it
8–10 printed sections total. Still only ~4 h and ~$3 of PETG, so the cost is
irrelevant; the cost is in the joining.

**This is the one place I'd push back on printing.** Six bolted panels forming a
462 × 325 mm surface is a lot of joints, and every joint is a hinge. Under hand
weight and with 280 switches being pressed, a segmented printed tray will flex
and creak in a way a single sheet does not — and flex in the case means the
magnet-to-sensor gap moves, which is the same failure mode as sloppy keycap
tolerances.

Two ways to fix it, both cheap:

1. **Bolt the printed tray down to a 3 mm MDF or aluminium sheet** cut to
   462 × 325 mm. The sheet carries the load, the print just provides geometry
   and mounting. This is what I'd do.
2. **Print the tray as a deep ribbed frame and skip the floor entirely** —
   open-bottom, sitting on a sheet or on the desk.

Either way, treat the printed case as a *fixture*, not a structure.

---

## 5. Cost of being wrong

Cheapest useful experiment first. A **35-key single panel** exercises every hard
part of the design at 1/8 scale:

| what it tests | why it can kill the project |
|---|---|
| mux + ADC scan chain | settling noise, channel bleed |
| magnetic crosstalk at 24.15 mm pitch | could force a pitch or magnet change |
| temperature drift + auto-zero | could force a reference sensor per board |
| keycap geometry and feel | 3–5 iterations, schedule risk |
| firmware MIDI pipeline | the only part with no prior art at this scale |

Cost of that prototype: roughly **$150–200** plus your time. Do that before
committing to four panels and a case.

---

## 6. What I could not verify

Be sceptical of these; they are estimates, not quotes:

- **PCB fabrication** — JLCPCB's quote API is not reachable
  (`/shoppingCart/pcb/selectPcbPrice` → 404). The $0.040/cm² figure for 4-layer
  1.6 mm HASL is my working estimate. Get a real quote before trusting it.
  Board *size limits* are verified (663 × 593 mm for 4-layer); board *price* is
  not. Also unverified: whether 2-layer FR4 at this size is meaningfully
  cheaper, which is the biggest remaining cost lever.
- **3D print time (42 h, 843 g)** — my estimate from part volume at an effective
  4.5 mm³/s deposition rate. Real throughput depends on your printer, layer
  height and how well you plate the parts; plating caps should beat this
  figure. Filament cost ($21) is solid; the hours are a model, not a stopwatch
  reading.
- **Printed case volume (79 cm³)** — a guess at a frame plus base for a
  462 × 325 mm instrument. Determine it from your actual CAD.
- **RGB LED unit price** — the $0.070 figure is an estimate; JLCPCB lists
  5050-package addressable LEDs at $0.0337–0.1098 depending on part.
- **No-name AliExpress/Alibaba switches (100+ qty)** — this is the one gap that
  could not be closed. Item pages are unreachable by any non-browser client
  (redirect loops, content-free shells, Alibaba serves a CAPTCHA), and the
  search payload exposes listing minimums without pack sizes, so no per-switch
  landed cost can be quoted. There may be a cheaper switch than $0.27 out
  there; I could not find or verify one.
- **NuPhy's magnetic switch range** — HTTP 429 on every attempt.
- **US sales tax** on the Wooting and mechanicalkeyboards.com orders.

## 7. Everything that *is* verified

- Lumatone price $4,950 and accessories (their store feed)
- Lumatone is Hall-effect, 280 keys, 21 mm key width, 24.15 mm centre-to-centre
  spacing, 5 A @ 12 V (their spec page)
- All component prices marked "JLCPCB parts API", pulled live by POSTing to
  `jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList`
- All PCBA fees, read off `jlcpcb.com/help/article/pcb-assembly-price`:
  setup $8.18, soldering $0.016/joint, placement $0.0016/joint, feeder loading
  $1.53/extended part, fixtures $16.42, X-ray $1.64/board, large-board
  surcharge $57.46

`bom_model.py` in this directory reproduces every number above and takes the
key count as a parameter — change the constants at the top to re-price.
