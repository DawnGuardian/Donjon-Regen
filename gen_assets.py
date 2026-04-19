"""Generate sharp door and stair symbol assets programmatically.

Creates pixel-perfect 19x19 door symbols and 44x19 stair symbols.
Black on white; white is made transparent at render time.

Symbol orientation: vertical wall (wall stubs at top/bottom center,
symbol opens left-right). Rotated 90° at render time for horizontal walls.
"""

from PIL import Image, ImageDraw
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

DOOR_SIZE = 19
STAIR_W = 44
STAIR_H = 19


def _new(w, h):
    img = Image.new("RGB", (w, h), WHITE)
    return img, ImageDraw.Draw(img)


def _wall_stub_v(draw, w, h, stub_top, stub_bot, cx=None, bar_w=3):
    """Draw vertical wall stubs at top and bottom center."""
    if cx is None:
        cx = w // 2
    x1 = cx - bar_w // 2
    x2 = x1 + bar_w - 1
    draw.rectangle([x1, 0, x2, stub_top - 1], fill=BLACK)
    draw.rectangle([x1, h - stub_bot, x2, h - 1], fill=BLACK)


def gen_archway():
    """Archway: wall stubs top/bottom with open gap between.

    Reference pattern (19x19):
      cols 8-10: wall stub rows 0-4 and rows 14-18
      rows 5-13: open
    """
    img, draw = _new(DOOR_SIZE, DOOR_SIZE)
    _wall_stub_v(draw, DOOR_SIZE, DOOR_SIZE, 5, 5)
    return img


def gen_portcullis():
    """Portcullis: wall stubs + dashed line in the gap.

    Reference pattern (19x19):
      cols 8-10: wall stub rows 0-4 and rows 14-18
      col 9: dashes at rows 6,9,12 (2px wide marks)
      col 9: thin line at rows 5,7-8,10-11,13
    """
    img, draw = _new(DOOR_SIZE, DOOR_SIZE)
    _wall_stub_v(draw, DOOR_SIZE, DOOR_SIZE, 5, 5)

    cx = DOOR_SIZE // 2  # col 9

    # Thin vertical line segments (1px wide)
    for y in [5, 7, 8, 10, 11, 13]:
        img.putpixel((cx, y), BLACK)

    # Wider dash marks (3px wide) at rows 6, 9, 12
    for y in [6, 9, 12]:
        img.putpixel((cx - 1, y), BLACK)
        img.putpixel((cx, y), BLACK)
        img.putpixel((cx + 1, y), BLACK)

    return img


def _draw_door_rect(draw, m=4):
    """Draw the standard door panel rectangle (1px outline)."""
    draw.rectangle([m, m, DOOR_SIZE - m - 1, DOOR_SIZE - m - 1], outline=BLACK)


def gen_door():
    """Door: wall stubs + rectangle outline.

    Reference pattern (19x19):
      cols 8-10: wall stub rows 0-3 and rows 15-18
      rectangle: rows 4-14, cols 4-14, border 1px
    """
    img, draw = _new(DOOR_SIZE, DOOR_SIZE)
    _wall_stub_v(draw, DOOR_SIZE, DOOR_SIZE, 4, 4)
    _draw_door_rect(draw)
    return img


def gen_locked():
    """Locked door: door rectangle + vertical bar through center.

    Reference pattern (19x19):
      Same as door, plus vertical bar at col 9 inside the rectangle.
    """
    img, draw = _new(DOOR_SIZE, DOOR_SIZE)
    _wall_stub_v(draw, DOOR_SIZE, DOOR_SIZE, 4, 4)
    _draw_door_rect(draw)

    # Vertical bar through center of rectangle
    cx = DOOR_SIZE // 2
    m = 4
    draw.line([(cx, m), (cx, DOOR_SIZE - m - 1)], fill=BLACK)
    return img


