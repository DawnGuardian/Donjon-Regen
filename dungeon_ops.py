"""Pure mutations on a loaded donjon dungeon dict.

Functions here change the in-memory dict (and the `cells` 2D array) so the
GUI / scripts can edit a dungeon without having to know the bit layout.
Each operation keeps the dungeon's invariants (cells ↔ rooms metadata,
settings.n_rows / n_cols, padding) consistent so the next render is correct.

Coordinate convention: every public function takes and returns *map*
coordinates (0 .. n_rows-1, 0 .. n_cols-1). Padding around the cells
array is handled internally and normalised away after each op — after any
call here, `len(cells) == n_rows` and `len(cells[0]) == n_cols` exactly.
"""

from __future__ import annotations

import cells as C


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def cells_offset(d: dict) -> tuple[int, int]:
    """Padding offset between cells array indices and map coordinates.

    `cells[row + row_off][col + col_off]` reads the cell at map (row, col).
    """
    cells = d["cells"]
    n_rows = d["settings"]["n_rows"]
    n_cols = d["settings"]["n_cols"]
    return (len(cells) - n_rows) // 2, (len(cells[0]) - n_cols) // 2


def get_cell(d: dict, row: int, col: int) -> int:
    """Read the cell at map (row, col) accounting for current padding."""
    row_off, col_off = cells_offset(d)
    cells = d["cells"]
    r, c = row + row_off, col + col_off
    if 0 <= r < len(cells) and 0 <= c < len(cells[0]):
        return cells[r][c]
    return 0


def set_cell(d: dict, row: int, col: int, value: int) -> None:
    """Write `value` to the cell at map (row, col)."""
    row_off, col_off = cells_offset(d)
    cells = d["cells"]
    r, c = row + row_off, col + col_off
    if 0 <= r < len(cells) and 0 <= c < len(cells[0]):
        cells[r][c] = value


def normalise_cells(d: dict) -> None:
    """Strip any padding so `len(cells) == n_rows`, `len(cells[0]) == n_cols`.
    Used after operations that rebuild the 2D array; downstream code can then
    rely on `cells[row][col]` being the same as map coordinates."""
    row_off, col_off = cells_offset(d)
    if row_off == 0 and col_off == 0:
        return
    cells = d["cells"]
    n_rows = d["settings"]["n_rows"]
    n_cols = d["settings"]["n_cols"]
    d["cells"] = [
        [cells[r + row_off][c + col_off] for c in range(n_cols)]
        for r in range(n_rows)
    ]


# ---------------------------------------------------------------------------
# Canvas resize
# ---------------------------------------------------------------------------

class Canvas_Resize_Conflict(Exception):
    """Raised by `resize_canvas` when a shrink would discard content and the
    caller didn't pass `force=True`. Carries the lists of victims so a GUI
    can present a confirmation dialog."""

    def __init__(self, dropped_rooms: list, dropped_stairs: list):
        self.dropped_rooms = dropped_rooms
        self.dropped_stairs = dropped_stairs
        names = []
        if dropped_rooms:
            ids = ", ".join(str(r.get("id", "?")) for r in dropped_rooms)
            names.append(f"rooms: {ids}")
        if dropped_stairs:
            names.append(f"{len(dropped_stairs)} stair(s)")
        super().__init__(
            "Shrink would discard " + "; ".join(names) +
            " — pass force=True to confirm."
        )


# ---------------------------------------------------------------------------
# Single-cell brush ops (corridor brush, eraser)
# ---------------------------------------------------------------------------

def paint_corridor(d: dict, row: int, col: int) -> bool:
    """Mark the cell at (row, col) as a corridor tile. Returns True if the
    cell was changed. Refuses to overwrite an existing room cell — corridors
    are meant to fill the space *between* rooms; if the user wants to remove
    a room cell first they need the eraser."""
    settings = d["settings"]
    if not (0 <= row < settings["n_rows"] and 0 <= col < settings["n_cols"]):
        return False
    cell = get_cell(d, row, col)
    if C.is_room(cell):
        return False
    if C.is_corridor(cell) and not (cell & (C.BLOCK | C.PERIMETER | C.DOOR_TYPES)):
        return False
    # Drop bits that conflict with "plain open corridor floor".
    keep_mask = C.LABEL  # keep any pre-existing label char (corridor feature)
    new_cell = (cell & keep_mask) | C.CORRIDOR
    set_cell(d, row, col, new_cell)
    return True


def erase_cell(d: dict, row: int, col: int) -> bool:
    """Clear the cell at (row, col): drops corridor / door / room / block /
    perimeter / label bits, leaving it as raw void. Room metadata is *not*
    updated — if you erase cells out of a room's interior the bbox in the
    room dict becomes inconsistent with the rendered fill. Use a dedicated
    `delete_room` op for full removal."""
    settings = d["settings"]
    if not (0 <= row < settings["n_rows"] and 0 <= col < settings["n_cols"]):
        return False
    cell = get_cell(d, row, col)
    if cell == C.NOTHING:
        return False
    set_cell(d, row, col, C.NOTHING)
    return True


# ---------------------------------------------------------------------------
# Canvas resize
# ---------------------------------------------------------------------------

