# HE60 reverse-engineering notes — the design rules to copy

Extracted by parsing `refs/HE60/HE60/HE60.kicad_sch` and `HE60.kicad_pcb` directly
(KiCad 9 s-expression files), plus `refs/libhmk` firmware source. Everything here
is read out of the actual design, not inferred.

## 1. Bill of materials (from the schematic)

```
1  × AT32F405RCT7      LQFP-64 10x10mm P0.5mm     Artery Cortex-M4, HS USB
67 × MT9102ET          SOT-23-3                   Hall sensor
9  × SN74LV4051A       TSSOP-16 4.4x5mm P0.65mm   8:1 analog mux
1  × X322512MSB4SI     Crystal_SMD_3225-4Pin      12 MHz
1  × USBLC6-2SC6       SOT-23-6                   USB ESD protection
1  × TLV75733PDBV      SOT-23-5                   LDO → +3.3VA (analog rail)
1  × XC6206P332MR-G    SOT-23-3                   LDO → +3.3V
1  × TYPE-C-31-M-12    USB_C_Receptacle_HRO
1  × Fuse_0805         + 2× TS-1187A push buttons + JST-SH
```

252 BOM rows total. Note the **two separate LDOs**: a dedicated `+3.3VA` rail
for the analog domain, fed from `+5V` (USB). Hall sensors run on `+3.3VA`,
not the digital rail.

## 2. The sensing chain — exact topology

**Three shared select lines drive all nine muxes.**
`MUX_SELECT_0/1/2` each fan out to AM1…AM9 *and* one MCU pin. Not nine sets of
address lines — three, total.

**Each mux gets its own dedicated ADC channel.** No second-stage multiplexing:

| mux | common output (pin 3) | MCU pin |
|---|---|---|
| AM1 | MUX_OUT_1 | U4 pin 17 |
| AM2 | MUX_OUT_2 | U4 pin 20 |
| AM3 | MUX_OUT_3 | U4 pin 21 |
| AM4 | MUX_OUT_4 | U4 pin 22 |
| AM5 | MUX_OUT_5 | U4 pin 23 |
| AM6 | MUX_OUT_6 | U4 pin 24 |
| AM7 | MUX_OUT_7 | U4 pin 25 |
| AM8 | MUX_OUT_8 | U4 pin 26 |
| AM9 | MUX_OUT_9 | U4 pin 27 |

Those MCU pins are PA0–PA7 and PB0 — **nine analog inputs in parallel**, which
is what makes the throughput work.

Complete SN74LV4051A wiring, verified pin by pin against the netlist:

| pin | function | HE60 connects to |
|---:|---|---|
| 16 | VCC | `+3.3VA` (all 9 muxes) |
| 8 | GND | `GND` (all 9) |
| 7 | VEE (negative rail) | **`GND`** (all 9) — single-supply operation |
| 6 | E̅ (enable, active LOW) | **`GND`** (all 9) — permanently enabled |
| 11 | S2 | `MUX_SELECT_0` |
| 10 | S1 | `MUX_SELECT_1` |
| 9 | S0 | `MUX_SELECT_2` |
| 3 | Z (common) | `MUX_OUT_n` |
| 12,13,14,15,1,2,4,5 | Y0…Y7 | sensor outputs |

Three things worth noting:
- **VEE tied to GND** — the mux is used single-supply. Inputs must stay within
  0…3.3 V, which Hall sensor outputs do.
- **Enable strapped low** — all muxes permanently on. No GPIO spent on it.
- **The select mapping is crossed** (S0←MUX_SELECT_2, S2←MUX_SELECT_0). Harmless
  — it is just a naming choice — but the firmware's mux matrix must match it,
  which is exactly what `keyboard.json`'s `matrix` array encodes.

Also note **67 keys use 70 of the 72 available mux inputs** — 89 % efficient,
with only two channels (AM5 pin 2 and AM5 pin 15) strapped to GND as a
deliberate benchmark or placeholder.

**Every sensor output has a decoupling capacitor.** All 69 Hall nets carry one
(C2 for HE1, C4 for HE2, …). **Zero series resistors** — no RC filtering between
sensor and mux. All filtering is digital.

## 3. Sensor placement — the mechanical rule

Measured from the PCB file:

- **47 of 70 sensors sit at exactly the same X/Y as their switch**, rotated −90°.
- Reference numbers pair one-to-one: `HE38` ↔ `MX38`.
- The exceptions are edge/odd-key positions; one outlier is 9.525 mm off — an
  alternate-layout key, not a different rule.

Physical pad positions in the footprint, unrotated:
- pad 1 `VCC` at (−1.1375, +0.95), pad 2 `OUT` at (−1.1375, −0.95), pad 3 `GND` at (+1.1375, 0)
- all pads 1.325 × 0.6 mm, rotation 270°, on **B.Cu**

So **the sensor is centred on the switch stem** — which is exactly what you want,
because the magnet runs down the middle of the switch. The sensing axis points
through the board.

**The footprint carries no plated holes**, so the SOT-23-3 sensor is a plain
surface-mount part. Because it is centred on the switch, you can only gild it on
the *sensor* side and route away from there.

## 4. Board construction

