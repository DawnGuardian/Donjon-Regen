"""Tabular grid export for donjon dungeons.

Emits a 2D grid of single-token codes matching donjon's reference TSV
format. The same grid is written through ``write_table`` with the
caller picking the delimiter (``\\t`` for TSV, ``,`` for CSV).

Cell codes
----------
- empty string  -- wall, perimeter, or void
- ``F``         -- floor (room or corridor); arches are emitted as ``F``
                   because they are visually indistinguishable from a
                   plain opening
- ``D{T,B,L,R}``  -- door (door / locked / trapped); the suffix is the
                     direction in which a room lies relative to the door
- ``DS{T,B,L,R}`` -- secret door
- ``DP{T,B,L,R}`` -- portcullis
- ``SU`` / ``SUU`` -- stair-up: ``SU`` on the cell in the stair's
                       facing direction (the visible symbol), ``SUU``
                       on the JSON's anchor cell
- ``SD`` / ``SDD`` -- stair-down (same convention)
"""

from pathlib import Path

import cells as C


_DIR_DELTAS = {
    "T": (-1, 0),
    "B": (1, 0),
    "L": (0, -1),
    "R": (0, 1),
}

_STAIR_DIR_DELTAS = {
    "north": (-1, 0),
    "south": (1, 0),
    "west":  (0, -1),
    "east":  (0, 1),
}


def _door_code(cells_arr, r, c):
    """Return the TSV code for a door cell, or ``F`` if it's an arch."""
    cell = cells_arr[r][c]
    door = C.door_type(cell)
    if door == "arch":
        return "F"

    orientation = C.door_orientation(cells_arr, r, c)
    if orientation == "horizontal":
        candidates = ("T", "B")
    else:
        candidates = ("L", "R")

    # Prefer the side whose neighbor is a room. If both or neither are
    # rooms, fall back to the first candidate (T for horizontal, L for
    # vertical) — matches the donjon reference.
    chosen = candidates[0]
    for d in candidates:
        dr, dc = _DIR_DELTAS[d]
        if C.is_room(C.get_cell(cells_arr, r + dr, c + dc)):
            chosen = d
            break

    if door == "secret":
        return "DS" + chosen
    if door == "portcullis":
        return "DP" + chosen
    return "D" + chosen


def build_grid(dungeon):
    """Build the 2D code grid for ``dungeon``. Returns ``list[list[str]]``."""
    settings = dungeon["settings"]
    n_rows = settings["n_rows"]
    n_cols = settings["n_cols"]
    cells_arr = dungeon["cells"]
    row_off = (len(cells_arr) - n_rows) // 2
    col_off = (len(cells_arr[0]) - n_cols) // 2

    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]

    for r in range(n_rows):
        for c in range(n_cols):
            cell = cells_arr[r + row_off][c + col_off]
            if C.has_door(cell):
                grid[r][c] = _door_code(cells_arr, r + row_off, c + col_off)
            elif C.is_open(cell):
                grid[r][c] = "F"
            # else: wall / void -> leave empty

    # Stairs override the floor codes on their two cells. The cell in the
    # stair's facing direction shows the symbol (SU/SD); the JSON anchor
    # cell shows the doubled code (SUU/SDD).
    for stair in dungeon.get("stairs", []):
        r = stair["row"]
        c = stair["col"]
        head = "SU" if stair["key"] == "up" else "SD"
        dr, dc = _STAIR_DIR_DELTAS[stair["dir"]]
        nr, nc = r + dr, c + dc
        if 0 <= r < n_rows and 0 <= c < n_cols:
            grid[r][c] = head + head[1]  # SUU / SDD
        if 0 <= nr < n_rows and 0 <= nc < n_cols:
            grid[nr][nc] = head

    return grid


def write_table(grid, path, delimiter):
    """Write ``grid`` to ``path`` using ``delimiter`` between fields.

    Rows are joined with ``\\n`` and the file has no trailing newline,
    matching donjon's reference TSV.
    """
    path = Path(path)
    lines = [delimiter.join(row) for row in grid]
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path
