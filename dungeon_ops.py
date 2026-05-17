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

def _room_by_id(d: dict, rid: int) -> dict | None:
    """Return the room dict with the given id, or None if it isn't present."""
    for r in d.get("rooms") or []:
        if r is not None and int(r["id"]) == int(rid):
            return r
    return None


# Brushed-cell → room-neighbour offsets and the room-relative wall a door at
# the brushed cell sits on. If the room cell is the *north* neighbour of the
# brushed cell, the brushed cell lies on that room's south wall, so the door
# is registered under doors["south"]. Scanned in this fixed order, so the
# first room found wins when a brushed cell borders more than one room.
_AUTO_DOOR_NEIGHBOURS = (
    (-1, 0, "south"),   # room above the brushed cell → room's south wall
    (1, 0, "north"),    # room below                  → room's north wall
    (0, -1, "east"),    # room to the left            → room's east wall
    (0, 1, "west"),     # room to the right           → room's west wall
)

# Generic descriptions for an auto-inserted door — the user can refine these
# in the Room editor afterwards.
_DEFAULT_DOOR_DESC = {
    "arch": "Archway",
    "door": "Unlocked Door",
    "locked": "Locked Door",
    "trapped": "Trapped Door",
    "secret": "Secret Door",
    "portcullis": "Portcullis",
}


def _auto_door_after_corridor(d: dict, row: int, col: int,
                              door_type: str) -> dict | None:
    """If the freshly-painted corridor cell at (row, col) borders a room,
    drop a door there: set the door-type bit on the cell and register the
    door in the adjacent room's `doors[direction]` list. Returns the new
    door dict (or the existing one if a door is already registered at this
    cell), else None when the cell borders no room.

    The first room found — scanning north, south, west, east — wins; at a
    1-cell-thick wall a single brushed cell can sit between two rooms, but
    one door symbol serves both sides either way."""
    for dr, dc, direction in _AUTO_DOOR_NEIGHBOURS:
        neighbour = get_cell(d, row + dr, col + dc)
        if not C.is_room(neighbour):
            continue
        room = _room_by_id(d, C.room_id(neighbour))
        if room is None:
            continue

        # Door cells in donjon's encoding are corridor floor with a door
        # overlay — keep CORRIDOR (and any label char), add the door bit.
        door_bit = C.door_type_bit(door_type) or C.DOOR
        set_cell(d, row, col, get_cell(d, row, col) | door_bit)

        wall = room.setdefault("doors", {}).setdefault(direction, [])
        for existing in wall:
            if existing.get("row") == row and existing.get("col") == col:
                return existing
        door = {
            "row": row,
            "col": col,
            "type": door_type,
            "desc": _DEFAULT_DOOR_DESC.get(door_type, door_type.capitalize()),
        }
        wall.append(door)
        return door
    return None


def paint_corridor(d: dict, row: int, col: int, *,
                   auto_door: bool = True, door_type: str = "door") -> bool:
    """Mark the cell at (row, col) as a corridor tile. Returns True if the
    cell was changed. Refuses to overwrite an existing room cell — corridors
    are meant to fill the space *between* rooms; if the user wants to remove
    a room cell first they need the eraser.

    Auto-door: when the brush turns a non-corridor cell into a *new* corridor
    tile and that cell borders a room, a door is inserted at the brushed cell
    (door-type bit on the cell + an entry in the room's `doors` list). Pass
    `auto_door=False` to suppress this, or `door_type` to choose the symbol
    (default `"door"`). Extending an existing corridor never triggers an
    auto-door — a cell that is already plain corridor floor short-circuits
    before the door check — and a cell that already carries a door bit is
    left for the user to edit rather than re-doored."""
    settings = d["settings"]
    if not (0 <= row < settings["n_rows"] and 0 <= col < settings["n_cols"]):
        return False
    cell = get_cell(d, row, col)
    if C.is_room(cell):
        return False
    if C.is_corridor(cell) and not (cell & (C.BLOCK | C.PERIMETER | C.DOOR_TYPES)):
        return False
    had_door = bool(cell & C.DOOR_TYPES)
    # Drop bits that conflict with "plain open corridor floor".
    keep_mask = C.LABEL  # keep any pre-existing label char (corridor feature)
    new_cell = (cell & keep_mask) | C.CORRIDOR
    set_cell(d, row, col, new_cell)
    if auto_door and not had_door:
        _auto_door_after_corridor(d, row, col, door_type)
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
# Room lifecycle
# ---------------------------------------------------------------------------

