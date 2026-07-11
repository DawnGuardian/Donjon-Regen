"""Core generation logic - shared by CLI and GUI."""

import json
from pathlib import Path

from renderer import render_map, RENDER_SCALE
from html_gen import generate_html
from table_gen import build_grid, write_table


class Output_Overwrite_Conflict(Exception):
    """Raised when generating would clobber files that already exist.

    Callers pre-flight with ``existing_outputs`` (or catch this) and re-run
    with ``force=True`` once the user has confirmed.
    """

    def __init__(self, paths):
        self.paths = list(paths)
        joined = ", ".join(p.name for p in self.paths)
        super().__init__(
            f"{len(self.paths)} existing file(s) would be overwritten: {joined}"
        )


def output_paths(dungeon, output_dir):
    """Every path ``generate_dungeon`` writes, in write order.

    Filenames depend only on ``settings``, never on the render scale, so this
    is safe to call before rendering. ``generate_dungeon`` unpacks this same
    list, which keeps the two from drifting apart.
    """
    output_dir = Path(output_dir)
    settings = dungeon["settings"]
    name = settings["name"]
    n_cols = settings["n_cols"]
    n_rows = settings["n_rows"]
    return [
        output_dir / f"{name}.json",
        output_dir / f"{name} ({n_cols} x {n_rows}).png",
        output_dir / f"{name} (player, {n_cols} x {n_rows}).png",
        output_dir / f"{name}.html",
        output_dir / f"{name}.tsv",
        output_dir / f"{name}.csv",
    ]


def existing_outputs(dungeon, output_dir):
    """Subset of ``output_paths`` already present on disk."""
    return [p for p in output_paths(dungeon, output_dir) if p.exists()]


def generate_dungeon(
    dungeon, output_dir, render_scale=RENDER_SCALE, on_progress=None, force=False
):
    """Generate all output files for a dungeon.

    Args:
        dungeon: Parsed dungeon JSON dict.
        output_dir: Path to save output files.
        render_scale: Multiplier applied to the JSON's authoring cell
            sizes for the GM and player PNGs (defaults to RENDER_SCALE).
        on_progress: Optional callback(message) for status updates.
        force: Overwrite pre-existing output files instead of raising.

    Returns:
        List of paths to generated files.

    Raises:
        Output_Overwrite_Conflict: Any output file already exists and
            ``force`` is False. Nothing is written in that case.
    """
    output_dir = Path(output_dir)
    json_path, gm_path, player_path, html_path, tsv_path, csv_path = output_paths(
        dungeon, output_dir
    )

    # Pre-flight before mkdir or any write, so a refusal leaves the disk
    # untouched. The dungeon JSON is written into output_dir under its own
    # settings.name, so this is also what stops an edited dungeon from
    # silently clobbering the source export it was loaded from.
    clashes = existing_outputs(dungeon, output_dir)
    if clashes and not force:
        raise Output_Overwrite_Conflict(clashes)

    output_dir.mkdir(parents=True, exist_ok=True)

    settings = dungeon["settings"]
    gm_cell_size = settings["cell_size"] * render_scale
    player_cell_size = 50 * render_scale
    generated = []

    def _log(msg):
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    # Written first: the edited dungeon dict is the only artifact that can't be
    # regenerated from the others, so it survives a later rendering failure.
    _log("Saving dungeon JSON...")
    with open(json_path, "w") as f:
        json.dump(dungeon, f, separators=(",", ":"))
    generated.append(json_path)
    _log(f"  Saved: {json_path}")

    _log("Generating GM map...")
    gm_img = render_map(dungeon, cell_size=gm_cell_size, gm_mode=True)
    gm_img.save(gm_path)
    generated.append(gm_path)
    _log(f"  Saved: {gm_path}")

    _log("Generating player map...")
    player_img = render_map(dungeon, cell_size=player_cell_size, gm_mode=False)
    player_img.save(player_path)
    generated.append(player_path)
    _log(f"  Saved: {player_path}")

    _log("Generating HTML...")
    html_content = generate_html(dungeon, gm_img, cell_size=gm_cell_size)
    with open(html_path, "w") as f:
        f.write(html_content)
    generated.append(html_path)
    _log(f"  Saved: {html_path}")

    _log("Generating TSV/CSV...")
    grid = build_grid(dungeon)
    generated.append(write_table(grid, tsv_path, "\t"))
    _log(f"  Saved: {tsv_path}")
    generated.append(write_table(grid, csv_path, ","))
    _log(f"  Saved: {csv_path}")

    _log("Done!")
    return generated
