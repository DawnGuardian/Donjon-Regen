"""CLI entry point for donjon-regen."""

import json
import sys
from pathlib import Path

from renderer import render_map
from html_gen import generate_html


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <dungeon.json> [--output-dir DIR]")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--output-dir" else json_path.parent

    with open(json_path) as f:
        dungeon = json.load(f)

    name = dungeon["settings"]["name"]
    n_cols = dungeon["settings"]["n_cols"]
    n_rows = dungeon["settings"]["n_rows"]

    # Generate GM map (uses cell_size from JSON settings)
    print("Generating GM map...")
    gm_img = render_map(dungeon, gm_mode=True)
    gm_path = output_dir / f"{name} ({n_cols} x {n_rows}).png"
    gm_img.save(gm_path)
    print(f"  Saved: {gm_path}")

    # Generate player map (50px per cell)
    print("Generating player map...")
    player_img = render_map(dungeon, cell_size=50, gm_mode=False)
    player_path = output_dir / f"{name} (player, {n_cols} x {n_rows}).png"
    player_img.save(player_path)
    print(f"  Saved: {player_path}")

    # Generate HTML
    print("Generating HTML...")
    html_path = output_dir / f"{name}.html"
    html_content = generate_html(dungeon, gm_img)
    with open(html_path, "w") as f:
        f.write(html_content)
    print(f"  Saved: {html_path}")

    print("Done!")


if __name__ == "__main__":
    main()