# Donjon stores widths/heights in "donjon units" — 10 per cell — and area in
# the same units squared. Match that convention so the rooms list stays
# byte-comparable to a freshly-generated dungeon.
_DONJON_CELL_UNITS = 10


def _bbox_room_id(d: dict, north: int, south: int, west: int, east: int) -> int | None:
    """If any cell in the inclusive bbox already has a ROOM bit, return that
    room's id (first one found). Otherwise None."""
    for r in range(north, south + 1):
        for c in range(west, east + 1):
            cell = get_cell(d, r, c)
            if C.is_room(cell):
                return C.room_id(cell)
    return None


def create_room(d: dict, north: int, south: int, west: int, east: int,
                *, shape: str = "square", polygon_n: int = 0) -> dict:
    """Add a new room covering the inclusive bbox. Returns the room dict.

    The renderer keys off cell bits for filled rectangles and off the
    `shape` + `polygon` fields of the room dict for polymorph rendering;
    we set both consistently.

    Validation:
      * bounds must lie inside the canvas;
      * no cell in the bbox may already have the ROOM bit (raises
        ValueError — the GUI catches this and surfaces it as a status
        message);
      * polygon / circle require a square bbox (renderer inscribes the
        shape in min(width, height) which assumes square);
      * polygon requires `polygon_n >= 3`.

    Shape normalization: donjon uses `shape: "square"` for *any* axis-aligned
    rectangle (square or not). We follow that convention so saved JSON
    matches a freshly-generated dungeon — a caller passing `shape="rectangle"`
    gets `"square"` written to the dict.
    """
    settings = d["settings"]
    n_rows = settings["n_rows"]
    n_cols = settings["n_cols"]

    if not (0 <= north <= south < n_rows and 0 <= west <= east < n_cols):
        raise ValueError(
            f"Room bounds out of canvas (rows {north}-{south}, "
            f"cols {west}-{east}, canvas is {n_rows}×{n_cols})."
        )

    existing_id = _bbox_room_id(d, north, south, west, east)
    if existing_id is not None:
        raise ValueError(
            f"Bbox overlaps existing room id {existing_id}; "
            "erase or shrink it first."
        )

    cells_wide = east - west + 1
    cells_tall = south - north + 1
    if shape in ("polygon", "circle") and cells_wide != cells_tall:
        raise ValueError(
            f"Polymorph rooms (polygon/circle) require a square bbox; "
            f"got {cells_wide}×{cells_tall}."
        )
    if shape == "polygon" and polygon_n < 3:
        raise ValueError(
            f"Polygon rooms need at least 3 sides; got {polygon_n}."
        )

    stored_shape = "square" if shape == "rectangle" else shape

    rooms = d.setdefault("rooms", [])
    used_ids = [int(r["id"]) for r in rooms if r is not None]
    next_id = max(
        int(settings.get("last_room_id", 0)),
        max(used_ids) if used_ids else 0,
    ) + 1

    while len(rooms) <= next_id:
        rooms.append(None)

    room: dict = {
        "id": str(next_id),
        "north": north,
        "south": south,
        "west": west,
        "east": east,
        "row": north,
        "col": west,
        "width": cells_wide * _DONJON_CELL_UNITS,
        "height": cells_tall * _DONJON_CELL_UNITS,
        "area": cells_wide * cells_tall * (_DONJON_CELL_UNITS ** 2),
        "shape": stored_shape,
        "size": "",
        "doors": {},
        "contents": {},
    }
    if stored_shape == "polygon":
        room["polygon"] = polygon_n

    rooms[next_id] = room

    room_id_bits = next_id << 6
    for r in range(north, south + 1):
        for c in range(west, east + 1):
            set_cell(d, r, c, C.ROOM | room_id_bits)

    settings["last_room_id"] = next_id
    settings["n_rooms"] = sum(1 for x in rooms if x is not None)

    return room


