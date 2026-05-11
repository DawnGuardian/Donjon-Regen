"""Core generation logic - shared by CLI and GUI."""

from pathlib import Path

from renderer import render_map, RENDER_SCALE
from html_gen import generate_html
from table_gen import build_grid, write_table


def generate_dungeon(dungeon, output_dir, render_scale=RENDER_SCALE, on_progress=None):
    """Generate all output files for a dungeon.

    Args:
        dungeon: Parsed dungeon JSON dict.
        output_dir: Path to save output files.
        render_scale: Multiplier applied to the JSON's authoring cell
            sizes for the GM and player PNGs (defaults to RENDER_SCALE).
        on_progress: Optional callback(message) for status updates.

    Returns:
        List of paths to generated files.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = dungeon["settings"]
    name = settings["name"]
    n_cols = settings["n_cols"]
    n_rows = settings["n_rows"]
    gm_cell_size = settings["cell_size"] * render_scale
    player_cell_size = 50 * render_scale
    generated = []

    def _log(msg):
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    _log("Generating GM map...")
    gm_img = render_map(dungeon, cell_size=gm_cell_size, gm_mode=True)
    gm_path = output_dir / f"{name} ({n_cols} x {n_rows}).png"
    gm_img.save(gm_path)
    generated.append(gm_path)
    _log(f"  Saved: {gm_path}")

    _log("Generating player map...")
    player_img = render_map(dungeon, cell_size=player_cell_size, gm_mode=False)
    player_path = output_dir / f"{name} (player, {n_cols} x {n_rows}).png"
    player_img.save(player_path)
    generated.append(player_path)
    _log(f"  Saved: {player_path}")

    _log("Generating HTML...")
    html_path = output_dir / f"{name}.html"
    html_content = generate_html(dungeon, gm_img, cell_size=gm_cell_size)
    with open(html_path, "w") as f:
        f.write(html_content)
    generated.append(html_path)
    _log(f"  Saved: {html_path}")

    _log("Generating TSV/CSV...")
    grid = build_grid(dungeon)
    tsv_path = write_table(grid, output_dir / f"{name}.tsv", "\t")
    generated.append(tsv_path)
    _log(f"  Saved: {tsv_path}")
    csv_path = write_table(grid, output_dir / f"{name}.csv", ",")
    generated.append(csv_path)
    _log(f"  Saved: {csv_path}")

    _log("Done!")
    return generated
