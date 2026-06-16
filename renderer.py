"""Map renderer - generates GM and player map PNGs from dungeon JSON data."""

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

import cells as C
from gen_assets import door_symbol_rgba

ASSETS_DIR = Path(__file__).parent / "assets"

# Default render-resolution multiplier applied on top of the JSON's cell_size
# (and the player map's base 50px). The doubled grid gives finer polygon edges,
# better-scaled door symbols, and crisper labels at the cost of a 4× pixel area.
RENDER_SCALE = 2

# Colors
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRID_COLOR = (204, 204, 204)
WALL_COLOR = (51, 51, 51)
STAIR_COLOR = (0, 0, 0)
LABEL_COLOR = (0, 0, 0)
DOOR_FILL = (255, 255, 255)
DOOR_OUTLINE = (0, 0, 0)
GRAY = (160, 160, 160)


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _compute_offset(dungeon):
    """Compute the row/col offset between the cells array and map coordinates."""
    cell_data = dungeon["cells"]
    n_rows = dungeon["settings"]["n_rows"]
    n_cols = dungeon["settings"]["n_cols"]
    row_off = (len(cell_data) - n_rows) // 2
    col_off = (len(cell_data[0]) - n_cols) // 2
    return row_off, col_off


def _cell(cell_data, map_row, map_col, row_off, col_off):
    """Read a cell value using map coordinates (with offset applied)."""
    r = map_row + row_off
    c = map_col + col_off
    if 0 <= r < len(cell_data) and 0 <= c < len(cell_data[0]):
        return cell_data[r][c]
    return 0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def room_polygon_vertices(room, cell_size):
    """Compute float polygon vertices (sub-pixel precision) for a shaped room."""
    n_sides = room.get("polygon", 0)
    if n_sides < 3:
        return None

    west, east = room["west"], room["east"]
    north, south = room["north"], room["south"]

    x1 = west * cell_size
    y1 = north * cell_size
    x2 = (east + 1) * cell_size
    y2 = (south + 1) * cell_size

    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    radius = min(x2 - x1, y2 - y1) / 2

    vertices = []
    for k in range(n_sides):
        angle = -math.pi / 2 + 2 * math.pi * k / n_sides
        vx = cx + radius * math.cos(angle)
        vy = cy + radius * math.sin(angle)
        vertices.append((vx, vy))
    return vertices


def room_circle_params(room, cell_size):
    """Compute circle center and radius for a circular room."""
    west, east = room["west"], room["east"]
    north, south = room["north"], room["south"]

    x1 = west * cell_size
    y1 = north * cell_size
    x2 = (east + 1) * cell_size
    y2 = (south + 1) * cell_size

    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    radius = min(x2 - x1, y2 - y1) / 2
    return cx, cy, radius


def _build_polymorph_set(rooms):
    """Return a set of room IDs that are polymorph (polygon or circle)."""
    ids = set()
    for room in rooms:
        if room is None:
            continue
        if room.get("shape") in ("polygon", "circle"):
            ids.add(int(room["id"]))
    return ids


