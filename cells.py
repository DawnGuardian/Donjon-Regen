"""Cell bitmask constants and helper functions for donjon dungeon data."""

# Cell bit masks (from cell_bit in JSON)
NOTHING    = 0
BLOCK      = 1
ROOM       = 2
CORRIDOR   = 4
PERIMETER  = 16
APERTURE   = 32
ARCH       = 65536
DOOR       = 131072
LOCKED     = 262144
TRAPPED    = 524288
SECRET     = 1048576
PORTCULLIS = 2097152
STAIR_DOWN = 4194304
STAIR_UP   = 8388608
ROOM_ID    = 65472       # bits 6-15
LABEL      = 4278190080  # bits 24-31

OPEN_SPACE = ROOM | CORRIDOR
DOOR_TYPES = DOOR | ARCH | PORTCULLIS | SECRET | LOCKED | TRAPPED


def is_open(cell):
    return bool(cell & OPEN_SPACE)


def is_room(cell):
    return bool(cell & ROOM)


def is_corridor(cell):
    return bool(cell & CORRIDOR)


def is_perimeter(cell):
    return bool(cell & PERIMETER) and not is_open(cell)


def room_id(cell):
    return (cell & ROOM_ID) >> 6


def label_char(cell):
    val = (cell & LABEL) >> 24
    return chr(val) if val else None


def has_door(cell):
    return bool(cell & DOOR_TYPES)


def door_type(cell):
    if cell & PORTCULLIS:
        return "portcullis"
    if cell & SECRET:
        return "secret"
    if cell & LOCKED:
        return "locked"
    if cell & TRAPPED:
        return "trapped"
    if cell & DOOR:
        return "door"
    if cell & ARCH:
        return "arch"
    return None


_DOOR_TYPE_BITS = {
    "arch": ARCH,
    "door": DOOR,
    "locked": LOCKED,
    "trapped": TRAPPED,
    "secret": SECRET,
    "portcullis": PORTCULLIS,
}


def door_type_bit(name):
    """Return the bitmask for a door type name (e.g. 'locked' → LOCKED)."""
    return _DOOR_TYPE_BITS.get(name, 0)


def set_door_type(cell, name):
    """Return `cell` with all door-type bits cleared and `name`'s bit set."""
    return (cell & ~DOOR_TYPES) | _DOOR_TYPE_BITS.get(name, 0)


DOOR_TYPE_NAMES = list(_DOOR_TYPE_BITS.keys())


def has_stair(cell):
    return bool(cell & (STAIR_DOWN | STAIR_UP))


def stair_direction(cell):
    if cell & STAIR_DOWN:
        return "down"
    if cell & STAIR_UP:
        return "up"
    return None


def get_cell(cells, row, col):
    if 0 <= row < len(cells) and 0 <= col < len(cells[0]):
        return cells[row][col]
    return 0


def door_orientation(cells, row, col):
    """Determine if a door is on a horizontal or vertical wall.

    Returns 'horizontal' if the wall runs east-west (door opens N-S),
    'vertical' if the wall runs north-south (door opens E-W).
    """
    left = get_cell(cells, row, col - 1)
    right = get_cell(cells, row, col + 1)
    above = get_cell(cells, row - 1, col)
    below = get_cell(cells, row + 1, col)

    left_wall = not is_open(left)
    right_wall = not is_open(right)
    above_wall = not is_open(above)
    below_wall = not is_open(below)

    if left_wall and right_wall:
        return "horizontal"
    if above_wall and below_wall:
        return "vertical"
    # Fallback: check which direction has more walls
    if left_wall or right_wall:
        return "horizontal"
    return "vertical"
