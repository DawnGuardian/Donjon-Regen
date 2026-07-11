# Donjon Regen

Recreate [donjon.bin.sh](https://donjon.bin.sh/5e/dungeon/) dungeon-generator
output from the exported JSON file — GM map PNG, player map PNG, HTML, TSV,
CSV — using Python + Pillow. Includes a PySide6 GUI that doubles as an
in-place editor (draw corridors, create / reshape / delete rooms, resize the
canvas, mirror rooms across an axis).

This README has two parts:

1. **[User Guide](#user-guide)** — install and run the app, and get past the
   "unidentified developer" warning. Start here.
2. **[Technical Reference](#technical-reference)** — features, CLI, editor
   internals, output formats, packaging, and code signing. For developers.

---

# User Guide

## Install and run from source (step by step)

The app runs from source with [`uv`](https://docs.astral.sh/uv/), which handles
Python and every dependency for you — you do **not** need to install Python
yourself.

### 1. Install `uv`

- **macOS / Linux** — open Terminal and run:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Windows** — open PowerShell and run:
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

Close and reopen the terminal afterwards so `uv` is on your `PATH`. Confirm it
with `uv --version`.

### 2. Get the code

Clone the repository (or download the ZIP from the project page and unzip it),
then move into the folder:

```bash
git clone <this-repo-url> Donjon-Regen
cd Donjon-Regen
```

### 3. Install the dependencies

From inside the `Donjon-Regen` folder:

```bash
uv sync
```

This creates a local `.venv` and installs Pillow, NumPy, and PySide6 into it.
It only needs to be done once (and again after you pull updates).

### 4. Launch the app

```bash
uv run python app.py
```

The GUI opens. Use **Open JSON…** to load a dungeon exported from donjon, then
click any cell for details or use the toolbar tools to edit. To render output
files from the command line instead, see
[CLI usage](#cli) in the Technical Reference.

## Build your own executable (optional)

If you'd rather have a double-clickable app than type `uv run` every time, you
can build one yourself from the same source. It's a single command, and it
produces exactly what the Releases page ships.

From inside the `Donjon-Regen` folder, after `uv sync`:

```bash
uv run pyinstaller donjon-regen.spec --noconfirm
```

The bundle lands in a new `dist/` folder:

- **macOS** → `dist/Donjon Regen.app` — drag it to Applications.
- **Windows** → `dist/Donjon Regen/Donjon Regen.exe` — keep the whole
  `Donjon Regen` folder together; the `.exe` needs the files beside it.

A few things worth knowing:

- **You build for the machine you're on.** PyInstaller can't cross-compile, so
  a Mac produces the `.app` and a Windows PC produces the `.exe`.
- **`uv sync` already installed PyInstaller** — it's a development dependency
  and comes down by default. If you ran `uv sync --no-dev`, re-run plain
  `uv sync` first or the command above won't be found.
- **An app you built yourself won't trip Gatekeeper or SmartScreen** the way a
  downloaded one does, because it never gets the "downloaded from the internet"
  quarantine flag.

See [Packaging & distribution](#packaging--distribution) in the Technical
Reference for the spec file, the LGPL note, and code signing.

## Getting past the "unidentified developer" warning

If you downloaded a **prebuilt desktop bundle** from the Releases page (rather
than running from source), the app is not yet signed with a paid developer
certificate, so your OS will warn you the first time you open it. This is
expected. Running from source (the steps above) does **not** trigger these
warnings.

### macOS

1. Unzip the download and move **Donjon Regen.app** to your Applications folder
   (or anywhere you like).
2. **Right-click** (or Control-click) the app and choose **Open**.
3. A dialog appears warning the app is from an unidentified developer — click
   **Open** again to confirm.

You only need to do this once; macOS remembers your choice.

If macOS says the app "is damaged and can't be opened," clear the quarantine
flag from Terminal, then open it normally:

```bash
xattr -dr com.apple.quarantine "/Applications/Donjon Regen.app"
```

### Windows

1. Unzip the download and run **Donjon Regen.exe** inside the extracted folder.
2. A blue **Windows protected your PC** (SmartScreen) box appears — click
   **More info**.
3. Click the **Run anyway** button that appears.

### Verify your download (optional)

Each release also ships a `.sha256` checksum file. To confirm the ZIP wasn't
corrupted or tampered with:

```bash
# macOS / Linux
shasum -a 256 -c Donjon-Regen-macos.zip.sha256
```

```powershell
# Windows — compare the printed hash against the .sha256 file's contents
(Get-FileHash Donjon-Regen-windows.zip -Algorithm SHA256).Hash
```

---

# Technical Reference

## Features

- **Faithful re-render** of donjon's GM and player maps from the exported
  JSON: labeled rooms, corridor-feature letters, polymorph (polygon / circle)
  rooms with crisp pixel-aligned edges, six door types with procedurally drawn
  symbols (no resampling, no anti-aliasing), and stair-up / stair-down
  hatching.
- **HTML page** — embedded map with clickable image-map areas + per-room
  detail tables.
- **TSV / CSV grid** — TSV is byte-identical to donjon's reference; CSV is the
  same data with comma delimiters.
- **PySide6 GUI** — zoom / pan viewer with a structured Room Editor and a
  toolbar that turns the same view into an editor (corridor brush, room
  rectangle, eraser, canvas resize, mirror rooms).
- **User-settable render scale** (`-s/--scale 1..8`) — default 2× makes
  polygon edges, door symbols, and labels noticeably sharper than donjon's
  reference output at the cost of 4× pixel area.

## Install

```bash
uv sync
```

Requires Python ≥ 3.14. Dependencies (Pillow ≥ 12, NumPy ≥ 2.4, PySide6 ≥ 6.8)
are managed by `uv` in a local `.venv`.

## Usage

### CLI

```bash
# Render to JSON / PNG / HTML / TSV / CSV (default output dir: renders/)
uv run python regen.py path/to/dungeon.json

# Override the output directory and the render scale
uv run python regen.py path/to/dungeon.json -o ./out -s 4

# Overwrite pre-existing outputs without the confirmation prompt
uv run python regen.py path/to/dungeon.json -f

# Launch the GUI from the CLI entry point
uv run python regen.py --gui

# …or launch the GUI directly (this is the packaging entry point)
uv run python app.py
```

### GUI

Open a donjon JSON via the toolbar (**Open JSON…**), then click any cell on
the map for details. Saved output (**Save Outputs…**) runs on a background
thread so the UI stays responsive.

#### Viewing

| Action               | Mapping                                                 |
|----------------------|---------------------------------------------------------|
| Mouse wheel          | Zoom (anchored under cursor)                            |
| Middle-button drag   | Pan                                                     |
| Left-click           | Open details / room editor for the cell                 |
| Ctrl + 0             | Fit to window                                           |
| Scale spinbox        | Change the render-scale multiplier (1–8, default 2)     |

#### Editing tools

The toolbar's tool group switches the left-click behavior:

| Tool       | Behavior                                                                |
|------------|-------------------------------------------------------------------------|
| Select     | Open cell details (default — preserves the original viewer behavior).   |
| Corridor   | Click or drag to paint corridor tiles. Refuses to overwrite room cells. |
| Room       | Click-and-drag to draw a rectangle; on release, a shape dialog appears (Rectangle / Polygon-N / Circle — polymorph options unlock only for square bbox). |
| Eraser     | Click or drag to clear tiles (resets each cell to void).                |

#### Room editor (open by selecting any room cell)

- **Summary** line + derived **Inhabited** display.
- Per-detail-section list editors (monsters, hidden treasure, …) with
  separator-aware add buttons.
- **Doors** rows with a type combo (arch / door / locked / trapped / secret /
  portcullis) and a type-dependent extra field (trap / hint).
- **Geometry** section: shape combo (Rectangle / Polygon / Circle), Polygon-N
  spinbox, and four signed perimeter Δ spinboxes (N / S / W / E). Apply runs
  resize first then reshape, so a non-square room can be grown to square *and*
  converted to polygon/circle in a single Apply.
- **Apply Changes** flushes everything to the in-memory dungeon and
  re-renders the map when doors or geometry changed.
- **Delete Room** removes the room, clears its cells, and strips door-type
  bits from the room's door cells.

#### Structural actions

- **Resize Canvas…** — 4-direction signed deltas (N / S / W / E in cells).
  Positive grows that edge, negative shrinks it. Shrinking is allowed: if any
  rooms or stairs would fall outside the new bounds the dialog confirms
  before discarding them.
- **Mirror Rooms…** — pick axis (horizontal / vertical), source side, and
  pivot index. Overwrite mode: destination-side rooms overlapping the
  mirrored region are deleted first. Corridors and stairs are NOT mirrored;
  rooms straddling the pivot are skipped.

## Output formats

| File                                  | Description                                       |
|---------------------------------------|---------------------------------------------------|
| `<name>.json`                         | The dungeon itself, including any edits — re-openable, and re-importable by VTT tools that read donjon's export |
| `<name> (cols × rows).png`            | GM map — labeled rooms + corridor features        |
| `<name> (player, cols × rows).png`    | Player map — same geometry, no labels             |
| `<name>.html`                         | Embedded map with image-map areas + detail tables |
| `<name>.tsv`                          | Tab-separated grid (byte-identical to donjon)     |
| `<name>.csv`                          | Comma-separated version of the same grid          |

Every output is named from `settings.name`, so saving into the directory your
source `.json` came from **overwrites that source**. Both surfaces warn first:
the CLI prompts (bypass with `-f/--force`, and it aborts rather than prompting
when stdin isn't a terminal), and the GUI shows a confirmation dialog listing
the affected files. Nothing is written until you confirm.

## Packaging & distribution

The GUI can be frozen into a standalone desktop bundle — a `.app` on macOS and
a one-folder `.exe` on Windows — with [PyInstaller](https://pyinstaller.org).
`app.py` is the bundle entry point (its no-arg `main()` opens the GUI).

```bash
# Build for the OS you're currently on (output in dist/)
uv run pyinstaller donjon-regen.spec --noconfirm
#   macOS  → dist/Donjon Regen.app
#   Windows → dist/Donjon Regen/Donjon Regen.exe
```

Notes:

- **Per-OS builds.** PyInstaller cannot cross-compile: build the `.app` on
  macOS and the `.exe` on Windows. The `.github/workflows/build.yml` pipeline
  does both on GitHub-hosted runners — it builds on every manual dispatch and,
  when a `v*` tag is pushed, attaches the zipped bundles to a GitHub Release.
- **No bundled data.** Door/stair glyphs are drawn procedurally and label
  fonts resolve from the host OS, so neither `assets/` nor any font files are
  packaged. Nothing extra to ship.
- **Qt is LGPL.** PySide6/Qt are dynamically linked by PyInstaller, which keeps
  the build LGPL-compliant; ship the Qt LGPL license text with any public
  distribution.

### Code signing

The CI pipeline signs the bundles **automatically when the signing secrets are
configured**, and falls back to unsigned builds (plus published SHA-256
checksums) when they aren't — so the workflow succeeds either way. See
[Getting past the "unidentified developer" warning](#getting-past-the-unidentified-developer-warning)
for the end-user side.

To enable signing, add these repository secrets
(**Settings → Secrets and variables → Actions**):

| Platform | Secrets | What it does |
|----------|---------|--------------|
| macOS    | `MACOS_CERT_P12_BASE64`, `MACOS_CERT_PASSWORD`, `MACOS_SIGN_IDENTITY`, `MACOS_NOTARY_APPLE_ID`, `MACOS_NOTARY_PASSWORD`, `MACOS_NOTARY_TEAM_ID` | Developer-ID-signs the `.app` with the hardened runtime + [`entitlements.plist`](entitlements.plist), then notarizes (`notarytool`) and staples it. Requires an Apple Developer Program membership ($99/yr). |
| Windows  | `WINDOWS_CERT_PFX_BASE64`, `WINDOWS_CERT_PASSWORD` | Authenticode-signs the `.exe` with `signtool` (SHA-256, RFC-3161 timestamp). Requires a code-signing certificate from a CA (or one exported to `.pfx`). |

`base64`-encode a certificate for the secret with
`base64 -i cert.p12` (macOS) or
`[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))` (PowerShell).

**Until signing is enabled, bundles are unsigned.** The macOS `.app` gets an
ad-hoc signature so Apple Silicon can launch it, but it isn't notarized; the
Windows `.exe` is unsigned. Users bypass the OS gatekeepers as described in the
[User Guide](#getting-past-the-unidentified-developer-warning).

## Project layout

```
regen.py               # CLI entry point (also launches the GUI with --gui)
app.py                 # PySide6 GUI: viewer + editor + structural ops (app entry point)
generate.py            # Shared generation pipeline used by CLI and GUI
cells.py               # Cell-bitmask constants & helpers
dungeon_ops.py         # Pure mutations on the in-memory dungeon dict
renderer.py            # PIL-based GM / player map renderer
html_gen.py            # HTML generator
table_gen.py           # TSV + CSV writer (shared)
gen_assets.py          # Procedural door / stair symbol generator
assets/                # 19×19 reference symbol PNGs (regenerated by gen_assets.py)
donjon-regen.spec      # PyInstaller build spec (targets app.py)
entitlements.plist     # macOS hardened-runtime entitlements (used when signing)
.github/workflows/     # CI: builds + signs the macOS .app + Windows .exe bundles
```

`CLAUDE.md` has the full notes on the cell-bitmask encoding, the cells-array
padding offset, the rendering pipeline, and the `dungeon_ops` invariants —
useful if you want to extend the editor or integrate with another tool.