def _build_door_orient_map(dungeon):
    """Build a map of (row, col) -> orientation from room door data."""
    orient_map = {}
    for room in dungeon.get("rooms", []):
        if room is None:
            continue
        for direction, door_list in room.get("doors", {}).items():
            orient = "horizontal" if direction in ("north", "south") else "vertical"
            for door in door_list:
                orient_map[(door["row"], door["col"])] = orient
    return orient_map


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_map(dungeon, cell_size=None, gm_mode=True):
    """Render the dungeon map as a PIL Image.

    Rendering pipeline:
      Phase 1 — Rooms: fill square rooms from cells; draw geometric shapes
                for polymorph rooms.
      Phase 2 — Corridors: fill corridor cells white. Where a corridor/door
                cell is adjacent to a room, also fill that room cell white
                (corridor invasion) so connections are never cut off.
      Phase 3 — Grid & perimeter: draw gray grid lines between open cells,
                wall-color lines at open/closed boundaries, and smooth
                geometric outlines for polymorph rooms.
      Phase 4 — Doors, stairs, labels.
    """
    settings = dungeon["settings"]
    if cell_size is None:
        cell_size = settings["cell_size"]

    cell_data = dungeon["cells"]
    n_rows = settings["n_rows"]
    n_cols = settings["n_cols"]
    rooms = dungeon.get("rooms", [])
    stairs = dungeon.get("stairs", [])
    row_off, col_off = _compute_offset(dungeon)

    width = n_cols * cell_size + 1
    height = n_rows * cell_size + 1

    img = Image.new("RGB", (width, height), BLACK)
    draw = ImageDraw.Draw(img)

    door_orient_map = _build_door_orient_map(dungeon)
    polymorph_ids = _build_polymorph_set(rooms)

    # Phase 1: Rooms
    _fill_rooms(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off,
                polymorph_ids)
    _fill_polymorph_rooms(img, rooms, cell_size)

    # Phase 2: Corridors (with directional invasion into polymorph rooms)
    _fill_corridors(draw, img, cell_data, n_rows, n_cols, cell_size, row_off,
                    col_off, dungeon)

    # Phase 3: Grid lines (polymorph perimeter is the natural sharp edge
    # of the binary white fill — no explicit perimeter outline needed).
    _draw_grid(draw, img, n_rows, n_cols, cell_size, rooms)

    # Phase 4: Doors, stairs, labels
    _draw_doors_on_img(img, cell_data, n_rows, n_cols, cell_size, row_off, col_off,
                       door_orient_map)
    _draw_stairs(draw, stairs, cell_data, cell_size, row_off, col_off)
    if gm_mode:
        _draw_labels(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off)

    return img


# ---------------------------------------------------------------------------
# Phase 1: Room fills
# ---------------------------------------------------------------------------

def _fill_rooms(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off,
                polymorph_ids):
    """Fill square/rectangular room cells white. Skip polymorph rooms."""
    for row in range(n_rows):
        for col in range(n_cols):
            cell = _cell(cell_data, row, col, row_off, col_off)
            if not C.is_room(cell):
                continue
            rid = C.room_id(cell)
            if rid in polymorph_ids:
                continue
            x = col * cell_size
            y = row * cell_size
            draw.rectangle([x, y, x + cell_size, y + cell_size], fill=WHITE)


def _fill_polymorph_rooms(img, rooms, cell_size):
    """Draw polymorph rooms as binary white fills with sharp edges.

    PIL's polygon/ellipse rasterizer paints whole pixels: every pixel center
    inside the geometric shape becomes pure WHITE, every pixel center outside
    stays as the existing background. The boundary is a single-pixel step —
    no anti-aliased gradient, no soft edge — which gives the polygon a crisp
    visible perimeter against the black background.
    """
    width, height = img.size
    mask = Image.new("L", (width, height), 0)
    draw_mask = ImageDraw.Draw(mask)

    drew_anything = False
    for room in rooms:
        if room is None:
            continue
        shape = room.get("shape")
        if shape == "polygon":
            vertices = room_polygon_vertices(room, cell_size)
            if vertices:
                draw_mask.polygon(vertices, fill=255)
                drew_anything = True
        elif shape == "circle":
            cx, cy, radius = room_circle_params(room, cell_size)
            draw_mask.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=255,
            )
            drew_anything = True

    if not drew_anything:
        return

    white_layer = Image.new("RGB", (width, height), WHITE)
    img.paste(white_layer, (0, 0), mask)


# ---------------------------------------------------------------------------
# Phase 2: Corridor fills (with invasion)
# ---------------------------------------------------------------------------