def reshape_room(room: dict, new_shape: str, polygon_n: int = 0) -> bool:
    """Switch an existing room's shape (rectangle / polygon / circle).

    Doesn't touch cells — every cell in the bbox keeps `ROOM | room_id` either
    way; the renderer decides polygon / circle / fill from the room dict's
    `shape` (and `polygon`) fields. Validates that polymorph shapes require
    a square bbox and that polygon needs N ≥ 3. Returns True if the room
    dict actually changed."""
    cells_wide = room["east"] - room["west"] + 1
    cells_tall = room["south"] - room["north"] + 1
    stored_shape = "square" if new_shape == "rectangle" else new_shape

    if stored_shape in ("polygon", "circle") and cells_wide != cells_tall:
        raise ValueError(
            f"Polymorph rooms require a square bbox; "
            f"current bbox is {cells_wide}×{cells_tall} — resize first."
        )
    if stored_shape == "polygon" and polygon_n < 3:
        raise ValueError(
            f"Polygon rooms need at least 3 sides; got {polygon_n}."
        )

    old_shape = room.get("shape")
    old_polygon = room.get("polygon")
    new_polygon = polygon_n if stored_shape == "polygon" else None

    if old_shape == stored_shape and old_polygon == new_polygon:
        return False

    room["shape"] = stored_shape
    if stored_shape == "polygon":
        room["polygon"] = polygon_n
    else:
        room.pop("polygon", None)
    return True


def resize_room(d: dict, room: dict, dn: int, ds: int, de: int, dw: int) -> bool:
    """Grow / shrink a room's bbox by the per-edge cell deltas.

    Positive deltas extend that edge outward; negative deltas pull it
    inward. Validates that the new bbox stays in the canvas, has area ≥ 1,
    doesn't overlap any other room, and (for polymorph rooms) stays square.

    On success: cells outside the new bbox that belonged to this room are
    cleared; cells now inside the bbox get `ROOM | room_id` (overwriting
    whatever was there — corridor / door / block / label). Room metadata
    (north/south/east/west/row/col/width/height/area) is updated to match.
    Doors that fall outside the new bbox are NOT auto-relocated — the caller
    can edit the doors section to clean up.
    """
    if (dn, ds, de, dw) == (0, 0, 0, 0):
        return False

    settings = d["settings"]
    n_rows = settings["n_rows"]
    n_cols = settings["n_cols"]
    rid = int(room["id"])
    rid_bits = rid << 6

    new_north = room["north"] - dn
    new_south = room["south"] + ds
    new_west = room["west"] - dw
    new_east = room["east"] + de

    if not (0 <= new_north <= new_south < n_rows and 0 <= new_west <= new_east < n_cols):
        raise ValueError(
            f"Resize would take room {rid} out of canvas "
            f"(rows {new_north}-{new_south}, cols {new_west}-{new_east})."
        )

    new_wide = new_east - new_west + 1
    new_tall = new_south - new_north + 1
    if room.get("shape") in ("polygon", "circle") and new_wide != new_tall:
        raise ValueError(
            f"Polymorph room must remain square; "
            f"new bbox would be {new_wide}×{new_tall}."
        )

    # Overlap check — any other room's cell falling inside our new bbox blocks
    # the resize. We check before mutating anything.
    for r in range(new_north, new_south + 1):
        for c in range(new_west, new_east + 1):
            cell = get_cell(d, r, c)
            if C.is_room(cell) and C.room_id(cell) != rid:
                raise ValueError(
                    f"Resize would overlap room id {C.room_id(cell)} "
                    f"at ({r},{c}); shrink or delete that room first."
                )

    old_n = room["north"]
    old_s = room["south"]
    old_w = room["west"]
    old_e = room["east"]

    # Clear cells that were in the OLD bbox but won't be in the new one.
    for r in range(old_n, old_s + 1):
        for c in range(old_w, old_e + 1):
            if new_north <= r <= new_south and new_west <= c <= new_east:
                continue
            cell = get_cell(d, r, c)
            if C.is_room(cell) and C.room_id(cell) == rid:
                set_cell(d, r, c, C.NOTHING)

    # Paint cells that are in the new bbox but weren't in the old one.
    for r in range(new_north, new_south + 1):
        for c in range(new_west, new_east + 1):
            if old_n <= r <= old_s and old_w <= c <= old_e:
                continue
            set_cell(d, r, c, C.ROOM | rid_bits)

    room["north"] = new_north
    room["south"] = new_south
    room["west"] = new_west
    room["east"] = new_east
    room["row"] = new_north
    room["col"] = new_west
    room["width"] = new_wide * _DONJON_CELL_UNITS
    room["height"] = new_tall * _DONJON_CELL_UNITS
    room["area"] = new_wide * new_tall * (_DONJON_CELL_UNITS ** 2)
    return True


