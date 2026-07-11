# Changelog

All notable changes to this project are documented in this file.
This file is generated from the commit history -- do not edit it by hand.

## Unreleased

### Features

- **generate**: Write the dungeon JSON back out alongside renders

## 1.0.0 - 2026-06-16

### Features

- Render donjon dungeon maps and HTML from exported JSON
- **gui**: Add a viewer and make input/output paths configurable
- **renderer**: Draw door symbols from extracted key.png assets
- **gen_assets**: Generate door/stair symbols programmatically
- **renderer**: Anti-alias polymorph room fills
- **gui**: Rebuild on PySide6 with a wandering-monster panel
- **gui**: Add a structured room editor
- Add TSV/CSV export and a user-settable render scale
- **dungeon_ops**: Add structural edit ops and GUI tool modes
- **dungeon_ops**: Add room geometry ops and room mirroring
- **dungeon_ops**: Auto-insert doors where corridors meet rooms

### Bug Fixes

- **renderer**: Render polymorph rooms as real polygons and circles
- **renderer**: Clip grid-line stubs protruding past polygon edges
- **renderer**: Drop the WALL_COLOR fringe and alias label glyphs
- **gen_assets**: Scale door-symbol strokes with cell size

### Documentation

- Note the deliberate 1-bit aliased label rendering

### Build

- Package the GUI as a distributable desktop app