def gen_trapped():
    """Trapped door: door rectangle + horizontal bar extending beyond it.

    Reference pattern (19x19):
      Same as door, plus horizontal bar at row 9 from col 3 to col 15.
    """
    img, draw = _new(DOOR_SIZE, DOOR_SIZE)
    _wall_stub_v(draw, DOOR_SIZE, DOOR_SIZE, 4, 4)
    _draw_door_rect(draw)

    # Horizontal bar through center, extending beyond rectangle
    m = 4
    cy = DOOR_SIZE // 2
    extend = 1  # 1px beyond rectangle on each side
    draw.line([(m - extend, cy), (DOOR_SIZE - m + extend - 1, cy)], fill=BLACK)
    return img


def gen_secret():
    """Secret door: wall stubs + S character.

    Reference pattern (19x19):
      cols 8-10: wall stub rows 0-4 and rows 14-18
      col 9: connector line at row 5 and row 13
      S shape: rows 6-12, cols 6-12
    """
    img, draw = _new(DOOR_SIZE, DOOR_SIZE)
    _wall_stub_v(draw, DOOR_SIZE, DOOR_SIZE, 5, 5)

    cx = DOOR_SIZE // 2  # 9

    # Connector lines from wall stub to S
    img.putpixel((cx, 5), BLACK)
    img.putpixel((cx, 13), BLACK)

    # S shape: geometric, built from horizontal and vertical segments
    # S occupies roughly cols 6-12, rows 6-12
    sx1, sx2 = 6, 12
    sy1, sy2 = 6, 12
    scy = (sy1 + sy2) // 2  # row 9

    # Top bar
    draw.line([(sx1, sy1), (sx2, sy1)], fill=BLACK)
    # Left side, top half
    draw.line([(sx1, sy1), (sx1, scy)], fill=BLACK)
    # Middle bar
    draw.line([(sx1, scy), (sx2, scy)], fill=BLACK)
    # Right side, bottom half
    draw.line([(sx2, scy), (sx2, sy2)], fill=BLACK)
    # Bottom bar
    draw.line([(sx1, sy2), (sx2, sy2)], fill=BLACK)

    return img


def gen_up():
    """Stair up: left wall block + vertical hatching lines.

    Reference pattern (44x19):
      cols 0-6: solid black wall block (7px wide, full height)
      cols 9,11,13,...,43: alternating 1px black stripes (1px on, 1px off)
    """
    img, draw = _new(STAIR_W, STAIR_H)

    # Left wall block
    draw.rectangle([0, 0, 6, STAIR_H - 1], fill=BLACK)

    # Alternating vertical stripes: 1px black, 1px white
    for x in range(9, STAIR_W, 2):
        draw.line([(x, 0), (x, STAIR_H - 1)], fill=BLACK)

    return img


def gen_down():
    """Stair down: left wall block + progressive bars forming a wedge.

    Reference pattern (44x19):
      cols 0-6: solid black wall block
      Progressive vertical bars: each bar centered vertically, height
      increases from left to right (forming a triangle/wedge).
    """
    img, draw = _new(STAIR_W, STAIR_H)

    # Left wall block
    draw.rectangle([0, 0, 6, STAIR_H - 1], fill=BLACK)

    # Progressive bars: alternating 1px lines, height grows linearly
    cy = STAIR_H // 2  # center row = 9
    usable = STAIR_W - 9  # cols 9 to 43 = 35 columns
    for x in range(9, STAIR_W, 2):
        t = (x - 8) / usable  # progress 0..1
        half_h = int(t * cy)
        if half_h > 0:
            draw.line([(x, cy - half_h), (x, cy + half_h)], fill=BLACK)

    # Final bar at right edge (full height)
    draw.line([(STAIR_W - 1, 0), (STAIR_W - 1, STAIR_H - 1)], fill=BLACK)

    return img


def main():
    ASSETS_DIR.mkdir(exist_ok=True)

    generators = {
        "archway": gen_archway,
        "portcullis": gen_portcullis,
        "door": gen_door,
        "locked": gen_locked,
        "trapped": gen_trapped,
        "secret": gen_secret,
        "up": gen_up,
        "down": gen_down,
    }

    for name, gen_fn in generators.items():
        img = gen_fn()
        path = ASSETS_DIR / f"{name}.png"
        img.save(path)
        print(f"  {path} ({img.size})")

    print("Done.")


if __name__ == "__main__":
    main()
