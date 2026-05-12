"""Procedural door and stair symbols, parameterized by target pixel size.

The drawing functions can produce a symbol at any target size; the canonical
master proportions are defined at DOOR_BASE_SIZE = 19 and STAIR_BASE_W/H, and
all measurements scale linearly from there. Running this module as a script
dumps reference PNGs at the master sizes into assets/.

Symbol orientation: vertical wall (wall stubs at top/bottom center, symbol
opens left-right). The renderer rotates 90° at composite time for horizontal
walls. Symbols are drawn black-on-white; `door_symbol_rgba` post-processes to
black-on-transparent for compositing.

Stroke scaling: thin interior features (door panel outline, S-glyph strokes,
locked/trapped/portcullis bars) scale linearly from 1px at the 19×19
reference (via `_stroke(size)`), so symbols stay readable at any cell_size
instead of degrading to hairlines against the thicker stubs.
"""

from PIL import Image, ImageDraw
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

DOOR_BASE_SIZE = 19
STAIR_BASE_W = 44
STAIR_BASE_H = 19


def _new(w, h):
    img = Image.new("RGB", (w, h), WHITE)
    return img, ImageDraw.Draw(img)


def _scale(base_value, size):
    """Scale a measurement defined at DOOR_BASE_SIZE to target `size`."""
    return max(1, round(base_value * size / DOOR_BASE_SIZE))


def _odd(n):
    """Force `n` to the nearest odd integer >= 1 (rounding down) so centered
    features are pixel-symmetric (a 4-px-wide bar can't be centered on a
    single column)."""
    n = max(1, n)
    return n if n % 2 == 1 else n - 1


def _odd_at_least(n):
    """Smallest odd integer >= n (and >= 1). Use for stroke widths that must
    stay pixel-symmetric around a center; rounding up keeps the stroke
    visible rather than collapsing to the next-thinner odd value."""
    n = max(1, n)
    return n if n % 2 == 1 else n + 1


def _stroke(size):
    """Thin-feature stroke thickness for a symbol drawn at `size`. Linear
    scale from 1px at the 19×19 reference."""
    return _scale(1, size)


def _wall_stub_v(draw, w, h, stub_h, bar_w):
    """Draw vertical wall stubs of `bar_w` width and `stub_h` height at the
    top and bottom center of the canvas."""
    cx = w // 2
    x1 = cx - bar_w // 2
    x2 = x1 + bar_w - 1
    draw.rectangle([x1, 0, x2, stub_h - 1], fill=BLACK)
    draw.rectangle([x1, h - stub_h, x2, h - 1], fill=BLACK)


def _vbar_c(draw, cx, y1, y2, stroke):
    """Vertical bar of width `stroke` centered on column `cx`, from y1..y2
    inclusive. Pass an odd `stroke` for pixel-symmetric centering."""
    half = stroke // 2
    draw.rectangle([cx - half, y1, cx + stroke - half - 1, y2], fill=BLACK)


def _hbar_c(draw, cy, x1, x2, stroke):
    """Horizontal bar of height `stroke` centered on row `cy`, from x1..x2
    inclusive. Pass an odd `stroke` for pixel-symmetric centering."""
    half = stroke // 2
    draw.rectangle([x1, cy - half, x2, cy + stroke - half - 1], fill=BLACK)


# --- Generator functions ---------------------------------------------------
#
# Each generator returns a fresh RGB image (black-on-white) at the requested
# size. Pure integer drawing — no anti-aliasing, no resampling — so straight
# lines stay straight at any output size.

def gen_archway(size=DOOR_BASE_SIZE):
    """Wall stubs at top and bottom with an open gap between them."""
    img, draw = _new(size, size)
    bar_w = _odd(_scale(3, size))
    stub_h = _scale(5, size)
    _wall_stub_v(draw, size, size, stub_h, bar_w)
    return img


