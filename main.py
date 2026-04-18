"""CLI entry point for donjon-regen."""

import argparse
import json
from pathlib import Path

from generate import generate_dungeon


def main():
    parser = argparse.ArgumentParser(description="Regenerate donjon dungeon maps and HTML from JSON.")
    parser.add_argument("json_file", help="Path to the dungeon JSON file")
    parser.add_argument("-o", "--output-dir", help="Directory to save output files (default: same as JSON file)")
    args = parser.parse_args()

    json_path = Path(args.json_file)
    output_dir = Path(args.output_dir) if args.output_dir else json_path.parent

    with open(json_path) as f:
        dungeon = json.load(f)

    generate_dungeon(dungeon, output_dir)


if __name__ == "__main__":
    main()
