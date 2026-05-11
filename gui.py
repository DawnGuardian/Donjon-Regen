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
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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
from generate import generate_dungeon
from renderer import render_map, RENDER_SCALE


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

    def set_map(self, pixmap: QPixmap, cell_size: int) -> None:
        self._scene.clear()
        self._highlight = None
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
    """Structured editor for a single room. `apply_clicked` is emitted with
    `(room, doors_with_type_change)` when the user clicks Apply."""

    apply_clicked = Signal(dict, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._room: dict | None = None

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

    # -- public ------------------------------------------------------------

    def set_room(self, room: dict) -> None:
        self._room = room
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

    def _on_apply(self) -> None:
        if self._room is None:
            return
        changed_doors = self._collect_edits()
        self.apply_clicked.emit(self._room, changed_doors)


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
                self._room_editor.set_room(room)
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

    def _on_room_applied(self, room: dict, changed_doors: list):
        if changed_doors:
            self._update_door_cell_bits(changed_doors)
            self._render_to_view()
            self.statusBar().showMessage(
                f"Applied changes to room {room.get('id')}; "
                f"{len(changed_doors)} door type(s) updated, map re-rendered.",
                5000,
            )
        else:
            self.statusBar().showMessage(
                f"Applied changes to room {room.get('id')}.", 3000
            )

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