def _fill_corridors(draw, img, cell_data, n_rows, n_cols, cell_size, row_off,
                    col_off, dungeon):
    """Fill corridor cells white, then walk into adjacent polymorph rooms.

    When a door connects a corridor to a polymorph room, the corridor's
    white fill walks in a straight line from the door into the room (in
    the door's inward direction) until it reaches cells already rendered
    white by the geometric shape fill. This bridges the gap without
    spreading laterally around polygon corners.
    """
    # Step 1: Fill all corridor cells white
    for row in range(n_rows):
        for col in range(n_cols):
            cell = _cell(cell_data, row, col, row_off, col_off)
            if C.is_corridor(cell):
                x = col * cell_size
                y = row * cell_size
                draw.rectangle([x, y, x + cell_size, y + cell_size], fill=WHITE)

    # Step 2: For each door into a polymorph room, walk inward filling
    # room cells until we reach a cell already white from the shape fill.
    polymorph_ids = _build_polymorph_set(dungeon.get("rooms", []))

    # Direction vectors: door direction -> step into the room
    inward_step = {
        "north": (-1, 0),  # door is on north wall, room is to the south...
        "south": (1, 0),   # wait, door direction = which wall of the room
        "east": (0, 1),    # the door is on the room's east wall
        "west": (0, -1),   # the door is on the room's west wall
    }
    # Actually: a "north" door is on the room's north wall. The door cell
    # is OUTSIDE the room (one row north of the room). Walking INTO the
    # room means going south (+1, 0). Correcting:
    inward_step = {
        "north": (1, 0),   # door on north wall → walk south into room
        "south": (-1, 0),  # door on south wall → walk north into room
        "east": (0, -1),   # door on east wall → walk west into room
        "west": (0, 1),    # door on west wall → walk east into room
    }

    def _is_white(r, c):
        px = c * cell_size + cell_size // 2
        py = r * cell_size + cell_size // 2
        if 0 <= px < img.width and 0 <= py < img.height:
            return img.getpixel((px, py))[0] > 128
        return False

    for room in dungeon.get("rooms", []):
        if room is None:
            continue
        rid = int(room["id"])
        if rid not in polymorph_ids:
            continue

        for direction, door_list in room.get("doors", {}).items():
            dr, dc = inward_step[direction]
            for door in door_list:
                # Start from the door cell and walk inward
                r, c = door["row"], door["col"]
                # Step into the room
                r, c = r + dr, c + dc
                # Always fill the first room cell adjacent to the door so
                # the door tab bridges cleanly into the polygon, even when
                # that cell is already inside the polygon shape.
                first = True
                while 0 <= r < n_rows and 0 <= c < n_cols:
                    if not first and _is_white(r, c):
                        # Reached the geometric fill — done
                        break
                    cell = _cell(cell_data, r, c, row_off, col_off)
                    if not C.is_room(cell):
                        break
                    # Fill this gap cell
                    x = c * cell_size
                    y = r * cell_size
                    draw.rectangle(
                        [x, y, x + cell_size, y + cell_size], fill=WHITE
                    )
                    r, c = r + dr, c + dc
                    first = False


# ---------------------------------------------------------------------------
# Phase 3: Grid lines and polymorph outlines
# ---------------------------------------------------------------------------

