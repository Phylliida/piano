# PCB fabrication quotes — verified live from vendor calculators

Recorded from research agent `359d3b0d` (finished 2026-10-05). These came from
actually driving JLCPCB's and PCBWay's quote pages, not from documentation.

## Bare PCB, 250 x 250 mm, 4-layer

| vendor | qty | PCB price | shipping | note |
|---|---:|---:|---:|---|
| **JLCPCB** | 5 | **$63.60** | — | $12.72/board |
| **JLCPCB** | 50 | **$411.45** | — | $8.23/board |
| PCBWay | 5 | $173.29 | $40.28 | **2.5x JLCPCB** |

That is **$0.0102/cm²**, against the **$0.040/cm²** I had been estimating.
My estimate was ~4x too high.

## Large-size surcharges are real, and they are per board

| board size | area | "Large Size" fee at qty 5 |
|---|---:|---:|
| 250 x 250 mm | 625 cm² | **$0.00** |
| 250 x 275 mm | 688 cm² | $3.00 |
| 300 x 300 mm | 900 cm² | $20.30 |
| 400 x 500 mm | 2000 cm² | $134.00 |

So the threshold is 650 cm², confirmed empirically — this matches the $57.46
figure read off the assembly price page, which is the *assembly* side of the
same threshold and applies **per board, separately from fabrication**.

## The constraint that actually shapes the design

Two independent rules combine badly:

1. **Economic PCBA is capped at 250 x 250 mm panels.** Larger boards force the
   much pricier **Standard** assembly tier.
2. **Assembly charges $57.46 when PCB area > 650 cm²**, plus the fabrication
   surcharge above.

A single 462 x 325 mm board for 280 keys (1556 cm²) trips *all three*: the
fabrication surcharge, the assembly surcharge, and forced Standard assembly.

**Panels at or below 250 x 250 mm dodge every one of them.** That reverses my
earlier conclusion — I had said "use one big board, no tiling needed", based on
the 663 x 593 mm fabrication maximum. The fabrication maximum is real, but
Economic *assembly* is the tighter limit, and 250 x 250 mm is the number that
matters.

## Also found

- **A 400 x 500 mm board cannot be v-scored** — v-cut panels max out at
  475 x 475 mm. Not binding at 250 x 250, but relevant if the design grows.
- **300 resin hex keycaps: $96.00 total, $0.32/part** — from a real STL
  generated and uploaded to JLC3DP. Useful as the "I don't want to print 280
  caps" fallback; compare against $19.10 of filament and 38 h of printing.

## Not verified

- Assembly cost per board — needs real gerbers and a BOM, which do not exist
  until the board is designed.
- The entire 920 x 360 mm case-panel price. JLCCNC's sheet-metal form parsed a
  STEP file but never produced a price, and their CNC form rejected
  agent-generated STEPs. Not a size limit — a file-generator limitation.
- Methodology note from the agent: its first size sweep leaked option state
  between rows (ENIG then 2 oz copper compounding), producing garbage deltas.
  The tables above come from clean re-runs on fresh pages.