def delete_room(d: dict, room_id: int) -> bool:
    """Remove a room and clear its cells. Returns True if a room was removed.

    Drops the room dict from `rooms` (replaced with None to preserve the
    1-indexed convention), clears every cell inside the bbox that carries
    this room's id, and strips the door-type bits from each of the room's
    own door cells (CORRIDOR / label bits on those cells are kept — a door
    cell in donjon's encoding is a corridor floor with a door overlay, so
    removing only the door bits leaves a usable corridor in place)."""
    rooms = d.get("rooms") or []
    target = None
    target_idx = None
    for i, r in enumerate(rooms):
        if r is not None and int(r["id"]) == int(room_id):
            target = r
            target_idx = i
            break
    if target is None:
        return False

    for r in range(target["north"], target["south"] + 1):
        for c in range(target["west"], target["east"] + 1):
            cell = get_cell(d, r, c)
            if C.is_room(cell) and C.room_id(cell) == int(room_id):
                set_cell(d, r, c, C.NOTHING)

    for door_list in (target.get("doors") or {}).values():
        for door in door_list:
            cell = get_cell(d, door["row"], door["col"])
            set_cell(d, door["row"], door["col"], cell & ~C.DOOR_TYPES)

    rooms[target_idx] = None
    settings = d["settings"]
    settings["n_rooms"] = sum(1 for x in rooms if x is not None)
    return True


# ---------------------------------------------------------------------------
# Mirror rooms (one-shot, overwrite)
# ---------------------------------------------------------------------------

_OPPOSITE_DIRECTION = {
    "north": "south", "south": "north",
    "east": "west", "west": "east",
}


