"""Core generation logic - shared by CLI and GUI."""

from pathlib import Path

from renderer import render_map
from html_gen import generate_html


def generate_dungeon(dungeon, output_dir, on_progress=None):
    """Generate all output files for a dungeon.

    Args:
        dungeon: Parsed dungeon JSON dict.
        output_dir: Path to save output files.
        on_progress: Optional callback(message) for status updates.

    Returns:
        List of paths to generated files.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    name = dungeon["settings"]["name"]
    n_cols = dungeon["settings"]["n_cols"]
    n_rows = dungeon["settings"]["n_rows"]
    generated = []

    def _log(msg):
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    _log("Generating GM map...")
    gm_img = render_map(dungeon, gm_mode=True)
    gm_path = output_dir / f"{name} ({n_cols} x {n_rows}).png"
    gm_img.save(gm_path)
    generated.append(gm_path)
    _log(f"  Saved: {gm_path}")

    _log("Generating player map...")
    player_img = render_map(dungeon, cell_size=50, gm_mode=False)
    player_path = output_dir / f"{name} (player, {n_cols} x {n_rows}).png"
    player_img.save(player_path)
    generated.append(player_path)
    _log(f"  Saved: {player_path}")

    _log("Generating HTML...")
    html_path = output_dir / f"{name}.html"
    html_content = generate_html(dungeon, gm_img)
    with open(html_path, "w") as f:
        f.write(html_content)
    generated.append(html_path)
    _log(f"  Saved: {html_path}")

    _log("Done!")
    return generated