| property | value |
|---|---|
| layers | **2** (F.Cu, B.Cu) |
| stackup | 0.035 mm Cu / 1.51 mm core / 0.035 mm Cu |
| vias | 2,123 × 0.6 mm with 0.3 mm drill |
| track widths | **0.3 mm** signal, **0.5 mm** power — only two widths used |
| track segments | 1,787 (1,535 on B.Cu, 252 on F.Cu) |
| copper zones | **zero** — no ground pour at all |
| footprints | 332, of which 253 on B.Cu |

**This is the biggest surprise: a 67-key 8 kHz Hall board on 2 layers, no ground
plane.** My earlier advice — 4 layers with solid ground and power planes — was
invented. The real design puts almost everything on the bottom and routes top
only where forced.

Component placement: **sensors, muxes and passives are on the bottom** (253 of
332 footprints on B.Cu); switches and the USB connector are on top.

## 5. Firmware — why it is fast

From `refs/libhmk/src/hardware/at32f405xx/analog.c`:

```c
dma_init_struct.buffer_size = ADC_NUM_MUX_INPUTS + ADC_NUM_RAW_INPUTS;
dma_init_struct.peripheral_base_addr = (uint32_t)&ADC1->odt;
dma_init_struct.loop_mode_enable = TRUE;
dmamux_init(DMA1MUX_CHANNEL1, DMAMUX_DMAREQ_ID_ADC1);
nvic_irq_enable(DMA1_Channel1_IRQn, 0, 0);
adc_dma_mode_enable(ADC1, TRUE);
```

and the per-tick hook:

```c
void analog_task(void) {}
```

**Empty.** The ADC runs autonomously under DMA, cycling the mux select lines and
writing results into a buffer. The CPU is not in the sampling loop; it wakes on
a DMA interrupt and reads nine finished values.

ADC config: **12-bit**, `ADC_SAMPLETIME_7_5`.

Filtering is a single exponential moving average per key in `matrix.c`:

```c
#define EMA(x, y) (((uint32_t)(x) + ((uint32_t)(y) * ((1 << MATRIX_EMA_ALPHA_EXPONENT) - 1))) >> MATRIX_EMA_ALPHA_EXPONENT)
```

Calibration (`matrix_recalibrate`) has two parts:
1. **Rest value**: sampled at boot while nothing is pressed, kept as a running
   minimum — it only ratchets *down* (`if (new + EPSILON <= rest) rest = new`).
2. **Bottom-out value**: discovered lazily during normal scanning, not at boot.

## 6. Scaling to 280 keys

| | HE60 (67 keys) | yours (280 keys) |
|---|---|---|
| Hall sensors | 67 × MT9102ET | 280 × GH39FKSW |
| muxes | 9 × 8:1 | **35 × 8:1** |
| ADC channels needed | 9 | 35 — more than one MCU has |
| select lines | 3, shared | **3, still shared** |
| MCUs | 1 × AT32F405 | **4** × AT32F405 (+ USB hub) |

35 muxes at 8–9 per MCU means **4 MCUs**, which is why the build needs a USB hub
— exactly the architecture already planned.

## 7. Sensor part choice

| part | package | stock | $ @ qty | note |
|---|---|---:|---:|---|
| GH39FKSW | SOT-23-3L | 6,144 | **0.0847 @500** | HE60 lists as "cheapest, **inverted**" |
| MT9102ET | SOT-23 | 5,928 | 0.1951 @500 | what HE60 actually uses |

HE60's README warns: *"Different sensors may also have different polarity, and
may require output inversion."* libhmk handles this with a single flag —
`"invert_adc": true` in `keyboard.json`. Get it wrong and every key reads
backwards.

## 8. Open questions before building

1. **GH39FKSW pad-out and sensing axis** vs MT9102ET — need its datasheet to
   confirm the SOT-23-3 pinout and field direction match. HE60's own pinout
   (pad 1 VCC, pad 2 OUT, pad 3 GND) is confirmed; the substitute's is not.
2. **Whether 280 keys stays on 2 layers.** HE60 proves 67 does. 4.2× the keys
   is not automatically fine; this is the main thing a prototype settles.
3. **libhmk's RGB limitation** — its README states plainly: *"RGB Lighting: The
   firmware does not support RGB lighting."* Per-key LED support means writing
   that layer yourself.

## 9. Two KiCad gotchas found while generating boards

Both cost real time to find, so they are worth recording.

**`(net N "NAME")` inside a pad must come after `(layers ...)`.** Placing it
right after the pad name — the obvious reading of the format — makes
`kicad-cli pcb drc` fail with a bare `Failed to load board` and no diagnostic.
It parses, then fails. Fatal and silent.

**Writing nets into pads by hand produces a corrupt netlist even when it
parses.** Injecting the attribute at the correct position produced a board that
loaded, but DRC reported *"Items shorting two nets (nets HE1 and HE2)"* on
adjacent pads of a single mux — neighbouring channels of one IC reading as
shorted to each other. The format had parsed but the association was wrong.
**Do not hand-write pad nets.** Generate a netlist and apply it with
KiCad's own engine (`Update PCB from Schematic`). `gen_hex_board.py` does this.

**DRC on a 0.65 mm-pitch TSSOP reports 582 clearance violations at the stock
0.2 mm netclass rule.** Adjacent pads of a 0.325 mm-wide land on 0.65 mm pitch
are 0.0 mm apart as the rule measures it. This is a *netclass configuration*
issue, not a footprint defect — the pads are within the package's own
tolerances. Set a rule for the mux netclass (or reduce clearance for that
footprint) rather than editing a vendor footprint.
