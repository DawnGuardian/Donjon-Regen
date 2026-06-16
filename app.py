"""Viewer + editor PySide6 GUI for donjon-regen.

Loads a dungeon JSON, renders the GM map in a QGraphicsView, and shows
details for the cell under the cursor when clicked.

Click resolution:
  - Room cell  → structured editor: summary, inhabited, detail item lists,
                 doors (type + description). Apply writes changes back to
                 the in-memory dungeon dict.  Changing a door's type also
                 updates the cell bitmask and triggers a map re-render.
  - Corridor   → corridor feature text (or "Plain corridor")
  - Door       → door type
  - Wall/empty → wandering monsters d6 table

Map controls:
  - Click           : show / edit details for that cell
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
    QActionGroup,
    QBrush,
    QColor,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import cells as C
import dungeon_ops
from generate import generate_dungeon
from renderer import render_map, RENDER_SCALE


# Tool modes for Map_View. Modes change how mouse events are interpreted:
#   select   → click to view/edit cell details (existing viewer behavior)
#   corridor → click/drag paints CORRIDOR cells
#   room     → click/drag draws a rectangle; release emits the bounds
#   eraser   → click/drag clears open-space bits / removes content
TOOL_SELECT = "select"
TOOL_CORRIDOR = "corridor"
TOOL_ROOM = "room"
TOOL_ERASER = "eraser"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pil_to_qpixmap(pil_image: Image.Image) -> QPixmap:
    """Convert a PIL Image to a QPixmap via PNG round-trip (lossless, simple)."""
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    qimg = QImage.fromData(buf.getvalue(), "PNG")
    return QPixmap.fromImage(qimg)


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    label.setContentsMargins(0, 6, 0, 2)
    return label


def _hr() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


# ---------------------------------------------------------------------------
# Map view widget — zoom/pan QGraphicsView
# ---------------------------------------------------------------------------

class Map_View(QGraphicsView):
    """QGraphicsView with mouse-wheel zoom, middle-button pan, and tool-aware
    left-button interaction.

    Signals depend on the active tool:
      * `select`              → `cell_clicked(row, col)` on each click.
      * `corridor` / `eraser` → `cell_painted(row, col, tool)` per cell
        entered while the left button is held, then `paint_stroke_ended(tool)`
        on release (the host re-renders once per stroke, not per cell).
      * `room`                → live rectangle preview during drag; on release
        emit `rect_drawn(north, south, west, east)` with the bbox in map
        coordinates.

    Cursor shape is updated by `set_tool` to reflect what the next click does.
    """

    cell_clicked = Signal(int, int)
    cell_painted = Signal(int, int, str)
    paint_stroke_ended = Signal(str)
    rect_drawn = Signal(int, int, int, int)

    _TOOL_CURSORS = {
        TOOL_SELECT: Qt.ArrowCursor,
        TOOL_CORRIDOR: Qt.CrossCursor,
        TOOL_ROOM: Qt.CrossCursor,
        TOOL_ERASER: Qt.PointingHandCursor,
    }

    _PREVIEW_PEN_COLOR = QColor(0, 160, 255)
    _PREVIEW_FILL_COLOR = QColor(0, 160, 255, 60)

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

        self._tool = TOOL_SELECT
        self._drag_active = False
        self._drag_start_cell: tuple[int, int] | None = None
        self._last_painted_cell: tuple[int, int] | None = None
        self._preview_rect: QGraphicsRectItem | None = None

    def set_map(self, pixmap: QPixmap, cell_size: int) -> None:
        self._scene.clear()
        self._highlight = None
        self._preview_rect = None
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._cell_size = cell_size
        self.fit_to_window()

    def update_pixmap(self, pixmap: QPixmap, cell_size: int | None = None) -> None:
        """Replace the pixmap without resetting zoom/pan or losing the
        highlight rectangle. Pass `cell_size` to update the click-mapping
        scale (required on first load — `_cell_size` defaults to 1)."""
        if cell_size is not None:
            self._cell_size = cell_size
        if self._pixmap_item is None:
            self.set_map(pixmap, self._cell_size)
            return
        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))

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

    # -- tool control ------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        if tool not in self._TOOL_CURSORS:
            tool = TOOL_SELECT
        self._tool = tool
        self._cancel_drag_state()
        self.viewport().setCursor(self._TOOL_CURSORS[tool])

    def current_tool(self) -> str:
        return self._tool

    def _cancel_drag_state(self) -> None:
        if self._preview_rect is not None:
            self._scene.removeItem(self._preview_rect)
            self._preview_rect = None
        self._drag_active = False
        self._drag_start_cell = None
        self._last_painted_cell = None

    def _cell_from_event(self, event) -> tuple[int, int]:
        scene_pos: QPointF = self.mapToScene(event.position().toPoint())
        col = int(scene_pos.x() // self._cell_size)
        row = int(scene_pos.y() // self._cell_size)
        return row, col

    def _bbox_rect(self, r0: int, c0: int, r1: int, c1: int) -> QRectF:
        cs = self._cell_size
        n, s = min(r0, r1), max(r0, r1)
        w, e = min(c0, c1), max(c0, c1)
        return QRectF(w * cs, n * cs, (e - w + 1) * cs, (s - n + 1) * cs)

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
            event.button() != Qt.LeftButton
            or self._pixmap_item is None
            or self._cell_size <= 0
        ):
            super().mousePressEvent(event)
            return

        row, col = self._cell_from_event(event)

        if self._tool == TOOL_SELECT:
            self.cell_clicked.emit(row, col)
            return

        if self._tool in (TOOL_CORRIDOR, TOOL_ERASER):
            self._drag_active = True
            self._last_painted_cell = (row, col)
            self.cell_painted.emit(row, col, self._tool)
            return

        if self._tool == TOOL_ROOM:
            self._drag_active = True
            self._drag_start_cell = (row, col)
            pen = QPen(self._PREVIEW_PEN_COLOR)
            pen.setWidth(2)
            pen.setCosmetic(True)
            brush = QBrush(self._PREVIEW_FILL_COLOR)
            self._preview_rect = self._scene.addRect(
                self._bbox_rect(row, col, row, col), pen, brush
            )
            self._preview_rect.setZValue(15)
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._drag_active or self._pixmap_item is None:
            super().mouseMoveEvent(event)
            return

        row, col = self._cell_from_event(event)

        if self._tool in (TOOL_CORRIDOR, TOOL_ERASER):
            if (row, col) == self._last_painted_cell:
                return
            self._last_painted_cell = (row, col)
            self.cell_painted.emit(row, col, self._tool)
            return

        if self._tool == TOOL_ROOM and self._preview_rect is not None:
            r0, c0 = self._drag_start_cell
            self._preview_rect.setRect(self._bbox_rect(r0, c0, row, col))
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self._drag_active:
            super().mouseReleaseEvent(event)
            return

        if self._tool in (TOOL_CORRIDOR, TOOL_ERASER):
            tool = self._tool
            self._drag_active = False
            self._last_painted_cell = None
            self.paint_stroke_ended.emit(tool)
            return

        if self._tool == TOOL_ROOM and self._drag_start_cell is not None:
            row, col = self._cell_from_event(event)
            r0, c0 = self._drag_start_cell
            n, s = min(r0, row), max(r0, row)
            w, e = min(c0, col), max(c0, col)
            self._cancel_drag_state()
            self.rect_drawn.emit(n, s, w, e)
            return

        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# Background save thread
# ---------------------------------------------------------------------------

class Save_Worker(QThread):
    finished_ok = Signal(int, str)
    failed = Signal(str)

    def __init__(self, dungeon: dict, out_dir: str, render_scale: int, parent=None):
        super().__init__(parent)
        self._dungeon = dungeon
        self._out_dir = out_dir
        self._render_scale = render_scale

    def run(self):
        try:
            generated = generate_dungeon(
                self._dungeon, self._out_dir, render_scale=self._render_scale
            )
            self.finished_ok.emit(len(generated), self._out_dir)
        except Exception as e:
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# Item-list editor — used for each detail category (monster, treasure, …)
# ---------------------------------------------------------------------------

ITEM_SEPARATOR = "--"


# Sections the user can add to a room from the editor when they aren't already
# present in the source data. Order = display order.
_ADDABLE_SECTIONS: list[tuple[str, str]] = [
    ("monster", "+ Add Monster Section"),
    ("hidden_treasure", "+ Add Hidden Treasure Section"),
]

# Per-section behavior for the per-row "+ Add …" button inside Item_List_Editor.
# (template, position). Defaults to ([""], "before_sep") for unlisted keys.
_SECTION_ADD_RULES: dict[str, tuple[list[str], str]] = {
    # Hidden treasure is a container/trap block separated from its loot by
    # '--', so each Add inserts a fresh container + separator + loot triplet
    # at the end of the list.
    "hidden_treasure": (["", ITEM_SEPARATOR, ""], "end"),
    # Monsters: inserts a single blank row in the monster sub-section
    # (above the '--' that separates monsters from their treasure).
    "monster": ([""], "before_sep"),
}

# Fresh-section initial contents when added via the "Add Section" button.
_SECTION_INITIAL_ITEMS: dict[str, list[str]] = {
    "monster": [""],
    "hidden_treasure": ["", ITEM_SEPARATOR, ""],
}


class Item_List_Editor(QWidget):
    """Vertical list of editable rows with per-row delete and an "Add <label>"
    button at the bottom. `'--'` items in the source data are shown as a
    non-editable horizontal divider but preserved at their original positions
    when `values()` is called.

    Emits `items_changed` whenever rows are added, removed, or their contents
    change — used by the room editor to keep the Inhabited line in sync.
    """

    items_changed = Signal()

    def __init__(
        self,
        items: list[str],
        label: str,
        add_template: list[str] | None = None,
        add_position: str = "before_sep",
        parent=None,
    ):
        """`add_template` is the sequence of items inserted on each click of the
        Add button (defaults to `[""]` — a single blank row). `add_position` is
        either `"before_sep"` (insert before the first `'--'` separator, so e.g.
        adding a monster lands in the monster section) or `"end"` (append at
        the bottom — used for hidden-treasure pairings)."""
        super().__init__(parent)
        # _rows holds (kind, widget, editor_or_None)
        # kind is "item" or "sep"; for "sep" the editor is None.
        self._rows: list[tuple[str, QWidget, QPlainTextEdit | None]] = []
        self._label = label
        self._add_template = add_template if add_template is not None else [""]
        self._add_position = add_position

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._layout.addWidget(self._rows_container)

        for item in items:
            if item == ITEM_SEPARATOR:
                self._add_separator()
            else:
                self._add_row(item)

        add_btn = QPushButton(f"+ Add {label.lower()}")
        add_btn.clicked.connect(self._on_add_clicked)
        self._layout.addWidget(add_btn, 0, Qt.AlignLeft)

    # -- row construction --------------------------------------------------

    def _add_row(self, text: str) -> None:
        # Used at construction time to append rows for the source items.
        self._insert_row(len(self._rows), text)

    def _add_separator(self) -> None:
        # Used at construction time to append separator rows.
        self._insert_separator(len(self._rows))

    def _insert_separator(self, index: int) -> None:
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: #888;")
        self._rows_layout.insertWidget(index, sep)
        self._rows.insert(index, ("sep", sep, None))

    def _on_add_clicked(self) -> None:
        if self._add_position == "end":
            insert_index = len(self._rows)
        else:  # "before_sep" — fall back to end if no separator exists yet
            insert_index = next(
                (i for i, (kind, _, _) in enumerate(self._rows) if kind == "sep"),
                len(self._rows),
            )
        for offset, text in enumerate(self._add_template):
            if text == ITEM_SEPARATOR:
                self._insert_separator(insert_index + offset)
            else:
                self._insert_row(insert_index + offset, text)
        self.items_changed.emit()

    def _insert_row(self, index: int, text: str) -> None:
        """Insert a new editable row at the given position (in both the layout
        and `self._rows`)."""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        editor = QPlainTextEdit(text)
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        editor.setMinimumHeight(48)
        editor.setMaximumHeight(120)
        editor.textChanged.connect(self.items_changed.emit)
        row_layout.addWidget(editor, 1)

        del_btn = QPushButton("✕")
        del_btn.setFixedWidth(28)
        del_btn.setToolTip("Delete this item")
        del_btn.clicked.connect(lambda: self._remove_row(row))
        row_layout.addWidget(del_btn, 0)

        self._rows_layout.insertWidget(index, row)
        self._rows.insert(index, ("item", row, editor))

    def _remove_row(self, row_widget: QWidget) -> None:
        for i, (_, w, _ed) in enumerate(self._rows):
            if w is row_widget:
                self._rows.pop(i)
                break
        row_widget.setParent(None)
        row_widget.deleteLater()
        self.items_changed.emit()

    # -- collect -----------------------------------------------------------

    def values(self) -> list[str]:
        out: list[str] = []
        for kind, _w, editor in self._rows:
            if kind == "sep":
                out.append(ITEM_SEPARATOR)
            else:
                out.append(editor.toPlainText())
        return out

    def items_only(self) -> list[str]:
        """Editable items, separators excluded."""
        return [
            editor.toPlainText()
            for kind, _w, editor in self._rows
            if kind == "item"
        ]


# ---------------------------------------------------------------------------
# Door editor row
# ---------------------------------------------------------------------------

class Door_Row(QWidget):
    """One row in the doors section: direction label, type combo, desc edit.
    A type-dependent extra field is shown beneath: a "trap" line for trapped
    doors, a "hint" line for secret doors, nothing otherwise. The extra row
    rebuilds when the type combo changes."""

    # Mapping of door type -> (extra-field label, key in door dict)
    _EXTRA_FIELDS = {
        "trapped": ("trap", "trap"),
        "secret": ("hint", "secret"),
    }

    def __init__(self, direction: str, door: dict, parent=None):
        super().__init__(parent)
        self._direction = direction
        self._door = door
        self._original_type = door.get("type", "door")
        # Cache values across type switches so flipping back restores them.
        self._extra_cache: dict[str, str] = {
            "trap": door.get("trap", ""),
            "secret": door.get("secret", ""),
        }
        self._extra_edit: QLineEdit | None = None

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 4, 0, 4)
        self._outer.setSpacing(2)

        first_row = QHBoxLayout()
        first_row.setSpacing(6)

        dir_label = QLabel(direction)
        dir_label.setFixedWidth(60)
        first_row.addWidget(dir_label)

        self._type_combo = QComboBox()
        self._type_combo.addItems(C.DOOR_TYPE_NAMES)
        if self._original_type in C.DOOR_TYPE_NAMES:
            self._type_combo.setCurrentText(self._original_type)
        self._type_combo.setFixedWidth(110)
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        first_row.addWidget(self._type_combo)

        self._desc_edit = QLineEdit(door.get("desc", ""))
        first_row.addWidget(self._desc_edit, 1)

        self._outer.addLayout(first_row)

        # Container for the type-dependent extra row so we can rebuild it
        # cheaply when the type changes.
        self._extra_container = QWidget()
        self._extra_layout = QHBoxLayout(self._extra_container)
        self._extra_layout.setContentsMargins(0, 0, 0, 0)
        self._extra_layout.setSpacing(6)
        self._outer.addWidget(self._extra_container)
        self._build_extra_field(self._original_type)

    def _build_extra_field(self, door_type: str) -> None:
        """Render the extra field for `door_type`. Caller is responsible for
        caching the previous field's value before invoking this."""
        # Clear container
        while self._extra_layout.count():
            item = self._extra_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._extra_edit = None

        if door_type not in self._EXTRA_FIELDS:
            self._extra_container.setVisible(False)
            return

        label_text, key = self._EXTRA_FIELDS[door_type]
        lbl = QLabel(label_text)
        lbl.setFixedWidth(60)
        self._extra_layout.addWidget(lbl)
        self._extra_edit = QLineEdit(self._extra_cache.get(key, ""))
        self._extra_layout.addWidget(self._extra_edit, 1)
        self._extra_container.setVisible(True)

    def _on_type_changed(self, new_type: str) -> None:
        # Capture the current extra-field value into its cache before rebuild.
        if self._extra_edit is not None:
            previous_type = getattr(self, "_previous_type", self._original_type)
            if previous_type in self._EXTRA_FIELDS:
                _, key = self._EXTRA_FIELDS[previous_type]
                self._extra_cache[key] = self._extra_edit.text()
        self._previous_type = new_type
        self._build_extra_field(new_type)

    def apply(self) -> bool:
        """Write edits back to the door dict. Returns True if type changed.
        Drops trap/secret keys that don't apply to the new type."""
        new_type = self._type_combo.currentText()
        type_changed = new_type != self._original_type
        self._door["type"] = new_type
        self._door["desc"] = self._desc_edit.text()

        if new_type in self._EXTRA_FIELDS and self._extra_edit is not None:
            _, key = self._EXTRA_FIELDS[new_type]
            self._door[key] = self._extra_edit.text()

        # Drop extra-field keys that don't match the current type
        for t, (_, key) in self._EXTRA_FIELDS.items():
            if t != new_type:
                self._door.pop(key, None)

        return type_changed

    @property
    def door(self) -> dict:
        return self._door

    @property
    def original_type(self) -> str:
        return self._original_type

    @property
    def new_type(self) -> str:
        return self._type_combo.currentText()


