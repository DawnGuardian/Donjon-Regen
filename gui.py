"""Viewer-first PySide6 GUI for donjon-regen.

Loads a dungeon JSON, renders the GM map in a QGraphicsView, and shows
details for the room/corridor under the cursor when clicked.

Map controls:
  - Click           : show details for that cell
  - Mouse wheel     : zoom (centred on cursor)
  - Middle drag     : pan
  - Ctrl/Cmd + 0    : reset zoom to fit window
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import (
    QAction,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import cells as C
from generate import generate_dungeon
from renderer import render_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pil_to_qpixmap(pil_image: Image.Image) -> QPixmap:
    """Convert a PIL Image to a QPixmap via PNG round-trip (lossless, simple)."""
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    qimg = QImage.fromData(buf.getvalue(), "PNG")
    return QPixmap.fromImage(qimg)


# ---------------------------------------------------------------------------
# Map view widget — zoom/pan QGraphicsView
# ---------------------------------------------------------------------------

class Map_View(QGraphicsView):
    """A QGraphicsView with mouse-wheel zoom, middle-button pan, and
    a `cell_clicked` signal emitting (row, col) on left-click."""

    cell_clicked = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setDragMode(QGraphicsView.NoDrag)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._highlight: QGraphicsRectItem | None = None
        self._cell_size = 1

    # -- scene management --------------------------------------------------

    def set_map(self, pixmap: QPixmap, cell_size: int) -> None:
        self._scene.clear()
        self._highlight = None
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._cell_size = cell_size
        self.fit_to_window()

    def fit_to_window(self) -> None:
        if self._pixmap_item is None:
            return
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def highlight_cell(self, row: int, col: int) -> None:
        cs = self._cell_size
        rect = QRectF(col * cs, row * cs, cs, cs)
        if self._highlight is None:
            pen = QPen(Qt.GlobalColor.red)
            pen.setWidth(2)
            pen.setCosmetic(True)
            self._highlight = self._scene.addRect(rect, pen)
            self._highlight.setZValue(10)
        else:
            self._highlight.setRect(rect)

    # -- input -------------------------------------------------------------

    def wheelEvent(self, event):
        if self._pixmap_item is None:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self._pixmap_item is not None
            and self._cell_size > 0
        ):
            scene_pos: QPointF = self.mapToScene(event.position().toPoint())
            col = int(scene_pos.x() // self._cell_size)
            row = int(scene_pos.y() // self._cell_size)
            self.cell_clicked.emit(row, col)
            return
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Background save thread
# ---------------------------------------------------------------------------

class Save_Worker(QThread):
    finished_ok = Signal(int, str)
    failed = Signal(str)

    def __init__(self, dungeon: dict, out_dir: str, parent=None):
        super().__init__(parent)
        self._dungeon = dungeon
        self._out_dir = out_dir

    def run(self):
        try:
            generated = generate_dungeon(self._dungeon, self._out_dir)
            self.finished_ok.emit(len(generated), self._out_dir)
        except Exception as e:
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class Donjon_Viewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Donjon Regen — Viewer")
        self.resize(1280, 800)

        self._dungeon: dict | None = None
        self._json_path: Path | None = None
        self._cell_size = 1
        self._row_off = 0
        self._col_off = 0
        self._save_worker: Save_Worker | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Toolbar
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_action = QAction("Open JSON…", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._open_json)
        toolbar.addAction(open_action)

        self._save_action = QAction("Save Outputs…", self)
        self._save_action.setShortcut(QKeySequence.Save)
        self._save_action.setEnabled(False)
        self._save_action.triggered.connect(self._save_outputs)
        toolbar.addAction(self._save_action)

        toolbar.addSeparator()

        fit_action = QAction("Fit to Window", self)
        fit_action.setShortcut(QKeySequence("Ctrl+0"))
        fit_action.triggered.connect(lambda: self._map_view.fit_to_window())
        toolbar.addAction(fit_action)

        # Central splitter: map | details
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        self._map_view = Map_View()
        self._map_view.cell_clicked.connect(self._on_cell_clicked)
        splitter.addWidget(self._map_view)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(8, 8, 8, 8)

        self._details_title = QLabel("Click a room or corridor")
        title_font = self._details_title.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self._details_title.setFont(title_font)
        self._details_title.setWordWrap(True)
        details_layout.addWidget(self._details_title)

        self._details_text = QTextEdit()
        self._details_text.setReadOnly(True)
        details_layout.addWidget(self._details_text, 1)

        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 380])

        # Status bar
        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("No dungeon loaded.")

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _open_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Dungeon JSON",
            str(Path.cwd()),
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return

        try:
            with open(path) as f:
                dungeon = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, "Error", f"Failed to load JSON:\n{e}")
            return

        self._dungeon = dungeon
        self._json_path = Path(path)
        self._cell_size = dungeon["settings"]["cell_size"]

        cell_data = dungeon["cells"]
        n_rows = dungeon["settings"]["n_rows"]
        n_cols = dungeon["settings"]["n_cols"]
        self._row_off = (len(cell_data) - n_rows) // 2
        self._col_off = (len(cell_data[0]) - n_cols) // 2

        name = dungeon["settings"].get("name", "(unnamed)")
        self.setWindowTitle(f"Donjon Regen — {name}")
        self.statusBar().showMessage(f"Loaded: {name}  ({n_cols}×{n_rows})")
        self._save_action.setEnabled(True)

        pil_img = render_map(dungeon, gm_mode=True)
        pixmap = pil_to_qpixmap(pil_img)
        self._map_view.set_map(pixmap, self._cell_size)

        self._set_details(
            "Click a room or corridor",
            "Click any cell on the map for details.",
        )

    # ------------------------------------------------------------------
    # Click handling
    # ------------------------------------------------------------------

    def _on_cell_clicked(self, row: int, col: int):
        if self._dungeon is None:
            return
        n_rows = self._dungeon["settings"]["n_rows"]
        n_cols = self._dungeon["settings"]["n_cols"]
        if not (0 <= row < n_rows and 0 <= col < n_cols):
            return

        cell = self._dungeon["cells"][row + self._row_off][col + self._col_off]
        self._show_cell_details(row, col, cell)
        self._map_view.highlight_cell(row, col)

    # ------------------------------------------------------------------
    # Details formatting
    # ------------------------------------------------------------------

    def _show_cell_details(self, row: int, col: int, cell: int):
        if C.is_room(cell):
            rid = C.room_id(cell)
            room = self._find_room(rid)
            title = f"Room {rid}"
            body = self._format_room(room) if room else f"(no metadata for room {rid})"
        elif C.is_corridor(cell):
            title = f"Corridor  ({row}, {col})"
            body = self._format_corridor(row, col, cell)
        elif C.has_door(cell):
            title = f"Door  ({row}, {col})"
            body = f"Type: {C.door_type(cell)}"
        else:
            title = "Wandering Monsters"
            body = self._format_wandering_monsters()

        self._set_details(title, body)

    def _format_wandering_monsters(self) -> str:
        wm = self._dungeon.get("wandering_monsters") or {}
        if not wm:
            return "(no wandering-monster table for this dungeon)"
        lines = ["Roll d6:", ""]
        for key in sorted(wm.keys(), key=lambda k: int(k) if str(k).isdigit() else k):
            lines.append(f"  {key}. {wm[key]}")
        return "\n".join(lines)

    def _find_room(self, rid: int):
        for room in self._dungeon.get("rooms") or []:
            if room is None:
                continue
            try:
                if int(room["id"]) == rid:
                    return room
            except (KeyError, ValueError, TypeError):
                continue
        return None

    def _format_room(self, room: dict) -> str:
        lines = []
        shape = room.get("shape", "?")
        size = room.get("size") or ""
        size_part = f", {size}" if size else ""
        lines.append(f"Shape: {shape}{size_part}")
        lines.append(
            f"Bounds: rows {room['north']}–{room['south']}, "
            f"cols {room['west']}–{room['east']}"
        )

        contents = room.get("contents") or {}
        summary = contents.get("summary")
        if summary:
            lines.append("")
            lines.append(f"Summary: {summary}")

        detail = contents.get("detail") or {}
        for key, value in detail.items():
            lines.append("")
            lines.append(f"— {key.replace('_', ' ').title()} —")
            if isinstance(value, list):
                for item in value:
                    lines.append(f"  • {item}")
            else:
                lines.append(f"  {value}")

        doors = room.get("doors") or {}
        if doors:
            lines.append("")
            lines.append("— Doors —")
            for direction, door_list in doors.items():
                for door in door_list:
                    desc = door.get("desc", door.get("type", "door"))
                    lines.append(f"  {direction}: {desc}")

        return "\n".join(lines)

    def _format_corridor(self, row: int, col: int, cell: int) -> str:
        ch = C.label_char(cell)
        feat_map = self._dungeon.get("corridor_features") or {}

        if ch and ch in feat_map:
            feat = feat_map[ch]
            summary = feat.get("summary") or feat.get("detail") or ""
            return f"Feature ({ch!r}): {summary}"

        for key, feat in feat_map.items():
            for mark in feat.get("marks", []):
                if mark.get("row") == row and mark.get("col") == col:
                    summary = feat.get("summary") or feat.get("detail") or ""
                    return f"Feature ({key!r}): {summary}"

        return "Plain corridor."

    def _set_details(self, title: str, body: str):
        self._details_title.setText(title)
        self._details_text.setPlainText(body)

    # ------------------------------------------------------------------
    # Save outputs
    # ------------------------------------------------------------------

    def _save_outputs(self):
        if self._dungeon is None or self._json_path is None:
            return
        initial = str(self._json_path.parent / "renders")
        out_dir = QFileDialog.getExistingDirectory(
            self, "Choose output directory", initial
        )
        if not out_dir:
            return

        self._save_action.setEnabled(False)
        self._save_action.setText("Saving…")

        worker = Save_Worker(self._dungeon, out_dir, self)
        worker.finished_ok.connect(self._on_save_finished)
        worker.failed.connect(self._on_save_failed)
        worker.finished.connect(self._on_save_done)
        self._save_worker = worker
        worker.start()

    def _on_save_finished(self, count: int, out_dir: str):
        QMessageBox.information(
            self, "Saved", f"Generated {count} files in:\n{out_dir}"
        )

    def _on_save_failed(self, message: str):
        QMessageBox.critical(self, "Error", f"Save failed:\n{message}")

    def _on_save_done(self):
        self._save_action.setText("Save Outputs…")
        self._save_action.setEnabled(True)
        self._save_worker = None


def main():
    app = QApplication(sys.argv)
    window = Donjon_Viewer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