def gen_portcullis(size=DOOR_BASE_SIZE):
    """Wall stubs + thin vertical line + three crossbar dashes in the gap."""
    img, draw = _new(size, size)
    bar_w = _odd(_scale(3, size))
    stub_h = _scale(5, size)
    stroke_c = _odd_at_least(_stroke(size))
    _wall_stub_v(draw, size, size, stub_h, bar_w)

    cx = size // 2
    gap_top = stub_h
    gap_bottom = size - stub_h - 1
    if gap_bottom <= gap_top:
        return img
    gap_h = gap_bottom - gap_top + 1

    # Continuous vertical line through the gap center
    _vbar_c(draw, cx, gap_top, gap_bottom, stroke_c)

    # Three horizontal dashes at 1/4, 1/2, 3/4 of the gap; same horizontal
    # span as the wall stubs so the dashes read as bars of the portcullis.
    dash_w = bar_w
    dx = dash_w // 2
    for frac in (0.25, 0.5, 0.75):
        y = gap_top + round(frac * (gap_h - 1))
        _hbar_c(draw, y, cx - dx, cx + dx, stroke_c)
    return img


def _draw_door_rect(draw, size, m, stroke):
    """Door panel rectangle outline at margin `m`, outline thickness `stroke`
    (PIL thickens the outline inward from the bbox edge)."""
    draw.rectangle([m, m, size - m - 1, size - m - 1],
                   outline=BLACK, width=stroke)


def gen_door(size=DOOR_BASE_SIZE):
    """Wall stubs + a square panel (door) outline between them."""
    img, draw = _new(size, size)
    bar_w = _odd(_scale(3, size))
    stub_h = _scale(4, size)
    stroke = _stroke(size)
    _wall_stub_v(draw, size, size, stub_h, bar_w)
    _draw_door_rect(draw, size, stub_h, stroke)
    return img


def gen_locked(size=DOOR_BASE_SIZE):
    """Door panel + a vertical bar through the panel center."""
    img, draw = _new(size, size)
    bar_w = _odd(_scale(3, size))
    stub_h = _scale(4, size)
    stroke = _stroke(size)
    stroke_c = _odd_at_least(stroke)
    _wall_stub_v(draw, size, size, stub_h, bar_w)
    _draw_door_rect(draw, size, stub_h, stroke)
    cx = size // 2
    _vbar_c(draw, cx, stub_h, size - stub_h - 1, stroke_c)
    return img


def gen_trapped(size=DOOR_BASE_SIZE):
    """Door panel + a horizontal bar through the panel center, extending
    one stroke-thickness beyond the panel on each side."""
    img, draw = _new(size, size)
    bar_w = _odd(_scale(3, size))
    stub_h = _scale(4, size)
    stroke = _stroke(size)
    stroke_c = _odd_at_least(stroke)
    _wall_stub_v(draw, size, size, stub_h, bar_w)
    _draw_door_rect(draw, size, stub_h, stroke)
    cy = size // 2
    extend = stroke
    _hbar_c(draw, cy, stub_h - extend, size - stub_h + extend - 1, stroke_c)
    return img


def gen_secret(size=DOOR_BASE_SIZE):
    """Wall stubs + connector marks + an angular `S` glyph in the gap."""
    img, draw = _new(size, size)
    bar_w = _odd(_scale(3, size))
    stub_h = _scale(5, size)
    stroke = _stroke(size)
    stroke_c = _odd_at_least(stroke)
    _wall_stub_v(draw, size, size, stub_h, bar_w)

    cx = size // 2
    # Centered connector marks at the inner edge of each stub, bridging
    # stub → S region. stroke_c × stroke_c squares centered on cx.
    half = stroke_c // 2
    draw.rectangle([cx - half, stub_h,
                    cx + stroke_c - half - 1, stub_h + stroke_c - 1],
                   fill=BLACK)
    draw.rectangle([cx - half, size - stub_h - stroke_c,
                    cx + stroke_c - half - 1, size - stub_h - 1],
                   fill=BLACK)

    # S-glyph inside the gap, scaled from the original 19×19 design (which
    # places the S at margin=6 horizontally, with one row of padding above
    # and below the stubs).
    sx_margin = _scale(6, size)
    sy_margin = stub_h + 1
    sx1 = sx_margin
    sx2 = size - sx_margin - 1
    sy1 = sy_margin
    sy2 = size - sy_margin - 1
    if sx1 >= sx2 or sy1 >= sy2:
        return img  # too small to render the S — leave just the stubs
    scy = (sy1 + sy2) // 2

    # Edge strokes thicken inward (their outer edge sits on the S bbox);
    # the middle bar is centered on scy and uses the odd stroke variant
    # to stay pixel-symmetric.
    draw.rectangle([sx1, sy1, sx2, sy1 + stroke - 1], fill=BLACK)    # top
    draw.rectangle([sx1, sy1, sx1 + stroke - 1, scy], fill=BLACK)    # left-top
    _hbar_c(draw, scy, sx1, sx2, stroke_c)                           # middle
    draw.rectangle([sx2 - stroke + 1, scy, sx2, sy2], fill=BLACK)    # right-bot
    draw.rectangle([sx1, sy2 - stroke + 1, sx2, sy2], fill=BLACK)    # bottom
    return img