# ---------------------------------------------------------------------------
# Room editor — full structured form
# ---------------------------------------------------------------------------

class Room_Editor(QWidget):
    """Structured editor for a single room.

    `apply_clicked(room, changed_doors, geometry_changed)` is emitted when
    the user clicks Apply — `changed_doors` is the list of door dicts whose
    type changed (so the main window can update their cell bits), and
    `geometry_changed` is True if `reshape_room` or `resize_room` actually
    modified anything on this apply (so the main window can re-render and,
    in the resize case, refresh its row/col offsets).

    `delete_clicked(room)` fires when the user clicks the Delete Room button
    — the main window confirms and dispatches to `dungeon_ops.delete_room`.
    """

    apply_clicked = Signal(dict, list, bool)
    delete_clicked = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._room: dict | None = None
        self._dungeon: dict | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Title
        self._title = QLabel("")
        title_font = self._title.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setContentsMargins(8, 8, 8, 4)
        outer.addWidget(self._title)

        # Scrollable form
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        outer.addWidget(self._scroll, 1)

        # Bottom button row
        button_row = QHBoxLayout()
        button_row.setContentsMargins(8, 4, 8, 8)
        self._delete_btn = QPushButton("Delete Room")
        self._delete_btn.setStyleSheet("color: #a40000;")
        self._delete_btn.clicked.connect(self._on_delete)
        button_row.addWidget(self._delete_btn)
        button_row.addStretch(1)
        self._apply_btn = QPushButton("Apply Changes")
        self._apply_btn.clicked.connect(self._on_apply)
        button_row.addWidget(self._apply_btn)
        outer.addLayout(button_row)

        # Cached editors (rebuilt per room)
        self._summary_edit: QLineEdit | None = None
        self._inhabited_edit: QLineEdit | None = None
        self._detail_editors: dict[str, Item_List_Editor | QPlainTextEdit] = {}
        self._door_rows: list[Door_Row] = []
        # Geometry section widgets (rebuilt per room).
        self._shape_combo: QComboBox | None = None
        self._poly_n_spin: QSpinBox | None = None
        self._poly_n_label: QLabel | None = None
        self._dn_spin: QSpinBox | None = None
        self._ds_spin: QSpinBox | None = None
        self._dw_spin: QSpinBox | None = None
        self._de_spin: QSpinBox | None = None

    # -- public ------------------------------------------------------------

    def set_room(self, room: dict, dungeon: dict | None = None) -> None:
        self._room = room
        if dungeon is not None:
            self._dungeon = dungeon
        self._build_form()

    # -- form construction -------------------------------------------------

    def _build_form(self) -> None:
        self._detail_editors = {}
        self._door_rows = []
        self._inhabited_edit = None

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)

        room = self._room
        rid = room.get("id", "?")
        shape = room.get("shape", "?")
        size = room.get("size") or ""
        size_part = f", {size}" if size else ""
        self._title.setText(f"Room {rid}")

        info = QLabel(
            f"Shape: {shape}{size_part}    "
            f"Bounds: rows {room['north']}–{room['south']}, "
            f"cols {room['west']}–{room['east']}"
        )
        info.setStyleSheet("color: #666;")
        layout.addWidget(info)
        layout.addWidget(_hr())

        # Geometry section — shape conversion and perimeter deltas. Applied
        # via `dungeon_ops.reshape_room` / `resize_room` when the user clicks
        # Apply Changes. Polygon / circle options are always present in the
        # combo so the user can target polymorph as the goal; if the *current*
        # bbox isn't square the op will reject the change with a clear error
        # (driving them to first equalise the bbox via the perimeter deltas).
        layout.addWidget(_section_label("Geometry"))

        shape_row = QHBoxLayout()
        shape_row.addWidget(QLabel("Shape:"))
        self._shape_combo = QComboBox()
        # currentData is the value passed to dungeon_ops.reshape_room.
        # "rectangle" is the user-facing label for donjon's "square" shape.
        self._shape_combo.addItem("Rectangle", "rectangle")
        self._shape_combo.addItem("Polygon", "polygon")
        self._shape_combo.addItem("Circle", "circle")
        current_stored = room.get("shape", "square")
        ui_shape = (
            "polygon" if current_stored == "polygon"
            else "circle" if current_stored == "circle"
            else "rectangle"
        )
        idx = self._shape_combo.findData(ui_shape)
        if idx >= 0:
            self._shape_combo.setCurrentIndex(idx)
        self._shape_combo.currentIndexChanged.connect(
            self._on_shape_combo_changed
        )
        shape_row.addWidget(self._shape_combo)

        self._poly_n_label = QLabel("Sides N:")
        self._poly_n_spin = QSpinBox()
        self._poly_n_spin.setRange(3, 12)
        self._poly_n_spin.setValue(int(room.get("polygon") or 6))
        shape_row.addWidget(self._poly_n_label)
        shape_row.addWidget(self._poly_n_spin)
        shape_row.addStretch(1)
        layout.addLayout(shape_row)
        self._on_shape_combo_changed()  # set N visibility

        # Perimeter Δ — signed spinboxes per edge. Pulls outward when positive,
        # inward when negative. Donjon's shape="square" rooms tolerate any
        # bbox; polymorph rooms must remain square (op rejects otherwise).
        deltas_label = QLabel(
            "Perimeter Δ cells (+ grows outward, − shrinks):"
        )
        deltas_label.setStyleSheet("color: #555;")
        layout.addWidget(deltas_label)

        deltas_row = QHBoxLayout()
        def _delta_spin() -> QSpinBox:
            s = QSpinBox()
            s.setRange(-64, 64)
            s.setValue(0)
            s.setFixedWidth(64)
            return s
        self._dn_spin = _delta_spin()
        self._ds_spin = _delta_spin()
        self._dw_spin = _delta_spin()
        self._de_spin = _delta_spin()
        for label, spin in (
            ("N", self._dn_spin), ("S", self._ds_spin),
            ("W", self._dw_spin), ("E", self._de_spin),
        ):
            deltas_row.addWidget(QLabel(label))
            deltas_row.addWidget(spin)
            deltas_row.addSpacing(8)
        deltas_row.addStretch(1)
        layout.addLayout(deltas_row)
        layout.addWidget(_hr())

        contents = room.setdefault("contents", {})

        # Summary
        layout.addWidget(_section_label("Summary"))
        self._summary_edit = QLineEdit(contents.get("summary") or "")
        layout.addWidget(self._summary_edit)

        # Inhabited — derived from the monster list, displayed read-only.
        # Hidden when there are no monster entries.
        self._inhabited_label_widget = _section_label("Inhabited")
        layout.addWidget(self._inhabited_label_widget)
        self._inhabited_edit = QLineEdit(contents.get("inhabited") or "")
        self._inhabited_edit.setReadOnly(True)
        self._inhabited_edit.setStyleSheet("color: #444; background: #f3f3f3;")
        layout.addWidget(self._inhabited_edit)

        # Detail categories
        detail = contents.setdefault("detail", {})
        if detail:
            layout.addWidget(_hr())
            for key, value in detail.items():
                self._add_detail_widget(layout, key, value)

        # "Add section" buttons for sections that don't currently exist
        addable_missing = [
            (k, label) for k, label in _ADDABLE_SECTIONS
            if k not in detail
        ]
        if addable_missing:
            layout.addWidget(_hr())
            row = QHBoxLayout()
            row.setSpacing(8)
            for key, label in addable_missing:
                btn = QPushButton(label)
                btn.clicked.connect(lambda _checked=False, k=key: self._add_section(k))
                row.addWidget(btn)
            row.addStretch(1)
            layout.addLayout(row)

        # Doors
        doors = room.get("doors") or {}
        if doors:
            layout.addWidget(_hr())
            layout.addWidget(_section_label("Doors"))
            for direction, door_list in doors.items():
                for door in door_list:
                    row = Door_Row(direction, door)
                    self._door_rows.append(row)
                    layout.addWidget(row)

        layout.addStretch(1)
        self._scroll.setWidget(body)

        self._refresh_inhabited()

    def _add_detail_widget(self, layout: QVBoxLayout, key: str, value) -> None:
        """Add the section header and editor for a single detail key."""
        section_title = key.replace("_", " ").title()
        layout.addWidget(_section_label(section_title))
        if isinstance(value, list):
            template, position = _SECTION_ADD_RULES.get(key, ([""], "before_sep"))
            editor = Item_List_Editor(
                [str(x) for x in value],
                section_title,
                add_template=template,
                add_position=position,
            )
            self._detail_editors[key] = editor
            layout.addWidget(editor)
            if key == "monster":
                editor.items_changed.connect(self._refresh_inhabited)
        else:
            editor = QPlainTextEdit(str(value))
            editor.setMinimumHeight(60)
            editor.setMaximumHeight(160)
            self._detail_editors[key] = editor
            layout.addWidget(editor)

    def _add_section(self, key: str) -> None:
        """Create a detail section that wasn't in the source data and rebuild
        the form. Preserves any edits the user has already made by collecting
        them into the dict first."""
        if self._room is None:
            return
        # Save current widget state before tearing down and rebuilding.
        self._collect_edits()
        contents = self._room.setdefault("contents", {})
        detail = contents.setdefault("detail", {})
        if key in detail:
            return
        detail[key] = list(_SECTION_INITIAL_ITEMS.get(key, [""]))
        self._build_form()

    # -- inhabited helpers -------------------------------------------------

    def _refresh_inhabited(self) -> None:
        """Recompute the Inhabited line from the current monster items.
        Called on form build and whenever the monster Item_List_Editor changes."""
        derived = self._derive_inhabited()
        if derived:
            self._inhabited_edit.setText(derived)
            self._inhabited_label_widget.setVisible(True)
            self._inhabited_edit.setVisible(True)
        else:
            self._inhabited_edit.setText("")
            self._inhabited_label_widget.setVisible(False)
            self._inhabited_edit.setVisible(False)

    def _derive_inhabited(self) -> str:
        """`<count> x <Monster Name>` joined with commas, taking only items
        before the first '--' separator and stripping the ` (cr …)` suffix
        and anything after it."""
        editor = self._detail_editors.get("monster")
        if not isinstance(editor, Item_List_Editor):
            return ""
        names = []
        for raw in editor.values():
            if raw == ITEM_SEPARATOR:
                break  # everything past the first '--' is treasure / loot
            text = raw.strip()
            if not text:
                continue
            if text.lower().startswith("treasure"):
                continue
            paren = text.find(" (")
            name = text[:paren] if paren != -1 else text
            names.append(name.strip())
        return ", ".join(names)

    # -- apply -------------------------------------------------------------

    def _collect_edits(self) -> list:
        """Write current widget state back into the room dict and return the
        list of doors whose type changed. Does NOT emit `apply_clicked`."""
        if self._room is None:
            return []
        contents = self._room.setdefault("contents", {})

        if self._summary_edit is not None:
            contents["summary"] = self._summary_edit.text()

        detail = contents.setdefault("detail", {})
        for key, editor in self._detail_editors.items():
            if isinstance(editor, Item_List_Editor):
                detail[key] = editor.values()
            else:
                detail[key] = editor.toPlainText()

        # Inhabited is derived; write it (or remove it) based on the monster list.
        derived = self._derive_inhabited()
        if derived:
            contents["inhabited"] = derived
        else:
            contents.pop("inhabited", None)

        changed_doors = []
        for row in self._door_rows:
            if row.apply():
                changed_doors.append(row.door)
        return changed_doors

    def _on_shape_combo_changed(self) -> None:
        """Polygon-N spinbox is only meaningful when the chosen shape is
        polygon."""
        if self._shape_combo is None:
            return
        is_polygon = self._shape_combo.currentData() == "polygon"
        if self._poly_n_label is not None:
            self._poly_n_label.setVisible(is_polygon)
        if self._poly_n_spin is not None:
            self._poly_n_spin.setVisible(is_polygon)

    def _apply_geometry(self) -> bool:
        """Apply shape + perimeter-delta changes via dungeon_ops. Shows a
        QMessageBox on validation failure (overlap / off-canvas / non-square
        polymorph) and leaves the room dict untouched in that case. Returns
        True if anything actually changed (so the caller knows to re-render).
        """
        if self._room is None or self._dungeon is None:
            return False
        if self._shape_combo is None:
            return False

        changed = False

        # Resize first — reshape's square-bbox validation should run against
        # the post-resize bbox, not the pre-resize one (otherwise the user
        # can't grow a non-square room and turn it into a circle in a single
        # Apply).
        dn = self._dn_spin.value() if self._dn_spin else 0
        ds = self._ds_spin.value() if self._ds_spin else 0
        dw = self._dw_spin.value() if self._dw_spin else 0
        de = self._de_spin.value() if self._de_spin else 0
        if (dn, ds, de, dw) != (0, 0, 0, 0):
            try:
                if dungeon_ops.resize_room(
                    self._dungeon, self._room, dn, ds, de, dw
                ):
                    changed = True
            except ValueError as e:
                QMessageBox.warning(self, "Cannot resize room", str(e))
                return changed

        target_shape = self._shape_combo.currentData() or "rectangle"
        polygon_n = self._poly_n_spin.value() if self._poly_n_spin else 0
        try:
            if dungeon_ops.reshape_room(
                self._room, target_shape, polygon_n=polygon_n
            ):
                changed = True
        except ValueError as e:
            QMessageBox.warning(self, "Cannot change shape", str(e))
            return changed

        return changed

    def _on_apply(self) -> None:
        if self._room is None:
            return
        changed_doors = self._collect_edits()
        geometry_changed = self._apply_geometry()
        self.apply_clicked.emit(self._room, changed_doors, geometry_changed)

    def _on_delete(self) -> None:
        if self._room is None:
            return
        self.delete_clicked.emit(self._room)