def _draw_grid(draw, img, n_rows, n_cols, cell_size, rooms):
    """Draw grid lines based on rendered pixel state.

    GRID_COLOR is drawn on every cell-edge that touches an open cell — both
    open/open and open/closed boundaries. Walls are simply the black
    background showing through where no open cell exists on either side.

    For polymorph rooms: Pass 2 redraws every internal bbox grid edge and
    then reverts any grid pixel where the pre-grid snapshot was non-WHITE,
    so grid lines only survive over the polygon's filled interior (and over
    connector cells that the corridor invasion filled white before Pass 2's
    snapshot was taken).
    """
    polymorph_cells = set()
    polymorph_rooms = []
    for room in rooms:
        if room is None:
            continue
        if room.get("shape") not in ("polygon", "circle"):
            continue
        polymorph_rooms.append(room)
        for r in range(room["north"], room["south"] + 1):
            for c in range(room["west"], room["east"] + 1):
                polymorph_cells.add((r, c))

    def _pixel_is_open(r, c):
        px = c * cell_size + cell_size // 2
        py = r * cell_size + cell_size // 2
        if 0 <= px < img.width and 0 <= py < img.height:
            return img.getpixel((px, py))[0] > 128
        return False

    # Snapshot polymorph bboxes BEFORE Pass 1 — Pass 2's mask needs the clean
    # polygon-fill-only state. (Snapshotting after Pass 1 would preserve any
    # rogue grid stubs Pass 1 drew along bbox edges.)
    poly_snapshots = {}
    for room in polymorph_rooms:
        px_x1 = room["west"] * cell_size
        px_y1 = room["north"] * cell_size
        px_x2 = (room["east"] + 1) * cell_size + 1
        px_y2 = (room["south"] + 1) * cell_size + 1
        poly_snapshots[id(room)] = (
            (px_x1, px_y1, px_x2, px_y2),
            img.crop((px_x1, px_y1, px_x2, px_y2)).copy(),
        )

    # --- Pass 1: Draw GRID_COLOR on every edge of every open cell ---
    # Suppression rule: skip an edge only when BOTH adjacent cells live inside
    # the same polymorph bbox — those purely-internal edges are reissued by
    # Pass 2 (which masks them against the polygon's white interior). Edges
    # at the bbox boundary still get drawn here, so connector cells (cells
    # inside the bbox but outside the polygon shape, filled white by corridor
    # invasion) keep their grid lines on the side that faces a corridor or
    # other open cell outside the bbox. Pass 2's pre-Pass-1 snapshot reverts
    # any pixels that fell on the polygon-exterior background, so polygon
    # boundary edges remain clean.
    for row in range(n_rows):
        for col in range(n_cols):
            if not _pixel_is_open(row, col):
                continue

            x = col * cell_size
            y = row * cell_size
            in_poly = (row, col) in polymorph_cells

            # Right edge
            rx = x + cell_size
            neighbor_in_poly = (row, col + 1) in polymorph_cells
            if not (in_poly and neighbor_in_poly):
                draw.line([(rx, y), (rx, y + cell_size)], fill=GRID_COLOR)

            # Bottom edge
            by = y + cell_size
            neighbor_in_poly = (row + 1, col) in polymorph_cells
            if not (in_poly and neighbor_in_poly):
                draw.line([(x, by), (x + cell_size, by)], fill=GRID_COLOR)

            # Left edge — only if neighbor not open (open neighbor draws it
            # from its own right edge to avoid double-drawing)
            neighbor_open = col > 0 and _pixel_is_open(row, col - 1)
            neighbor_in_poly = (row, col - 1) in polymorph_cells
            if not neighbor_open and not (in_poly and neighbor_in_poly):
                draw.line([(x, y), (x, y + cell_size)], fill=GRID_COLOR)

            # Top edge — same rule
            neighbor_open = row > 0 and _pixel_is_open(row - 1, col)
            neighbor_in_poly = (row - 1, col) in polymorph_cells
            if not neighbor_open and not (in_poly and neighbor_in_poly):
                draw.line([(x, y), (x + cell_size, y)], fill=GRID_COLOR)

    # --- Pass 2: Polymorph grid lines — draw all, then mask to white area ---
    for room in polymorph_rooms:
        north, south = room["north"], room["south"]
        west, east = room["west"], room["east"]

        # Use the pre-Pass-1 snapshot as the mask reference, so any rogue
        # GRID_COLOR pixels Pass 1 drew along bbox boundaries get reverted.
        (px_x1, px_y1, px_x2, px_y2), region = poly_snapshots[id(room)]

        # Draw ALL internal grid lines within the bounding box
        for r in range(north, south + 1):
            for c in range(west, east + 1):
                x = c * cell_size
                y = r * cell_size
                # Right edge (internal only — not the last column)
                if c < east:
                    rx = x + cell_size
                    draw.line([(rx, y), (rx, y + cell_size)], fill=GRID_COLOR)
                # Bottom edge (internal only — not the last row)
                if r < south:
                    by = y + cell_size
                    draw.line([(x, by), (x + cell_size, by)], fill=GRID_COLOR)

        # Mask: a grid pixel should only survive where the underlying fill
        # was fully white. Pixels outside the polygon were BLACK in the
        # snapshot and get reverted, so the polygon's sharp boundary stays
        # clean instead of being painted over with grid gray.
        pixels = img.load()
        region_pixels = region.load()
        for py in range(px_y1, min(px_y2, img.height)):
            for px in range(px_x1, min(px_x2, img.width)):
                if pixels[px, py] != GRID_COLOR:
                    continue
                orig = region_pixels[px - px_x1, py - px_y1]
                if orig != WHITE:
                    pixels[px, py] = orig


