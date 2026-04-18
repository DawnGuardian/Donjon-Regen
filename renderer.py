"""Map renderer - generates GM and player map PNGs from dungeon JSON data."""

import math
from PIL import Image, ImageDraw, ImageFont

import cells as C

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


def _compute_offset(dungeon):
    """Compute the row/col offset between the cells array and map coordinates.

    The cells array may be larger than n_rows x n_cols, padded equally on all
    sides.  Map-coordinate (r, c) corresponds to cells[r + off][c + off].
    """
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


def render_map(dungeon, cell_size=None, gm_mode=True):
    """Render the dungeon map as a PIL Image."""
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

    # Build door orientation map from room data (more reliable than neighbor checks)
    door_orient_map = _build_door_orient_map(dungeon)

    # Pass 1: Fill open cells with white
    _fill_cells(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off)

    # Pass 2: Draw polygon/circle rooms as filled geometric shapes
    _draw_shaped_rooms(draw, img, rooms, cell_size)

    # Pass 3: Draw grid lines on top of fills
    draw = ImageDraw.Draw(img)  # refresh after paste operations
    _draw_grid(draw, img, cell_data, n_rows, n_cols, cell_size, row_off, col_off)

    # Pass 4: Draw doors
    _draw_doors(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off,
                door_orient_map)

    # Pass 5: Draw stairs
    _draw_stairs(draw, stairs, cell_data, cell_size, row_off, col_off)

    # Pass 6: Draw labels (GM mode only)
    if gm_mode:
        _draw_labels(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off)

    return img


def _build_door_orient_map(dungeon):
    """Build a map of (row, col) -> orientation from room door data.

    This is more reliable than checking cell neighbors, which can fail when
    all neighbors are walls.
    """
    orient_map = {}
    rooms = dungeon.get("rooms", [])
    for room in rooms:
        if room is None:
            continue
        doors = room.get("doors", {})
        for direction, door_list in doors.items():
            # north/south doors sit on a horizontal wall (east-west)
            # east/west doors sit on a vertical wall (north-south)
            orient = "horizontal" if direction in ("north", "south") else "vertical"
            for door in door_list:
                orient_map[(door["row"], door["col"])] = orient
    return orient_map


def _fill_cells(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off):
    """Fill all open cells with white."""
    for row in range(n_rows):
        for col in range(n_cols):
            if C.is_open(_cell(cell_data, row, col, row_off, col_off)):
                x = col * cell_size
                y = row * cell_size
                draw.rectangle([x, y, x + cell_size, y + cell_size], fill=WHITE)


def _draw_shaped_rooms(draw, img, rooms, cell_size):
    """Draw polygon and circle rooms as filled geometric shapes.

    Shaped rooms are drawn directly from their geometric definition, not from
    cell data, because the cell data may only contain a subset of the room area.
    """
    for room in rooms:
        if room is None:
            continue

        shape = room.get("shape")
        if shape not in ("polygon", "circle"):
            continue

        west, east = room["west"], room["east"]
        north, south = room["north"], room["south"]

        x1 = west * cell_size
        y1 = north * cell_size
        x2 = (east + 1) * cell_size
        y2 = (south + 1) * cell_size

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


