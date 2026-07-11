"""CLI entry point for donjon-regen."""

import argparse
import json
import sys
from pathlib import Path

from generate import generate_dungeon, Output_Overwrite_Conflict
from renderer import RENDER_SCALE


def _scale_arg(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("scale must be a positive integer")
    return n


def _confirm_overwrite(paths):
    """Ask before clobbering existing outputs. False means abort."""
    print("These files already exist and will be overwritten:", file=sys.stderr)
    for p in paths:
        print(f"  {p}", file=sys.stderr)
    if not sys.stdin.isatty():
        print("Re-run with --force to overwrite them.", file=sys.stderr)
        return False
    return input("Overwrite? [y/N] ").strip().lower() in ("y", "yes")


def main():
    parser = argparse.ArgumentParser(
        description="Regenerate donjon dungeon maps and HTML from JSON.",
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        help="Path to the dungeon JSON file (omit when using --gui)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        help="Directory to save output files (default: renders/)",
    )
    parser.add_argument(
        "-s", "--scale",
        type=_scale_arg,
        default=RENDER_SCALE,
        help=f"Render scale multiplier — applied to GM and player cell sizes "
             f"(default: {RENDER_SCALE})",
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Overwrite existing output files without asking",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the PySide6 GUI viewer/editor instead of rendering to disk",
    )
    args = parser.parse_args()

    if args.gui:
        from app import main as gui_main
        gui_main()
        return

    if not args.json_file:
        parser.error("json_file is required unless --gui is given")

    json_path = Path(args.json_file)
    output_dir = Path(args.output_dir) if args.output_dir else Path("renders")

    with open(json_path) as f:
        dungeon = json.load(f)

    try:
        generate_dungeon(
            dungeon, output_dir, render_scale=args.scale, force=args.force
        )
    except Output_Overwrite_Conflict as conflict:
        if not _confirm_overwrite(conflict.paths):
            print("Aborted; nothing written.", file=sys.stderr)
            sys.exit(1)
        generate_dungeon(dungeon, output_dir, render_scale=args.scale, force=True)


if __name__ == "__main__":
    main()