def gen_up(size_w=STAIR_BASE_W, size_h=STAIR_BASE_H):
    """Stair up: solid wall block on the left + alternating vertical stripes."""
    img, draw = _new(size_w, size_h)
    wall_w = max(1, round(size_w * 7 / STAIR_BASE_W))
    draw.rectangle([0, 0, wall_w - 1, size_h - 1], fill=BLACK)
    for x in range(wall_w + 2, size_w, 2):
        draw.line([(x, 0), (x, size_h - 1)], fill=BLACK)
    return img


def gen_down(size_w=STAIR_BASE_W, size_h=STAIR_BASE_H):
    """Stair down: solid wall block on the left + progressive bars (wedge)."""
    img, draw = _new(size_w, size_h)
    wall_w = max(1, round(size_w * 7 / STAIR_BASE_W))
    draw.rectangle([0, 0, wall_w - 1, size_h - 1], fill=BLACK)
    cy = size_h // 2
    usable = size_w - (wall_w + 2)
    if usable <= 0:
        return img
    for x in range(wall_w + 2, size_w, 2):
        t = (x - (wall_w + 1)) / usable
        half_h = int(t * cy)
        if half_h > 0:
            draw.line([(x, cy - half_h), (x, cy + half_h)], fill=BLACK)
    draw.line([(size_w - 1, 0), (size_w - 1, size_h - 1)], fill=BLACK)
    return img


# --- Public dispatcher used by the renderer --------------------------------

_DOOR_GENS = {
    "archway": gen_archway,
    "portcullis": gen_portcullis,
    "door": gen_door,
    "locked": gen_locked,
    "trapped": gen_trapped,
    "secret": gen_secret,
}


def door_symbol_rgba(name, size):
    """Return an RGBA `size`×`size` door symbol, black on transparent.

    Drawn fresh at the requested size with integer drawing only — no
    resampling, no anti-aliasing — so straight lines stay straight and
    1-pixel features stay crisp regardless of cell_size.
    """
    rgb = _DOOR_GENS[name](size)
    rgba = rgb.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, _a = pixels[x, y]
            if r > 200 and g > 200 and b > 200:
                pixels[x, y] = (0, 0, 0, 0)
            else:
                pixels[x, y] = (0, 0, 0, 255)
    return rgba


# --- Reference-asset CLI: dump 19x19 / 44x19 PNGs to assets/ ---------------

def main():
    ASSETS_DIR.mkdir(exist_ok=True)

    static_outputs = {
        "archway": gen_archway,
        "portcullis": gen_portcullis,
        "door": gen_door,
        "locked": gen_locked,
        "trapped": gen_trapped,
        "secret": gen_secret,
        "up": gen_up,
        "down": gen_down,
    }

    for name, gen_fn in static_outputs.items():
        img = gen_fn()
        path = ASSETS_DIR / f"{name}.png"
        img.save(path)
        print(f"  {path} ({img.size})")

    print("Done.")


if __name__ == "__main__":
    main()
