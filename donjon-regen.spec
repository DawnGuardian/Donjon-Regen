# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the Donjon Regen desktop app.

    Build:  uv run pyinstaller donjon-regen.spec
    Output: dist/Donjon Regen.app   (macOS)
            dist/Donjon Regen/       (Windows, Linux — run the exe inside)

Targets ``app.py``, whose no-arg ``main()`` launches the PySide6 GUI.

Notes
-----
* ``assets/`` is intentionally NOT bundled: door/stair glyphs are drawn
  procedurally at runtime (see ``gen_assets.door_symbol_rgba``), and the
  ``assets/`` PNGs are reference-only.
* Label fonts are resolved from the host OS at runtime
  (``renderer._LABEL_FONT_CANDIDATES``), so no font files are bundled either.
* PySide6 / Qt plugins are pulled in automatically by PyInstaller's bundled
  PySide6 hook — keep ``datas``/``binaries`` empty unless that stops working.
* one-dir layout (COLLECT) is used rather than one-file: Qt apps start faster
  and unpack more reliably this way.
* Code signing is done in CI AFTER the build (see .github/workflows/build.yml),
  not here: the macOS .app is Developer-ID-signed with the hardened runtime +
  ``entitlements.plist`` then notarized/stapled, and the Windows .exe is
  Authenticode-signed — all gated on repo secrets. Hence ``codesign_identity``
  stays ``None`` in the spec (a local ``pyinstaller`` build is left unsigned).
"""

APP_NAME = "Donjon Regen"
BUNDLE_ID = "sh.bin.donjon-regen"
VERSION = "1.0.0"

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app — no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,       # let PyInstaller match the build host's arch
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # TODO: add an .icns / .ico when artwork exists
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

# BUNDLE only emits a .app on macOS; it is a no-op on Windows/Linux, where the
# COLLECT directory above is the distributable.
app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=None,
    bundle_identifier=BUNDLE_ID,
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
    },
)