# ---------------------------------------------------------------------------
# Mirror dialog (Phase 5)
# ---------------------------------------------------------------------------

class Mirror_Dialog(QDialog):
    """Mirror rooms across an axis. Overwrite mode (destination-side rooms
    overlapping the mirrored region are deleted first). Corridors and stairs
    are NOT mirrored.

    The user picks: axis (horizontal/vertical), source side (which side of
    the pivot to copy FROM), and pivot index. Side options reset whenever
    the axis changes — vertical axis takes west/east, horizontal takes
    north/south."""

    def __init__(self, parent, n_rows: int, n_cols: int):
        super().__init__(parent)
        self.setWindowTitle("Mirror Rooms")
        self._n_rows = n_rows
        self._n_cols = n_cols

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Overwrite mode: rooms on the destination side that overlap any\n"
            "mirrored bbox are deleted first. Corridors and stairs are NOT\n"
            "mirrored. Rooms straddling the pivot line are skipped."
        )
        intro.setStyleSheet("color: #555;")
        layout.addWidget(intro)

        form = QFormLayout()

        self._axis_combo = QComboBox()
        self._axis_combo.addItem("Horizontal (mirror across rows)", "horizontal")
        self._axis_combo.addItem("Vertical (mirror across cols)", "vertical")
        self._axis_combo.currentIndexChanged.connect(self._on_axis_changed)
        form.addRow("Axis:", self._axis_combo)

        self._side_combo = QComboBox()
        form.addRow("Source side:", self._side_combo)

        self._pivot_spin = QSpinBox()
        form.addRow("Pivot index:", self._pivot_spin)

        self._pivot_hint = QLabel("")
        self._pivot_hint.setStyleSheet("color: #888;")
        form.addRow("", self._pivot_hint)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_axis_changed()

    def _on_axis_changed(self) -> None:
        axis = self._axis_combo.currentData()
        self._side_combo.clear()
        if axis == "horizontal":
            self._side_combo.addItem("North side (copy to South)", "north")
            self._side_combo.addItem("South side (copy to North)", "south")
            self._pivot_spin.setRange(0, self._n_rows - 1)
            self._pivot_spin.setValue(self._n_rows // 2)
            self._pivot_hint.setText(f"Row index — canvas has {self._n_rows} rows")
        else:
            self._side_combo.addItem("West side (copy to East)", "west")
            self._side_combo.addItem("East side (copy to West)", "east")
            self._pivot_spin.setRange(0, self._n_cols - 1)
            self._pivot_spin.setValue(self._n_cols // 2)
            self._pivot_hint.setText(f"Col index — canvas has {self._n_cols} cols")

    def result_settings(self) -> tuple[str, int, str]:
        return (
            self._axis_combo.currentData(),
            self._pivot_spin.value(),
            self._side_combo.currentData(),
        )


# ---------------------------------------------------------------------------
# New-room dialog (Phase 3)
# ---------------------------------------------------------------------------

class New_Room_Dialog(QDialog):
    """Modal shown after the user drags a room rectangle. Asks for shape +
    polygon-N. Polygon / circle options are only enabled when the bbox is
    square (donjon's polymorph renderer inscribes the shape in
    `min(width, height)`).

    `result_shape()` returns `(shape_name, polygon_n)` where `shape_name`
    is one of "rectangle" / "square" / "polygon" / "circle"."""

    def __init__(self, parent, cells_wide: int, cells_tall: int):
        super().__init__(parent)
        self.setWindowTitle("New Room")
        self._is_square = cells_wide == cells_tall

        layout = QVBoxLayout(self)

        info = QLabel(
            f"Bbox: {cells_wide} × {cells_tall} cells"
            f" ({'square' if self._is_square else 'non-square'})"
        )
        info.setStyleSheet("color: #666;")
        layout.addWidget(info)

        layout.addWidget(QLabel("Shape:"))
        self._shape_combo = QComboBox()
        # Always present "Rectangle" — donjon stores any axis-aligned rect
        # as shape="square", but the user-facing label uses the geometric
        # term so non-square bounds don't read as a contradiction.
        self._shape_combo.addItem("Rectangle", "rectangle")
        if self._is_square:
            self._shape_combo.addItem("Polygon (N-sided)", "polygon")
            self._shape_combo.addItem("Circle", "circle")
        else:
            note = QLabel(
                "(polygon / circle require a square bbox — "
                "drag a 1:1 rectangle to unlock them)"
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: #888; font-style: italic;")
            layout.addWidget(note)
        self._shape_combo.currentIndexChanged.connect(self._update_n_visibility)
        layout.addWidget(self._shape_combo)

        n_row = QHBoxLayout()
        self._n_label = QLabel("Sides (N):")
        self._n_spin = QSpinBox()
        self._n_spin.setRange(3, 12)
        self._n_spin.setValue(6)
        n_row.addWidget(self._n_label)
        n_row.addWidget(self._n_spin)
        n_row.addStretch(1)
        layout.addLayout(n_row)
        self._update_n_visibility()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_n_visibility(self) -> None:
        is_polygon = self._shape_combo.currentData() == "polygon"
        self._n_label.setVisible(is_polygon)
        self._n_spin.setVisible(is_polygon)

    def result_shape(self) -> tuple[str, int]:
        shape = self._shape_combo.currentData() or "rectangle"
        n = self._n_spin.value() if shape == "polygon" else 0
        return shape, n


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

        # Edit-tool selector — exclusive checkable group. Default = Select
        # (preserves the prior viewer behavior).
        tool_group = QActionGroup(self)
        tool_group.setExclusive(True)
        self._tool_actions: dict[str, QAction] = {}
        for tool_id, label, tooltip in (
            (TOOL_SELECT, "Select", "View / edit cell details (default)"),
            (TOOL_CORRIDOR, "Corridor", "Click or drag to paint corridor tiles"),
            (TOOL_ROOM, "Room", "Click-and-drag to draw a new room rectangle"),
            (TOOL_ERASER, "Eraser", "Click or drag to clear tiles"),
        ):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setToolTip(tooltip)
            act.setEnabled(False)
            act.triggered.connect(lambda _checked=False, t=tool_id: self._set_tool(t))
            tool_group.addAction(act)
            toolbar.addAction(act)
            self._tool_actions[tool_id] = act
        self._tool_actions[TOOL_SELECT].setChecked(True)

        toolbar.addSeparator()

        # Structural-edit actions (one-shot dialogs, not tool modes).
        self._resize_canvas_action = QAction("Resize Canvas…", self)
        self._resize_canvas_action.setEnabled(False)
        self._resize_canvas_action.setToolTip(
            "Grow or shrink the dungeon canvas by per-edge cell deltas"
        )
        self._resize_canvas_action.triggered.connect(self._on_resize_canvas)
        toolbar.addAction(self._resize_canvas_action)

        self._mirror_action = QAction("Mirror Rooms…", self)
        self._mirror_action.setEnabled(False)
        self._mirror_action.setToolTip(
            "Mirror all rooms across an axis (overwrite mode — destination rooms are replaced)"
        )
        self._mirror_action.triggered.connect(self._on_mirror_rooms)
        toolbar.addAction(self._mirror_action)

        toolbar.addSeparator()

        fit_action = QAction("Fit to Window", self)
        fit_action.setShortcut(QKeySequence("Ctrl+0"))
        fit_action.triggered.connect(lambda: self._map_view.fit_to_window())
        toolbar.addAction(fit_action)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Scale: "))
        self._scale_spin = QSpinBox()
        self._scale_spin.setRange(1, 8)
        self._scale_spin.setValue(RENDER_SCALE)
        self._scale_spin.setToolTip(
            "Render scale multiplier (applied to GM and player cell sizes)"
        )
        self._scale_spin.valueChanged.connect(self._on_scale_changed)
        toolbar.addWidget(self._scale_spin)

        # Central splitter: map | details
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        self._map_view = Map_View()
        self._map_view.cell_clicked.connect(self._on_cell_clicked)
        self._map_view.cell_painted.connect(self._on_cell_painted)
        self._map_view.paint_stroke_ended.connect(self._on_paint_stroke_ended)
        self._map_view.rect_drawn.connect(self._on_rect_drawn)
        splitter.addWidget(self._map_view)

        # Right-side details: stacked simple-view / room-editor
        self._details_stack = QStackedWidget()

        # Page 0: simple read-only view (corridor / door / wall / wandering)
        simple_page = QWidget()
        simple_layout = QVBoxLayout(simple_page)
        simple_layout.setContentsMargins(8, 8, 8, 8)
        self._simple_title = QLabel("Click a room or corridor")
        title_font = self._simple_title.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self._simple_title.setFont(title_font)
        self._simple_title.setWordWrap(True)
        simple_layout.addWidget(self._simple_title)
        self._simple_text = QTextEdit()
        self._simple_text.setReadOnly(True)
        simple_layout.addWidget(self._simple_text, 1)
        self._details_stack.addWidget(simple_page)

        # Page 1: structured room editor
        self._room_editor = Room_Editor()
        self._room_editor.apply_clicked.connect(self._on_room_applied)
        self._room_editor.delete_clicked.connect(self._on_room_delete)
        self._details_stack.addWidget(self._room_editor)

        splitter.addWidget(self._details_stack)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([800, 480])

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
        # Render at the toolbar's scale × the JSON's authoring cell_size;
        # the same scaled value drives click-to-cell mapping inside Map_View.
        self._cell_size = dungeon["settings"]["cell_size"] * self._scale_spin.value()

        cell_data = dungeon["cells"]
        n_rows = dungeon["settings"]["n_rows"]
        n_cols = dungeon["settings"]["n_cols"]
        self._row_off = (len(cell_data) - n_rows) // 2
        self._col_off = (len(cell_data[0]) - n_cols) // 2

        name = dungeon["settings"].get("name", "(unnamed)")
        self.setWindowTitle(f"Donjon Regen — {name}")
        self.statusBar().showMessage(f"Loaded: {name}  ({n_cols}×{n_rows})")
        self._save_action.setEnabled(True)
        self._resize_canvas_action.setEnabled(True)
        self._mirror_action.setEnabled(True)
        for act in self._tool_actions.values():
            act.setEnabled(True)
        # Reset to Select tool on every load.
        self._tool_actions[TOOL_SELECT].setChecked(True)
        self._map_view.set_tool(TOOL_SELECT)

        self._render_to_view()

        self._show_simple(
            "Click a room or corridor",
            "Click any cell on the map for details.",
        )

    def _render_to_view(self) -> None:
        pil_img = render_map(self._dungeon, cell_size=self._cell_size, gm_mode=True)
        pixmap = pil_to_qpixmap(pil_img)
        self._map_view.update_pixmap(pixmap, self._cell_size)

    def _on_scale_changed(self, value: int) -> None:
        if self._dungeon is None:
            return
        self._cell_size = self._dungeon["settings"]["cell_size"] * value
        self._render_to_view()

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

    def _show_cell_details(self, row: int, col: int, cell: int):
        if C.is_room(cell):
            rid = C.room_id(cell)
            room = self._find_room(rid)
            if room is not None:
                self._room_editor.set_room(room, self._dungeon)
                self._details_stack.setCurrentIndex(1)
                return
            self._show_simple(f"Room {rid}", f"(no metadata for room {rid})")
        elif C.is_corridor(cell):
            self._show_simple(
                f"Corridor  ({row}, {col})",
                self._format_corridor(row, col, cell),
            )
        elif C.has_door(cell):
            self._show_simple(
                f"Door  ({row}, {col})", f"Type: {C.door_type(cell)}"
            )
        else:
            self._show_simple(
                "Wandering Monsters", self._format_wandering_monsters()
            )

    # ------------------------------------------------------------------
    # Simple-view formatting (non-room cells)
    # ------------------------------------------------------------------

    def _show_simple(self, title: str, body: str):
        self._simple_title.setText(title)
        self._simple_text.setPlainText(body)
        self._details_stack.setCurrentIndex(0)

    def _format_wandering_monsters(self) -> str:
        wm = self._dungeon.get("wandering_monsters") or {}
        if not wm:
            return "(no wandering-monster table for this dungeon)"
        lines = ["Roll d6:", ""]
        for key in sorted(wm.keys(), key=lambda k: int(k) if str(k).isdigit() else k):
            lines.append(f"  {key}. {wm[key]}")
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

    # ------------------------------------------------------------------
    # Apply (room editor wrote back)
    # ------------------------------------------------------------------

    def _on_room_applied(self, room: dict, changed_doors: list, geometry_changed: bool):
        if changed_doors:
            self._update_door_cell_bits(changed_doors)
        if changed_doors or geometry_changed:
            # Geometry changes can also reshape the cells array (via resize_room
            # painting new cells); re-render and reload the editor so the
            # bounds line at the top reflects the new bbox.
            self._render_to_view()
            self._room_editor.set_room(room, self._dungeon)
            bits = []
            if changed_doors:
                bits.append(f"{len(changed_doors)} door type(s)")
            if geometry_changed:
                bits.append("geometry")
            self.statusBar().showMessage(
                f"Applied changes to room {room.get('id')} ({', '.join(bits)}); "
                "map re-rendered.",
                5000,
            )
        else:
            self.statusBar().showMessage(
                f"Applied changes to room {room.get('id')}.", 3000
            )

    def _on_room_delete(self, room: dict) -> None:
        rid = room.get("id", "?")
        choice = QMessageBox.warning(
            self,
            "Delete room",
            f"Delete room {rid}? This clears its cells and removes the room "
            "metadata. Doors and contents are lost permanently.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return
        if not dungeon_ops.delete_room(self._dungeon, int(rid)):
            self.statusBar().showMessage(f"Room {rid} not found.", 4000)
            return
        self._render_to_view()
        # Return the details panel to the simple-view page since the room
        # the editor was pointed at no longer exists.
        self._show_simple(
            f"Room {rid} deleted",
            "Click another cell on the map for details.",
        )
        self.statusBar().showMessage(f"Deleted room {rid}.", 5000)

    def _update_door_cell_bits(self, changed_doors: list):
        cells_arr = self._dungeon["cells"]
        for door in changed_doors:
            r = door.get("row")
            c = door.get("col")
            if r is None or c is None:
                continue
            cr = r + self._row_off
            cc = c + self._col_off
            if not (0 <= cr < len(cells_arr) and 0 <= cc < len(cells_arr[0])):
                continue
            cells_arr[cr][cc] = C.set_door_type(cells_arr[cr][cc], door["type"])

    # ------------------------------------------------------------------
    # Editing tools: brush / room-rect / structural ops
    # ------------------------------------------------------------------

    def _set_tool(self, tool: str) -> None:
        """Switch the Map_View's active tool. Also clears the cell-highlight
        rectangle so it doesn't sit on top of brush strokes."""
        self._map_view.set_tool(tool)
        # Select keeps the highlight, the others don't need it cluttering paint.
        if tool != TOOL_SELECT and self._map_view._highlight is not None:
            self._map_view._highlight.setVisible(False)
        elif tool == TOOL_SELECT and self._map_view._highlight is not None:
            self._map_view._highlight.setVisible(True)
        self.statusBar().showMessage(f"Tool: {tool}", 2000)

    def _on_cell_painted(self, row: int, col: int, tool: str) -> None:
        """One step of a brush stroke — mutate the cell but defer re-rendering
        until the stroke ends, so a long drag doesn't trigger N re-renders."""
        if self._dungeon is None:
            return
        if tool == TOOL_CORRIDOR:
            dungeon_ops.paint_corridor(self._dungeon, row, col)
        elif tool == TOOL_ERASER:
            dungeon_ops.erase_cell(self._dungeon, row, col)

    def _on_paint_stroke_ended(self, tool: str) -> None:
        """Re-render once at the end of a brush stroke."""
        if self._dungeon is None:
            return
        self._render_to_view()
        self.statusBar().showMessage(f"Stroke applied ({tool}).", 2000)

    def _on_rect_drawn(self, north: int, south: int, west: int, east: int) -> None:
        """Room-rect tool released. Clamp bounds to the canvas, refuse if any
        bbox cell already lives in a room, then prompt for shape and create."""
        if self._dungeon is None:
            return

        settings = self._dungeon["settings"]
        n_rows = settings["n_rows"]
        n_cols = settings["n_cols"]

        # Clamp to canvas — drag may finish outside the map.
        north = max(0, min(north, n_rows - 1))
        south = max(0, min(south, n_rows - 1))
        west = max(0, min(west, n_cols - 1))
        east = max(0, min(east, n_cols - 1))
        if south < north or east < west:
            return

        # Pre-flight the overlap check so we don't bother the user with a
        # dialog they'd immediately have to cancel.
        existing = dungeon_ops._bbox_room_id(self._dungeon, north, south, west, east)
        if existing is not None:
            self.statusBar().showMessage(
                f"Bbox overlaps room id {existing} — erase or shrink it first.",
                4000,
            )
            return

        cells_wide = east - west + 1
        cells_tall = south - north + 1

        dialog = New_Room_Dialog(self, cells_wide, cells_tall)
        if dialog.exec() != QDialog.Accepted:
            return

        shape, polygon_n = dialog.result_shape()
        try:
            room = dungeon_ops.create_room(
                self._dungeon,
                north, south, west, east,
                shape=shape, polygon_n=polygon_n,
            )
        except ValueError as e:
            QMessageBox.warning(self, "Cannot create room", str(e))
            return

        self._after_dungeon_mutated()
        descr = (
            f"polygon-{polygon_n}" if shape == "polygon" else shape
        )
        self.statusBar().showMessage(
            f"Created room {room['id']} ({descr}) at "
            f"rows {north}-{south}, cols {west}-{east}.",
            5000,
        )
        # Open the new room in the editor so the user can name it / add doors.
        self._room_editor.set_room(room, self._dungeon)
        self._details_stack.setCurrentIndex(1)
        self._map_view.highlight_cell(north, west)

    def _after_dungeon_mutated(self) -> None:
        """Re-sync derived state after a structural op (resize, mirror, …)
        and re-render. The cells array is normalised by `dungeon_ops`, so
        the row/col offsets are 0 — but we still recompute defensively in
        case a future op leaves padding in place."""
        cells_arr = self._dungeon["cells"]
        n_rows = self._dungeon["settings"]["n_rows"]
        n_cols = self._dungeon["settings"]["n_cols"]
        self._row_off = (len(cells_arr) - n_rows) // 2
        self._col_off = (len(cells_arr[0]) - n_cols) // 2
        self._render_to_view()

    def _on_resize_canvas(self) -> None:
        """Open the 4-direction resize dialog and apply the result. Negative
        deltas shrink that edge; if the shrink would discard rooms or stairs
        we ask the user to confirm first."""
        if self._dungeon is None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Resize Canvas")
        outer = QVBoxLayout(dialog)
        outer.addWidget(QLabel(
            "Add (positive) or remove (negative) rows / cols on each edge.\n"
            "Existing content stays anchored — adding to the north pushes\n"
            "every coordinate south."
        ))

        form = QFormLayout()
        outer.addLayout(form)

        def _signed_spin(initial: int = 0) -> QSpinBox:
            s = QSpinBox()
            s.setRange(-512, 512)
            s.setValue(initial)
            return s

        n_spin = _signed_spin()
        s_spin = _signed_spin()
        w_spin = _signed_spin()
        e_spin = _signed_spin()
        form.addRow("North Δ rows:", n_spin)
        form.addRow("South Δ rows:", s_spin)
        form.addRow("West Δ cols:",  w_spin)
        form.addRow("East Δ cols:",  e_spin)

        current = QLabel(
            f"Current size: {self._dungeon['settings']['n_cols']} "
            f"× {self._dungeon['settings']['n_rows']} (cols × rows)"
        )
        current.setStyleSheet("color: #666;")
        outer.addWidget(current)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        n_add = n_spin.value()
        s_add = s_spin.value()
        w_add = w_spin.value()
        e_add = e_spin.value()
        if (n_add, s_add, w_add, e_add) == (0, 0, 0, 0):
            return

        try:
            report = dungeon_ops.resize_canvas(
                self._dungeon, n_add, s_add, e_add, w_add
            )
        except dungeon_ops.Canvas_Resize_Conflict as conflict:
            lines = ["Shrinking will permanently discard:"]
            if conflict.dropped_rooms:
                ids = ", ".join(
                    str(r.get("id", "?")) for r in conflict.dropped_rooms
                )
                lines.append(f"  • Rooms: {ids}")
            if conflict.dropped_stairs:
                lines.append(f"  • {len(conflict.dropped_stairs)} stair(s)")
            lines.append("")
            lines.append("Continue anyway?")
            choice = QMessageBox.warning(
                self,
                "Confirm shrink",
                "\n".join(lines),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if choice != QMessageBox.Yes:
                return
            report = dungeon_ops.resize_canvas(
                self._dungeon, n_add, s_add, e_add, w_add, force=True
            )
        except ValueError as e:
            QMessageBox.critical(self, "Invalid size", str(e))
            return

        self._after_dungeon_mutated()
        new_rows = self._dungeon["settings"]["n_rows"]
        new_cols = self._dungeon["settings"]["n_cols"]
        msg = f"Canvas resized to {new_cols}×{new_rows}"
        if report["dropped_rooms"] or report["dropped_stairs"]:
            msg += (
                f" (dropped {len(report['dropped_rooms'])} room(s), "
                f"{len(report['dropped_stairs'])} stair(s))"
            )
        self.statusBar().showMessage(msg, 6000)

    def _on_mirror_rooms(self) -> None:
        """Open the mirror dialog, then call `dungeon_ops.mirror_rooms` in
        overwrite mode. Destination-side rooms overlapping any mirrored bbox
        are deleted; corridors / stairs are NOT mirrored."""
        if self._dungeon is None:
            return
        settings = self._dungeon["settings"]
        n_rows = settings["n_rows"]
        n_cols = settings["n_cols"]

        dialog = Mirror_Dialog(self, n_rows, n_cols)
        if dialog.exec() != QDialog.Accepted:
            return
        axis, pivot, source_side = dialog.result_settings()

        # The user-visible damage (deletions) is potentially large, so
        # confirm once before mutating. We can't easily pre-flight the
        # exact delete count without duplicating the op's logic, so the
        # warning is generic.
        choice = QMessageBox.warning(
            self,
            "Confirm mirror",
            f"Mirror {source_side} side across {axis} pivot {pivot} in overwrite mode.\n\n"
            "Any rooms on the destination side that overlap a mirrored bbox\n"
            "will be deleted permanently. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return

        try:
            report = dungeon_ops.mirror_rooms(
                self._dungeon, axis, pivot, source_side
            )
        except ValueError as e:
            QMessageBox.critical(self, "Mirror failed", str(e))
            return

        self._after_dungeon_mutated()
        msg = (
            f"Mirror complete: created {len(report['created_room_ids'])} room(s), "
            f"deleted {len(report['deleted_room_ids'])}"
        )
        if report["skipped_off_canvas"]:
            msg += f", skipped {len(report['skipped_off_canvas'])} off-canvas"
        self.statusBar().showMessage(msg + ".", 6000)

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

        worker = Save_Worker(self._dungeon, out_dir, self._scale_spin.value(), self)
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
