# piano

Open hardware modular hexagonal piano, inspired by the Lumatone.

It is built from identical tiles of 8 x 5 keys on a hex lattice. Each key is
an MX-style magnetic switch over a Hall-effect sensor, so the tile reads how
far every key is pressed. Tiles snap together edge to edge, in any
arrangement, and a short cable under each seam carries power and data. A hub
(not designed yet) will plug into any free edge, take power in and give USB
out: MIDI or raw key data.

## Status

| part | state |
|---|---|
| tile PCB | routed, DRC clean, not ordered yet. [`pcb/README.md`](pcb/README.md) lists what to verify before ordering |
| tile case | generated and checked on its solids, not printed yet |
| hub | not designed |
| firmware | not written |

## Where things are

| file | what |
|---|---|
| `tilegeom.py` | every dimension the board and the case share. Run it to check them |
| `tile_circuit.py` | the tile's circuit as data |
| `gen_lib.py`, `gen_sch.py`, `gen_pcb.py` | make the KiCad library, schematic and (once) the board |
| `route_board.py`, `pcbroute.py` | the board's routing as a script |
| `pcb/` | the KiCad project, and its README with the pin map for the firmware |
| `gen_case.py` | builds the printed case from `tilegeom.py` |
| `cad/` | its output: STLs ready to print, and two drawings |
| `HE60_NOTES.md` | notes on the sensing chain (sections 1 to 5) |

`TILE95.md`, `COST_ESTIMATE.md`, `gen_tile.py`, `gen_hex_board.py`,
`bom_model.py` and `kicad/` belong to earlier designs and contradict this one.

## The case

A printed tray (key plate, ribs, walls) and a printed bottom cover per tile.
No screws.

- **PCB:** drops into the tray from below. The switch pegs locate it.
- **Cover:** tuck its W edge over the lip on the W wall, then press its E edge
  down until the two catch beams click over the bumps on the E wall. Posts on
  the cover press the PCB against the ribs. To open it, pry a beam's tip out
  at its notch in the E wall, beside the E cable channel.
- **Tile to tile:** dovetail rails on a tile's E and N walls run in slots in
  the next tile's W and S walls. One snap latch per seam stops the tile
  lifting off again.
- **Cables:** at every port the cover and both walls are cut away, so the
  cable lies in an open channel from one tile's header to the next one's. It
  goes in from underneath, with the covers on.

![a latch and a rail in section](cad/joints.svg)

### Putting tiles together

1. Start with the south-west tile, keys up, on a table.
2. Lower each further tile straight down beside it: its W and S slots slide
   over the rails of the tiles already there, and the latches click. Tiles
   only go together downward, never sideways.
3. Work outward to the north and east. A tile cannot be lowered into a place
   that already has a tile to its north or east.
4. Turn the keyboard over and plug a 50 mm JST GH 6-pin cable across each
   seam ([`pcb/README.md`](pcb/README.md) says how to measure the 50 mm).
   Either way round works.

To take it apart, unplug the cables and pull tiles straight up, starting at
the north-east. A firm pull releases a latch.

The rails carry a tile's weight, the latches only hold it down. So lift an
assembly by its south-west tile or from underneath; lifted by another tile,
the ones south and west of it hang on their latches.

### Printing

`cad/` holds the parts in their print orientation: the tray upside down
(plate on the bed), the cover with its posts up. Print without support. One
tile's tray is 168 x 134 mm.

1. **`plate_test.stl`**, a 1.5 mm plate. Check that switches clip in, measure
   the two spans `gen_case.py` prints, and pass their ratio as `--xy-scale` if
   your printer shrinks.
2. **The coupons:** `coupon_tray.stl` twice, `coupon_cover.stl` and
   `coupon_pcb_dummy.stl` once. A coupon is a 2 x 4-key tile with every snap
   feature of the full one. Check the cover's catch on one, then join the two
   trays E to W and N to S.
3. **`tray.stl`, `cover.stl`** and, until boards exist, **`pcb_dummy.stl`**:
   a plastic stand-in with the board's outline, thickness and peg holes.

If a fit is off, change the number in `tilegeom.py` and run `gen_case.py`
again:

| symptom | constant |
|---|---|
| rails too tight or too loose in their slots | `RAIL_CLEAR` (0.15 mm) |
| latch lets go too easily, or will not let go | `LATCH_ANGLE` (50 degrees; higher holds harder) |
| latch does not click in | `LATCH_BITE` (0.6 mm), `LATCH_PLAY` (0.2 mm) |

`LIP_W` and `CATCH_D` must stay as they are: the board's outline is built on
them.

Not yet tried in plastic, so expect a round or two on the coupons. Known
soft spots:

- The latch beams stand upright on the bed and bend across their layers,
  the weak direction. They strain 0.7 %, which PLA should take; PETG is
  the safer choice.
- The W lip is a 1.5 mm ledge with nothing under it. Its underside, which
  the cover rests on, will come out rough.
- The dovetails' sharp edges print rounded, which loosens them slightly.

### Building the case files

```
python3 tilegeom.py     # check the geometry
python3 gen_case.py     # check the fits on the solids, write cad/
```

`gen_case.py` needs `manifold3d` and `numpy`. It refuses to write anything
unless the solids show that neighbouring tiles clear each other, that a tile
can be lowered onto its neighbours' rails, that the latches and dovetails
then hold it, and that the cover and the PCB fit.