# ---------------------------------------------------------------------------
# Phase 4: Doors — asset-based rendering
# ---------------------------------------------------------------------------

# Cache for procedurally drawn door symbols, keyed by (name, size, orient).
_door_symbol_cache = {}


def _get_door_symbol(name, cell_size, orient):
    """Return an RGBA door symbol drawn fresh at `cell_size` (cached).

    Symbols are rendered procedurally by `gen_assets.door_symbol_rgba` at the
    exact target pixel size — no resampling, no anti-aliasing — so straight
    lines stay straight and 1-pixel features stay crisp at any cell_size.
    The base symbol is drawn for a vertical wall (stubs top/bottom); for
    horizontal walls (north/south doors) it's rotated 90° clockwise.
    """
    cache_key = (name, cell_size, orient)
    cached = _door_symbol_cache.get(cache_key)
    if cached is not None:
        return cached

    sym = door_symbol_rgba(name, cell_size)
    if orient == "horizontal":
        sym = sym.rotate(-90, expand=True)

    _door_symbol_cache[cache_key] = sym
    return sym


def _draw_doors_on_img(img, cell_data, n_rows, n_cols, cell_size, row_off, col_off,
                       door_orient_map):
    """Draw door symbols by compositing freshly-rendered RGBA glyphs."""
    for row in range(n_rows):
        for col in range(n_cols):
            cell = _cell(cell_data, row, col, row_off, col_off)
            if not C.has_door(cell):
                continue

            x = col * cell_size
            y = row * cell_size
            dt = C.door_type(cell)

            orient = door_orient_map.get((row, col))
            if orient is None:
                orient = _orient_from_neighbors(cell_data, row, col, row_off, col_off)

            asset_name = {
                "arch": "archway",
                "portcullis": "portcullis",
                "door": "door",
                "locked": "locked",
                "trapped": "trapped",
                "secret": "secret",
            }.get(dt, "door")

            asset = _get_door_symbol(asset_name, cell_size, orient)
            img.paste(asset, (x, y), asset)  # use alpha channel as mask


def _orient_from_neighbors(cell_data, row, col, row_off, col_off):
    """Fallback orientation detection from cell neighbors."""
    left = _cell(cell_data, row, col - 1, row_off, col_off)
    right = _cell(cell_data, row, col + 1, row_off, col_off)
    above = _cell(cell_data, row - 1, col, row_off, col_off)
    below = _cell(cell_data, row + 1, col, row_off, col_off)

    h_walls = (not C.is_open(left)) + (not C.is_open(right))
    v_walls = (not C.is_open(above)) + (not C.is_open(below))

    if h_walls > v_walls:
        return "horizontal"
    if v_walls > h_walls:
        return "vertical"
    return "horizontal"


# ---------------------------------------------------------------------------
# Phase 4: Stairs
# ---------------------------------------------------------------------------

