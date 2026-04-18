# Donjon Regen

Recreates [donjon.bin.sh](https://donjon.bin.sh/5e/dungeon/) dungeon generator output (GM map PNG, player map PNG, HTML) from the exported JSON file, using Python + Pillow.

## Project Structure

```
Donjon-Regen/           # Project root — all source lives here
  main.py               # CLI entry point
  cells.py              # Cell bitmask constants & helper functions
  renderer.py           # PIL-based map renderer (GM & player maps)
  html_gen.py           # HTML generator (embedded map, image map areas, detail tables)
  pyproject.toml
  CLAUDE.md
```

## Usage

```bash
python3 main.py <dungeon.json>
# e.g. python3 main.py test.json
```

Output files are named after the dungeon (from JSON `settings.name`) and placed next to the input JSON.

## Dependencies

- Python >= 3.14
- Pillow >= 12.0

## Cell Bitmask Encoding

The JSON `cells` array is a 2D grid where each value is a bitmask:

| Bit(s)   | Mask         | Meaning        |
|----------|--------------|----------------|
| 0        | 1            | Block          |
| 1        | 2            | Room           |
| 2        | 4            | Corridor       |
| 4        | 16           | Perimeter/Wall |
| 5        | 32           | Aperture       |
| 6-15     | 65472        | Room ID        |
| 16       | 65536        | Arch           |
| 17       | 131072       | Door           |
| 18       | 262144       | Locked         |
| 19       | 524288       | Trapped        |
| 20       | 1048576      | Secret         |
| 21       | 2097152      | Portcullis     |
| 22       | 4194304      | Stair Down     |
| 23       | 8388608      | Stair Up       |
| 24-31    | 4278190080   | Label char     |

- **Room ID**: `(cell & 65472) >> 6`
- **Label character**: `chr((cell & 4278190080) >> 24)` — used for room numbers and corridor feature markers

## Critical: Cells Array Offset

The `cells` array may be **larger** than `n_rows × n_cols` (e.g., 99×99 for a 91×91 map). It is padded symmetrically. All JSON metadata coordinates (rooms, doors, stairs, corridor features) use the **map coordinate system** (0 to n_rows-1), NOT the cells array indices.

To read cell data for map coordinate `(row, col)`:
```python
row_off = (len(cells) - n_rows) // 2
col_off = (len(cells[0]) - n_cols) // 2
cell = cells[row + row_off][col + col_off]
```

When no padding exists (cells size == n_rows × n_cols), offset is 0.

## Map Rendering Details

- **GM map**: `cell_size` from JSON settings (varies per dungeon), includes room number labels and corridor feature labels
- **Player map**: 50px per cell, no labels
- **Polygon rooms**: Regular N-sided polygon inscribed in a circle centered on the room's bounding box. Drawn as geometric white fills directly — cell data may only contain a subset of room cells.
- **Circle rooms**: PIL ellipse drawn directly as white fill from bounding box.
- **Doors**: Orientation determined from room door direction data (`north/south` → horizontal wall, `east/west` → vertical wall). Small rectangle outline for regular doors; dots for arches; dashed dots for portcullises; S glyph for secret doors.
- **Stairs**: Coordinates from JSON `stairs` array (in map coordinate space). Hatching for stair-up; progressive bars for stair-down.
- **Grid lines**: Must be drawn AFTER all white fills to avoid being overwritten.

## Reference Files

- `test.json` — Source dungeon data from donjon.bin.sh
- `test (91 x 91).png` — Reference GM map
- `test (player, 91 x 91).png` — Reference player map
- `test.html` — Reference HTML document
- `key.png` — Door/stair symbol legend assets