def resize_canvas(d: dict, n_add: int, s_add: int, e_add: int, w_add: int,
                  *, force: bool = False) -> dict:
    """Grow or shrink the dungeon canvas by the per-edge cell deltas.

    Positive deltas grow that edge; negative deltas shrink it. The
    coordinate system shifts so existing content stays anchored to its
    original grid square: e.g., `n_add=5` pushes every row index +5 because
    five new empty rows are inserted on the north side.

    Shrinking (negative deltas) may render some rooms / stairs out of bounds.
    Such content is identified before mutating anything; if `force` is False
    a `Canvas_Resize_Conflict` is raised so the caller can confirm. With
    `force=True` (or no conflicts) the resize proceeds and the dropped
    items are returned in `{'dropped_rooms': [...], 'dropped_stairs': [...]}`.
    """
    settings = d["settings"]
    n_rows = settings["n_rows"]
    n_cols = settings["n_cols"]
    new_n_rows = n_rows + n_add + s_add
    new_n_cols = n_cols + e_add + w_add
    if new_n_rows < 1 or new_n_cols < 1:
        raise ValueError("Resulting canvas must be at least 1×1.")

    shift_r = n_add
    shift_c = w_add

    def _fits(r: int, c: int) -> bool:
        return 0 <= r < new_n_rows and 0 <= c < new_n_cols

    # Pre-flight: identify content that wouldn't fit.
    dropped_rooms: list = []
    for room in d.get("rooms") or []:
        if room is None:
            continue
        new_n = room["north"] + shift_r
        new_s = room["south"] + shift_r
        new_w = room["west"] + shift_c
        new_e = room["east"] + shift_c
        if not (_fits(new_n, new_w) and _fits(new_s, new_e)):
            dropped_rooms.append(room)

    dropped_stairs: list = []
    for stair in d.get("stairs") or []:
        new_r = stair["row"] + shift_r
        new_c = stair["col"] + shift_c
        if not _fits(new_r, new_c):
            dropped_stairs.append(stair)

    if (dropped_rooms or dropped_stairs) and not force:
        raise Canvas_Resize_Conflict(dropped_rooms, dropped_stairs)

    # Build the new cells array (no padding — we always normalise on resize).
    row_off, col_off = cells_offset(d)
    old_cells = d["cells"]
    new_cells = [[C.NOTHING] * new_n_cols for _ in range(new_n_rows)]
    for old_r in range(n_rows):
        new_r = old_r + shift_r
        if not (0 <= new_r < new_n_rows):
            continue
        for old_c in range(n_cols):
            new_c = old_c + shift_c
            if not (0 <= new_c < new_n_cols):
                continue
            new_cells[new_r][new_c] = old_cells[old_r + row_off][old_c + col_off]

    # Zero out any cells that belonged to dropped rooms (paranoia — they
    # should already be out of bounds, but partial overlap could leak).
    drop_room_ids = {int(r["id"]) for r in dropped_rooms}
    if drop_room_ids:
        for r in range(new_n_rows):
            for c in range(new_n_cols):
                cell = new_cells[r][c]
                if C.is_room(cell) and C.room_id(cell) in drop_room_ids:
                    new_cells[r][c] = C.NOTHING

    # Shift coordinates on every surviving room, stair, door, mark, egress.
    new_rooms: list = []
    for room in d.get("rooms") or []:
        if room is None:
            new_rooms.append(None)
            continue
        if room in dropped_rooms:
            new_rooms.append(None)
            continue
        room["north"] += shift_r
        room["south"] += shift_r
        room["row"] += shift_r
        room["west"] += shift_c
        room["east"] += shift_c
        room["col"] += shift_c
        for door_list in (room.get("doors") or {}).values():
            for door in door_list:
                door["row"] += shift_r
                door["col"] += shift_c
        new_rooms.append(room)
    d["rooms"] = new_rooms

    new_stairs: list = []
    for stair in d.get("stairs") or []:
        if stair in dropped_stairs:
            continue
        stair["row"] += shift_r
        stair["col"] += shift_c
        new_stairs.append(stair)
    d["stairs"] = new_stairs

    for feat in (d.get("corridor_features") or {}).values():
        marks = feat.get("marks") or []
        kept = []
        for mark in marks:
            new_r = mark["row"] + shift_r
            new_c = mark["col"] + shift_c
            if _fits(new_r, new_c):
                mark["row"] = new_r
                mark["col"] = new_c
                kept.append(mark)
        if "marks" in feat:
            feat["marks"] = kept

    egress = d.get("egress") or []
    kept_egress = []
    for eg in egress:
        new_r = eg.get("row", 0) + shift_r
        new_c = eg.get("col", 0) + shift_c
        if _fits(new_r, new_c):
            eg["row"] = new_r
            eg["col"] = new_c
            kept_egress.append(eg)
    if isinstance(d.get("egress"), list):
        d["egress"] = kept_egress

    settings["n_rows"] = new_n_rows
    settings["n_cols"] = new_n_cols
    settings["max_row"] = new_n_rows - 1
    settings["max_col"] = new_n_cols - 1
    settings["n_rooms"] = sum(1 for r in d["rooms"] if r is not None)

    d["cells"] = new_cells

    return {"dropped_rooms": dropped_rooms, "dropped_stairs": dropped_stairs}