def _draw_grid(draw, img, cell_data, n_rows, n_cols, cell_size, row_off, col_off):
    """Draw grid lines over open cells. Must be called after all fills."""
    for row in range(n_rows):
        for col in range(n_cols):
            x = col * cell_size
            y = row * cell_size

            # Check if this pixel is white (open area) - works for both
            # cell-based fills and geometric shape fills
            px_x = min(x + cell_size // 2, img.width - 1)
            px_y = min(y + cell_size // 2, img.height - 1)
            if img.getpixel((px_x, px_y)) == BLACK:
                continue

            cell_right = _cell(cell_data, row, col + 1, row_off, col_off)
            cell_below = _cell(cell_data, row + 1, col, row_off, col_off)
            cell_left = _cell(cell_data, row, col - 1, row_off, col_off)
            cell_above = _cell(cell_data, row - 1, col, row_off, col_off)

            # Check if neighbor pixels are also white (for shaped rooms)
            def _is_open_pixel(r, c):
                px = min(c * cell_size + cell_size // 2, img.width - 1)
                py = min(r * cell_size + cell_size // 2, img.height - 1)
                if px < 0 or py < 0:
                    return False
                return img.getpixel((px, py)) != BLACK

            # Right edge
            rx = x + cell_size
            if col + 1 < n_cols and _is_open_pixel(row, col + 1):
                draw.line([(rx, y), (rx, y + cell_size)], fill=GRID_COLOR)
            else:
                draw.line([(rx, y), (rx, y + cell_size)], fill=WALL_COLOR)

            # Bottom edge
            by = y + cell_size
            if row + 1 < n_rows and _is_open_pixel(row + 1, col):
                draw.line([(x, by), (x + cell_size, by)], fill=GRID_COLOR)
            else:
                draw.line([(x, by), (x + cell_size, by)], fill=WALL_COLOR)

            # Left edge (wall boundary)
            if col == 0 or not _is_open_pixel(row, col - 1):
                draw.line([(x, y), (x, y + cell_size)], fill=WALL_COLOR)

            # Top edge (wall boundary)
            if row == 0 or not _is_open_pixel(row - 1, col):
                draw.line([(x, y), (x + cell_size, y)], fill=WALL_COLOR)


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


def _draw_doors(draw, cell_data, n_rows, n_cols, cell_size, row_off, col_off,
                door_orient_map):
    """Draw door symbols on the map."""
    for row in range(n_rows):
        for col in range(n_cols):
            cell = _cell(cell_data, row, col, row_off, col_off)
            if not C.has_door(cell):
                continue

            x = col * cell_size
            y = row * cell_size
            dt = C.door_type(cell)

            # Use precomputed orientation from room data, fall back to neighbor check
            orient = door_orient_map.get((row, col))
            if orient is None:
                orient = _orient_from_neighbors(cell_data, row, col, row_off, col_off)

            if dt == "arch":
                _draw_arch_symbol(draw, x, y, cell_size, orient)
            elif dt == "portcullis":
                _draw_portcullis_symbol(draw, x, y, cell_size, orient)
            elif dt == "secret":
                _draw_secret_symbol(draw, x, y, cell_size, orient)
            else:
                _draw_door_symbol(draw, x, y, cell_size, orient)


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


def _draw_door_symbol(draw, x, y, cell_size, orient):
    """Draw a regular door: rectangle outline with connecting lines to walls."""
    cx = x + cell_size // 2
    cy = y + cell_size // 2
    half = max(3, (cell_size * 3) // 7)
    lw = max(1, cell_size // 5)

    if orient == "vertical":
        # Wall runs north-south, door opens east-west
        draw.rectangle([cx - lw // 2, y, cx + lw // 2, y + cell_size], fill=BLACK)
        draw.rectangle(
            [cx - half, cy - half, cx + half, cy + half],
            fill=DOOR_FILL, outline=DOOR_OUTLINE,
        )
        draw.point((cx - half, cy - half), fill=GRAY)
        draw.point((cx + half, cy - half), fill=GRAY)
        draw.point((cx - half, cy + half), fill=GRAY)
        draw.point((cx + half, cy + half), fill=GRAY)
    else:
        # Wall runs east-west, door opens north-south
        draw.rectangle([x, cy - lw // 2, x + cell_size, cy + lw // 2], fill=BLACK)
        draw.rectangle(
            [cx - half, cy - half, cx + half, cy + half],
            fill=DOOR_FILL, outline=DOOR_OUTLINE,
        )
        draw.point((cx - half, cy - half), fill=GRAY)
        draw.point((cx + half, cy - half), fill=GRAY)
        draw.point((cx - half, cy + half), fill=GRAY)
        draw.point((cx + half, cy + half), fill=GRAY)


def _draw_arch_symbol(draw, x, y, cell_size, orient):
    """Draw an archway: two small dots at wall edges with open space."""
    cx = x + cell_size // 2
    cy = y + cell_size // 2
    lw = max(1, cell_size // 5)

    if orient == "vertical":
        draw.rectangle([cx - lw // 2, y, cx + lw // 2, y + cell_size], fill=BLACK)
        gap = max(3, cell_size // 2)
        draw.rectangle(
            [cx - lw // 2 - 1, cy - gap, cx + lw // 2 + 1, cy + gap],
            fill=WHITE,
        )
        draw.point((cx, cy - gap), fill=GRAY)
        draw.point((cx - 1, cy - gap), fill=GRAY)
        draw.point((cx, cy + gap), fill=GRAY)
        draw.point((cx - 1, cy + gap), fill=GRAY)
    else:
        draw.rectangle([x, cy - lw // 2, x + cell_size, cy + lw // 2], fill=BLACK)
        gap = max(3, cell_size // 2)
        draw.rectangle(
            [cx - gap, cy - lw // 2 - 1, cx + gap, cy + lw // 2 + 1],
            fill=WHITE,
        )
        draw.point((cx - gap, cy), fill=GRAY)
        draw.point((cx - gap, cy - 1), fill=GRAY)
        draw.point((cx + gap, cy), fill=GRAY)
        draw.point((cx + gap, cy - 1), fill=GRAY)


def _draw_portcullis_symbol(draw, x, y, cell_size, orient):
    """Draw a portcullis: alternating dots/dashes across the opening."""
    cx = x + cell_size // 2
    cy = y + cell_size // 2
    lw = max(1, cell_size // 5)

    if orient == "vertical":
        draw.rectangle([cx - lw // 2, y, cx + lw // 2, y + cell_size], fill=BLACK)
        draw.rectangle(
            [cx - lw, cy - (cell_size // 2 - 2), cx + lw, cy + (cell_size // 2 - 2)],
            fill=WHITE,
        )
        spacing = max(2, cell_size // 5)
        py = cy - (cell_size // 2 - 3)
        while py <= cy + (cell_size // 2 - 3):
            draw.point((cx, py), fill=BLACK)
            draw.point((cx - 1, py), fill=GRAY)
            draw.point((cx + 1, py), fill=GRAY)
            py += spacing
    else:
        draw.rectangle([x, cy - lw // 2, x + cell_size, cy + lw // 2], fill=BLACK)
        draw.rectangle(
            [cx - (cell_size // 2 - 2), cy - lw, cx + (cell_size // 2 - 2), cy + lw],
            fill=WHITE,
        )
        spacing = max(2, cell_size // 5)
        px = cx - (cell_size // 2 - 3)
        while px <= cx + (cell_size // 2 - 3):
            draw.point((px, cy), fill=BLACK)
            draw.point((px, cy - 1), fill=GRAY)
            draw.point((px, cy + 1), fill=GRAY)
            px += spacing


def _draw_secret_symbol(draw, x, y, cell_size, orient):
    """Draw a secret door: S-shaped pattern."""
    cx = x + cell_size // 2
    cy = y + cell_size // 2
    lw = max(1, cell_size // 5)

    if orient == "vertical":
        draw.rectangle([cx - lw // 2, y, cx + lw // 2, y + cell_size], fill=BLACK)
    else:
        draw.rectangle([x, cy - lw // 2, x + cell_size, cy + lw // 2], fill=BLACK)

    try:
        font_size = max(6, cell_size - 4)
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default()
    bbox = font.getbbox("S")
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        (cx - tw // 2, cy - th // 2 - bbox[1]),
        "S", fill=DOOR_OUTLINE, font=font,
    )


def _draw_stairs(draw, stairs, cell_data, cell_size, row_off, col_off):
    """Draw stair symbols using coordinates from the stairs array."""
    for stair in stairs:
        row, col = stair["row"], stair["col"]
        direction = stair["dir"]
        key = stair["key"]

        x = col * cell_size
        y = row * cell_size

        if key == "up":
            _draw_stair_up(draw, x, y, cell_size, direction)
        else:
            _draw_stair_down(draw, x, y, cell_size, direction)


def _draw_stair_up(draw, x, y, cell_size, direction):
    """Draw stair up: alternating stripes (hatching) filling the cell."""
    wall_w = max(2, cell_size // 7)
    spacing = 2

    if direction == "east":
        for px in range(x + wall_w, x + cell_size, spacing):
            draw.line([(px, y + wall_w), (px, y + cell_size - wall_w)], fill=STAIR_COLOR)
    elif direction == "west":
        for px in range(x, x + cell_size - wall_w, spacing):
            draw.line([(px, y + wall_w), (px, y + cell_size - wall_w)], fill=STAIR_COLOR)
    elif direction == "north":
        for py in range(y, y + cell_size - wall_w, spacing):
            draw.line([(x + wall_w, py), (x + cell_size - wall_w, py)], fill=STAIR_COLOR)
    elif direction == "south":
        for py in range(y + wall_w, y + cell_size, spacing):
            draw.line([(x + wall_w, py), (x + cell_size - wall_w, py)], fill=STAIR_COLOR)


def _draw_stair_down(draw, x, y, cell_size, direction):
    """Draw stair down: progressively wider bars forming a staircase."""
    wall_w = max(2, cell_size // 7)
    inner = cell_size - wall_w * 2
    cx = x + cell_size // 2
    cy = y + cell_size // 2
    n_steps = max(3, inner // 3)

    if direction == "south":
        for i in range(n_steps):
            t = (i + 1) / n_steps
            half_w = int(t * inner / 2)
            step_y = y + wall_w + int(t * inner)
            if step_y < y + cell_size - wall_w:
                draw.line([(cx - half_w, step_y), (cx + half_w, step_y)], fill=STAIR_COLOR)
    elif direction == "north":
        for i in range(n_steps):
            t = (i + 1) / n_steps
            half_w = int(t * inner / 2)
            step_y = y + cell_size - wall_w - int(t * inner)
            if step_y >= y + wall_w:
                draw.line([(cx - half_w, step_y), (cx + half_w, step_y)], fill=STAIR_COLOR)
    elif direction == "east":
        for i in range(n_steps):
            t = (i + 1) / n_steps
            half_h = int(t * inner / 2)
            step_x = x + wall_w + int(t * inner)
            if step_x < x + cell_size - wall_w:
                draw.line([(step_x, cy - half_h), (step_x, cy + half_h)], fill=STAIR_COLOR)
    elif direction == "west":
        for i in range(n_steps):
            t = (i + 1) / n_steps
            half_h = int(t * inner / 2)
            step_x = x + cell_size - wall_w - int(t * inner)
            if step_x >= x + wall_w:
                draw.line([(step_x, cy - half_h), (step_x, cy + half_h)], fill=STAIR_COLOR)


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