def _draw_stairs(draw, stairs, cell_data, cell_size, row_off, col_off):
    """Draw stair symbols. Stairs span 2 cells in the direction they face."""
    for stair in stairs:
        row, col = stair["row"], stair["col"]
        direction = stair["dir"]
        key = stair["key"]

        x = col * cell_size
        y = row * cell_size

        if direction == "east":
            sx, sy = x, y
            sw, sh = cell_size * 2, cell_size
        elif direction == "west":
            sx, sy = x - cell_size, y
            sw, sh = cell_size * 2, cell_size
        elif direction == "south":
            sx, sy = x, y
            sw, sh = cell_size, cell_size * 2
        elif direction == "north":
            sx, sy = x, y - cell_size
            sw, sh = cell_size, cell_size * 2
        else:
            continue

        wall_w = max(2, cell_size // 7)

        if key == "up":
            _draw_stair_up(draw, sx, sy, sw, sh, wall_w, direction)
        else:
            _draw_stair_down(draw, sx, sy, sw, sh, wall_w, direction)


def _draw_stair_up(draw, sx, sy, sw, sh, wall_w, direction):
    """Draw stair up: alternating stripes (hatching) filling the 2-cell area."""
    spacing = 2
    if direction in ("east", "west"):
        for px in range(sx + wall_w, sx + sw, spacing):
            draw.line([(px, sy + wall_w), (px, sy + sh - wall_w)], fill=STAIR_COLOR)
    else:
        for py in range(sy + wall_w, sy + sh, spacing):
            draw.line([(sx + wall_w, py), (sx + sw - wall_w, py)], fill=STAIR_COLOR)


def _draw_stair_down(draw, sx, sy, sw, sh, wall_w, direction):
    """Draw stair down: progressively wider bars forming a staircase."""
    cx = sx + sw // 2
    cy = sy + sh // 2

    if direction in ("south", "north"):
        inner_w = sw - wall_w * 2
        inner_h = sh - wall_w * 2
        n_steps = max(3, inner_h // 3)
        for i in range(n_steps):
            t = (i + 1) / n_steps
            half_w = int(t * inner_w / 2)
            if direction == "south":
                step_y = sy + wall_w + int(t * inner_h)
            else:
                step_y = sy + sh - wall_w - int(t * inner_h)
            if sy + wall_w <= step_y <= sy + sh - wall_w:
                draw.line([(cx - half_w, step_y), (cx + half_w, step_y)],
                          fill=STAIR_COLOR)
    else:
        inner_w = sw - wall_w * 2
        inner_h = sh - wall_w * 2
        n_steps = max(3, inner_w // 3)
        for i in range(n_steps):
            t = (i + 1) / n_steps
            half_h = int(t * inner_h / 2)
            if direction == "east":
                step_x = sx + wall_w + int(t * inner_w)
            else:
                step_x = sx + sw - wall_w - int(t * inner_w)
            if sx + wall_w <= step_x <= sx + sw - wall_w:
                draw.line([(step_x, cy - half_h), (step_x, cy + half_h)],
                          fill=STAIR_COLOR)


# ---------------------------------------------------------------------------
# Phase 4: Labels
# ---------------------------------------------------------------------------

_LABEL_FONT_CANDIDATES = (
    # Serifed monospace fonts, in preference order. Courier is the classic
    # typewriter face; Courier New is the Microsoft-licensed equivalent that
    # ships under macOS Supplemental. Absolute paths are tried first, then
    # bare names that let Pillow resolve from the platform font directories
    # (covers packaged/frozen builds where the layout may differ). The PIL
    # default bitmap font is the last-resort fallback.
    # macOS
    "/System/Library/Fonts/Courier.ttc",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/Library/Fonts/Courier New.ttf",
    # Windows (Courier New ships with every install)
    "C:/Windows/Fonts/cour.ttf",
    # Linux (DejaVu Sans Mono is the near-universal monospace fallback)
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    # Bare-name lookups (Pillow searches the platform font directories)
    "cour.ttf",
    "Courier New.ttf",
    "DejaVuSansMono.ttf",
)


def _load_label_font(font_size):
    for path in _LABEL_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, font_size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw_labels(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off):
    """Draw room numbers and corridor feature labels on the map.

    Uses a serifed monospace font (Courier) and 1-bit (aliased) glyph
    rendering so each character has a uniform width and crisp pure-black
    edges rather than grayscale anti-aliased ones.
    """
    font_size = max(8, cell_size - 4)
    font = _load_label_font(font_size)

    draw.fontmode = "1"

    for row in range(n_rows):
        for col in range(n_cols):
            cell = _cell(cell_data, row, col, row_off, col_off)
            ch = C.label_char(cell)
            if ch is None:
                continue

            x = col * cell_size
            y = row * cell_size
            cx = x + cell_size // 2
            cy = y + cell_size // 2

            bbox = font.getbbox(ch)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]

            draw.text(
                (cx - tw // 2, cy - th // 2 - bbox[1]),
                ch,
                fill=LABEL_COLOR,
                font=font,
            )