def mirror_rooms(d: dict, axis: str, pivot: int, source_side: str) -> dict:
    """Mirror rooms across an axis. Overwrite mode: any room on the
    destination side that overlaps a mirrored bbox is deleted first.

    Args:
        axis: ``"horizontal"`` mirrors rows around `pivot` (a row index);
            ``"vertical"`` mirrors cols around `pivot` (a col index).
        pivot: integer index of the mirror line. Rooms straddling the pivot
            (i.e., on both sides) are excluded from mirroring — there's no
            meaningful one-sided source for them.
        source_side: which side is the source. Horizontal axis takes
            ``"north"`` or ``"south"``; vertical axis takes ``"west"`` or
            ``"east"``. Source rooms are those strictly on that side of the
            pivot.

    Corridors and stairs are NOT mirrored. The source rooms' cells stay in
    place; the destination side receives mirrored copies (cells, doors,
    contents) with new room ids.

    Returns ``{"created_room_ids": [...], "deleted_room_ids": [...],
    "skipped_off_canvas": [...]}``. `skipped_off_canvas` lists source-room
    ids whose mirrored bbox would fall outside the canvas — those are
    silently skipped rather than failing the whole op.
    """
    if axis not in ("horizontal", "vertical"):
        raise ValueError(f"Unknown axis: {axis!r}")
    if axis == "horizontal" and source_side not in ("north", "south"):
        raise ValueError("Horizontal axis requires source_side north/south.")
    if axis == "vertical" and source_side not in ("west", "east"):
        raise ValueError("Vertical axis requires source_side west/east.")

    settings = d["settings"]
    n_rows = settings["n_rows"]
    n_cols = settings["n_cols"]
    if axis == "horizontal" and not (0 <= pivot < n_rows):
        raise ValueError(f"Pivot row {pivot} out of canvas (0..{n_rows - 1}).")
    if axis == "vertical" and not (0 <= pivot < n_cols):
        raise ValueError(f"Pivot col {pivot} out of canvas (0..{n_cols - 1}).")

    def _is_source(room: dict) -> bool:
        if axis == "horizontal":
            return (
                room["south"] < pivot if source_side == "north"
                else room["north"] > pivot
            )
        return (
            room["east"] < pivot if source_side == "west"
            else room["west"] > pivot
        )

    def _mirror_bbox(room: dict) -> tuple[int, int, int, int]:
        if axis == "horizontal":
            return (2 * pivot - room["south"], 2 * pivot - room["north"],
                    room["west"], room["east"])
        return (room["north"], room["south"],
                2 * pivot - room["east"], 2 * pivot - room["west"])

    def _mirror_rc(row: int, col: int) -> tuple[int, int]:
        if axis == "horizontal":
            return 2 * pivot - row, col
        return row, 2 * pivot - col

    def _mirror_direction(direction: str) -> str:
        if axis == "horizontal" and direction in ("north", "south"):
            return _OPPOSITE_DIRECTION[direction]
        if axis == "vertical" and direction in ("east", "west"):
            return _OPPOSITE_DIRECTION[direction]
        return direction

    rooms = d.get("rooms") or []
    source_rooms = [r for r in rooms if r is not None and _is_source(r)]

    # Plan: compute every mirrored bbox up front, dropping ones that would
    # fall off the canvas. Then identify destination rooms to delete (those
    # overlapping any mirrored bbox AND not themselves a source room we're
    # about to copy from).
    planned: list[tuple[dict, tuple[int, int, int, int]]] = []
    skipped: list[str] = []
    for src in source_rooms:
        mn, ms, mw, me = _mirror_bbox(src)
        if not (0 <= mn <= ms < n_rows and 0 <= mw <= me < n_cols):
            skipped.append(src.get("id", "?"))
            continue
        planned.append((src, (mn, ms, mw, me)))

    source_ids = {int(s["id"]) for s in source_rooms}
    to_delete_ids: set[int] = set()
    for _src, (mn, ms, mw, me) in planned:
        for room in rooms:
            if room is None:
                continue
            rid = int(room["id"])
            if rid in source_ids or rid in to_delete_ids:
                continue
            if room["north"] > ms or room["south"] < mn:
                continue
            if room["west"] > me or room["east"] < mw:
                continue
            to_delete_ids.add(rid)

    for rid in sorted(to_delete_ids):
        delete_room(d, rid)

    created_ids: list[int] = []
    for src, (mn, ms, mw, me) in planned:
        new_room = create_room(
            d, mn, ms, mw, me,
            shape=src.get("shape", "square"),
            polygon_n=int(src.get("polygon", 0) or 0),
        )
        new_room["size"] = src.get("size", "")
        # Deep-copy mutable substructures so editing one room's contents
        # later doesn't bleed into its mirror.
        import copy
        new_room["contents"] = copy.deepcopy(src.get("contents") or {})

        new_doors: dict[str, list[dict]] = {}
        for direction, door_list in (src.get("doors") or {}).items():
            mirrored_dir = _mirror_direction(direction)
            for door in door_list:
                mr, mc = _mirror_rc(door["row"], door["col"])
                if not (0 <= mr < n_rows and 0 <= mc < n_cols):
                    continue  # door would fall off canvas; skip
                new_door = copy.deepcopy(door)
                new_door["row"] = mr
                new_door["col"] = mc
                new_doors.setdefault(mirrored_dir, []).append(new_door)

                # Paint the door cell — donjon door cells carry CORRIDOR
                # plus the door-type bit (no ROOM, no PERIMETER).
                cell = get_cell(d, mr, mc)
                door_bit = C.door_type_bit(door.get("type", "door"))
                cell = (cell & C.LABEL) | C.CORRIDOR | door_bit
                set_cell(d, mr, mc, cell)
        new_room["doors"] = new_doors
        created_ids.append(int(new_room["id"]))

    return {
        "created_room_ids": created_ids,
        "deleted_room_ids": sorted(to_delete_ids),
        "skipped_off_canvas": skipped,
    }


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
