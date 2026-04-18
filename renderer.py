"""Map renderer - generates GM and player map PNGs from dungeon JSON data."""

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

import cells as C

ASSETS_DIR = Path(__file__).parent / "assets"

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
    """Compute polygon vertices for a shaped room."""
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
        vertices.append((round(vx), round(vy)))
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
    _fill_polymorph_rooms(draw, rooms, cell_size)

    # Phase 2: Corridors (with directional invasion into polymorph rooms)
    _fill_corridors(draw, img, cell_data, n_rows, n_cols, cell_size, row_off,
                    col_off, dungeon)

    # Phase 3: Grid lines and polymorph perimeter outlines
    _draw_grid(draw, img, n_rows, n_cols, cell_size)
    _draw_polymorph_outlines(draw, rooms, cell_size)

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


def _fill_polymorph_rooms(draw, rooms, cell_size):
    """Draw polymorph rooms as proper geometric shapes (white fill)."""
    for room in rooms:
        if room is None:
            continue
        shape = room.get("shape")
        if shape == "polygon":
            vertices = room_polygon_vertices(room, cell_size)
            if vertices:
                draw.polygon(vertices, fill=WHITE)
        elif shape == "circle":
            cx, cy, radius = room_circle_params(room, cell_size)
            draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=WHITE,
            )


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
            return img.getpixel((px, py)) != BLACK
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
                while 0 <= r < n_rows and 0 <= c < n_cols:
                    if _is_white(r, c):
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


# ---------------------------------------------------------------------------
# Phase 3: Grid lines and polymorph outlines
# ---------------------------------------------------------------------------

def _draw_grid(draw, img, n_rows, n_cols, cell_size):
    """Draw grid lines based on rendered pixel state.

    Gray lines between two open cells, wall-color lines at open/closed edges.
    """
    def _pixel_is_open(r, c):
        """Check if a cell's center pixel is white (rendered as open)."""
        px = c * cell_size + cell_size // 2
        py = r * cell_size + cell_size // 2
        if 0 <= px < img.width and 0 <= py < img.height:
            return img.getpixel((px, py)) != BLACK
        return False

    for row in range(n_rows):
        for col in range(n_cols):
            if not _pixel_is_open(row, col):
                continue

            x = col * cell_size
            y = row * cell_size

            # Right edge
            rx = x + cell_size
            if col + 1 < n_cols and _pixel_is_open(row, col + 1):
                draw.line([(rx, y), (rx, y + cell_size)], fill=GRID_COLOR)
            else:
                draw.line([(rx, y), (rx, y + cell_size)], fill=WALL_COLOR)

            # Bottom edge
            by = y + cell_size
            if row + 1 < n_rows and _pixel_is_open(row + 1, col):
                draw.line([(x, by), (x + cell_size, by)], fill=GRID_COLOR)
            else:
                draw.line([(x, by), (x + cell_size, by)], fill=WALL_COLOR)

            # Left edge
            if col == 0 or not _pixel_is_open(row, col - 1):
                draw.line([(x, y), (x, y + cell_size)], fill=WALL_COLOR)

            # Top edge
            if row == 0 or not _pixel_is_open(row - 1, col):
                draw.line([(x, y), (x + cell_size, y)], fill=WALL_COLOR)


def _draw_polymorph_outlines(draw, rooms, cell_size):
    """Draw smooth geometric outlines for polymorph rooms.

    This overrides the blocky cell-boundary wall lines with proper
    polygon/circle perimeters.
    """
    for room in rooms:
        if room is None:
            continue
        shape = room.get("shape")
        if shape == "polygon":
            vertices = room_polygon_vertices(room, cell_size)
            if vertices:
                draw.polygon(vertices, outline=WALL_COLOR)
        elif shape == "circle":
            cx, cy, radius = room_circle_params(room, cell_size)
            draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                outline=WALL_COLOR,
            )


# ---------------------------------------------------------------------------
# Phase 4: Doors — asset-based rendering
# ---------------------------------------------------------------------------

# Cache for loaded and scaled door symbol images
_door_asset_cache = {}


def _load_door_asset(name, cell_size, orient):
    """Load a door symbol asset, scale to cell_size, and rotate for orientation.

    The key.png symbols are drawn for a vertical wall (wall bars at top/bottom,
    symbol opens left-right). For horizontal wall doors the asset is rotated 90°.

    Returns a PIL Image with transparency (RGBA).
    """
    cache_key = (name, cell_size, orient)
    if cache_key in _door_asset_cache:
        return _door_asset_cache[cache_key]

    asset_path = ASSETS_DIR / f"{name}.png"
    src = Image.open(asset_path).convert("RGBA")

    # Make white pixels transparent so the symbol composites cleanly
    pixels = src.load()
    for y in range(src.height):
        for x in range(src.width):
            r, g, b, a = pixels[x, y]
            if r > 200 and g > 200 and b > 200:
                pixels[x, y] = (255, 255, 255, 0)

    # Scale to cell_size x cell_size (LANCZOS preserves thin details like
    # the locked door's center line and trapped door's cross bar)
    scaled = src.resize((cell_size, cell_size), Image.LANCZOS)

    # Threshold: LANCZOS produces anti-aliased grays. Snap pixels to either
    # opaque black/gray or fully transparent so the symbol stays crisp.
    sp = scaled.load()
    for y in range(scaled.height):
        for x in range(scaled.width):
            r, g, b, a = sp[x, y]
            if a < 64:
                sp[x, y] = (0, 0, 0, 0)
            else:
                sp[x, y] = (r, g, b, 255)

    # The asset shows a vertical wall orientation (wall at top/bottom).
    # For horizontal wall doors (north/south), rotate 90° clockwise.
    if orient == "horizontal":
        scaled = scaled.rotate(-90, expand=True)

    _door_asset_cache[cache_key] = scaled
    return scaled



def _draw_doors_on_img(img, cell_data, n_rows, n_cols, cell_size, row_off, col_off,
                       door_orient_map):
    """Draw door symbols by pasting scaled assets onto the image directly."""
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

            asset = _load_door_asset(asset_name, cell_size, orient)
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

def _draw_labels(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off):
    """Draw room numbers and corridor feature labels on the map."""
    try:
        font_size = max(8, cell_size - 4)
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default()

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
