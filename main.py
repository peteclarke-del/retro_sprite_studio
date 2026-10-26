#!/usr/bin/env python3
"""8-Bit Sprite Studio: a small, native Linux editor for retro sprites."""
import copy
import json
import math
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

from PIL import Image
from PyQt5.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QApplication, QComboBox, QDockWidget, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QToolBar, QToolButton, QVBoxLayout, QWidget,
)

APP_NAME = "8-Bit Sprite Studio"
PROJECT_MAGIC = b"RSPRITE\x01"
FORMAT_VERSION = 1
MAX_SIDE = 256
MAX_FRAMES = 512
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 96 * 1024 * 1024
MAX_PROJECT_PIXELS = 16_000_000

GENERIC = ("#000000", "#ffffff", "#ed1c24", "#ff7f27", "#fff200", "#22b14c", "#00a2e8", "#3f48cc", "#a349a4", "#ffaec9", "#ffc90e", "#efe4b0", "#b97a57", "#c3c3c3", "#7f7f7f", "#880015")
SPECTRUM = ("#000000", "#0000d7", "#d70000", "#d700d7", "#00d700", "#00d7d7", "#d7d700", "#d7d7d7", "#000000", "#0000ff", "#ff0000", "#ff00ff", "#00ff00", "#00ffff", "#ffff00", "#ffffff")
C64 = ("#000000", "#ffffff", "#883932", "#67b6bd", "#8b3f96", "#55a049", "#40318d", "#bfce72", "#8b5429", "#574200", "#b86962", "#505050", "#787878", "#a4d785", "#7869c4", "#9f9f9f")
MSX = ("#000000", "#000000", "#3eb849", "#74d07d", "#5955e0", "#8076f1", "#b95e51", "#65dbef", "#db6559", "#ff897d", "#ccc35e", "#ded087", "#3aa241", "#b766b5", "#cccccc", "#ffffff")
CPC = ("#000000", "#000080", "#0000ff", "#800000", "#800080", "#8000ff", "#ff0000", "#ff0080", "#ff00ff", "#008000", "#008080", "#0080ff", "#808000", "#808080", "#8080ff", "#00ff00")


def profile(name, colors, default, *, sprite_limit=None, cell=None, cell_limit=None, note=""):
    return {"name": name, "palette": colors, "default": default,
            "sprite_limit": sprite_limit, "cell": cell,
            "cell_limit": cell_limit, "note": note}


PROFILES = {
    "generic": profile("Generic 16-colour pixel art", GENERIC, (32, 32), note="16-colour editor palette; no sprite or cell limit."),
    "bbc1": profile("BBC Micro / Electron · MODE 1", ("#000000", "#ff0000", "#ffff00", "#ffffff"), (16, 16), sprite_limit=4, note="Representative four-colour BBC mode."),
    "bbc2": profile("BBC Micro / Electron · MODE 2", ("#000000", "#ffffff"), (16, 16), sprite_limit=2, note="Two-colour BBC mode."),
    "bbc5": profile("BBC Micro / Electron · MODE 5", ("#000000", "#ff0000", "#ffff00", "#ffffff"), (16, 16), sprite_limit=4, note="Representative four-colour BBC mode."),
    "c64multi": profile("Commodore 64 / 128 · multicolour sprite", C64, (24, 21), sprite_limit=3, note="Up to three visible colours plus transparency; hardware multicolour pixels are double-width."),
    "c64hires": profile("Commodore 64 / 128 · hires sprite", C64, (24, 21), sprite_limit=1, note="One visible colour plus transparency."),
    "spectrum": profile("ZX Spectrum · attribute cells", SPECTRUM, (16, 16), cell=(8, 8), cell_limit=2, note="Each 8×8 cell is limited to two visible colours."),
    "cpc0": profile("Amstrad CPC · MODE 0", CPC, (16, 16), sprite_limit=16, note="Sixteen-colour editor subset; the complete CPC palette has more hardware colours."),
    "cpc1": profile("Amstrad CPC · MODE 1", CPC[:4], (16, 16), sprite_limit=4, note="Four-colour mode."),
    "cpc2": profile("Amstrad CPC · MODE 2", CPC[:2], (16, 16), sprite_limit=2, note="Two-colour mode."),
    "msx": profile("MSX · SCREEN 2", MSX, (16, 16), cell=(8, 8), cell_limit=15, note="MSX SCREEN 2 uses 8×1 colour patterns; this profile uses 8×8 cells as an editor-friendly approximation."),
    "nes": profile("Nintendo · NES sprite", SPECTRUM, (8, 8), cell=(8, 8), cell_limit=3, note="Up to three visible colours per 8×8 tile, plus transparency."),
    "gameboy": profile("Nintendo · Game Boy", ("#0f380f", "#306230", "#8bac0f", "#9bbc0f"), (8, 8), sprite_limit=4, note="Four shades."),
    "sms": profile("Sega · Master System", SPECTRUM, (8, 8), cell=(8, 8), cell_limit=3, note="Up to three visible colours per tile, plus transparency."),
    "snes": profile("Nintendo · SNES", SPECTRUM, (8, 8), cell=(8, 8), cell_limit=15, note="Up to 15 visible colours per tile, plus transparency."),
    "megadrive": profile("Sega · Mega Drive / Genesis", SPECTRUM, (8, 8), cell=(8, 8), cell_limit=15, note="Up to 15 visible colours per tile, plus transparency."),
}


def dimensions(frame):
    return len(frame[0]), len(frame)


def nearest_color(rgb, palette, allowed=None):
    choices = range(len(palette)) if allowed is None else allowed
    r, g, b = rgb
    return min(choices, key=lambda i: (30 * (r - int(palette[i][1:3], 16)) ** 2
                                      + 59 * (g - int(palette[i][3:5], 16)) ** 2
                                      + 11 * (b - int(palette[i][5:7], 16)) ** 2, i))


def color_rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def constrain_frame(frame, spec):
    """Apply sprite-wide and cell-local colour limits without touching transparency."""
    result = [row[:] for row in frame]
    sprite_limit = spec["sprite_limit"]
    if sprite_limit is not None:
        counts = Counter(px for row in result for px in row if px >= 0)
        if len(counts) > sprite_limit:
            keep = sorted(counts, key=lambda c: (-counts[c], c))[:sprite_limit]
            result = [[px if px < 0 or px in keep else nearest_color(
                color_rgb(spec["palette"][px]), spec["palette"], keep)
                for px in row] for row in result]
    if spec["cell"] and spec["cell_limit"]:
        cw, ch = spec["cell"]
        limit = spec["cell_limit"]
        h, w = len(result), len(result[0])
        for cy in range(0, h, ch):
            for cx in range(0, w, cw):
                cells = [(y, x) for y in range(cy, min(cy + ch, h))
                         for x in range(cx, min(cx + cw, w))]
                counts = Counter(result[y][x] for y, x in cells if result[y][x] >= 0)
                if len(counts) > limit:
                    keep = sorted(counts, key=lambda c: (-counts[c], c))[:limit]
                    for y, x in cells:
                        px = result[y][x]
                        if px >= 0 and px not in keep:
                            result[y][x] = nearest_color(color_rgb(spec["palette"][px]), spec["palette"], keep)
    return result


def convert_frame(frame, source, target):
    src_palette = source["palette"]
    converted = [[-1 if px < 0 else nearest_color(color_rgb(src_palette[min(px, len(src_palette) - 1)]), target["palette"])
                  for px in row] for row in frame]
    return constrain_frame(converted, target)


def bresenham(x0, y0, x1, y1):
    points = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return points


def polar_order(points, center):
    cx, cy = center
    return sorted(points, key=lambda p: (math.atan2(p[1] - cy, p[0] - cx),
                                        (p[0] - cx) ** 2 + (p[1] - cy) ** 2, p[1], p[0]))


def tween_frames(start, end, count, spec):
    """Create editable, centroid/polar-matched pixel frames between two keyframes."""
    if count < 0 or count > 100:
        raise ValueError("Choose between 0 and 100 in-between frames.")
    if dimensions(start) != dimensions(end):
        raise ValueError("Keyframes must have the same canvas size.")
    if count == 0:
        return []
    h, w = len(start), len(start[0])

    def all_points(frame):
        return [(x, y) for y, row in enumerate(frame) for x, color in enumerate(row) if color >= 0]

    def center(points):
        return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)) if points else (w / 2, h / 2)

    ca, cb = center(all_points(start)), center(all_points(end))
    by_color_a = {c: polar_order([(x, y) for y, row in enumerate(start) for x, v in enumerate(row) if v == c], ca)
                  for c in range(len(spec["palette"]))}
    by_color_b = {c: polar_order([(x, y) for y, row in enumerate(end) for x, v in enumerate(row) if v == c], cb)
                  for c in range(len(spec["palette"]))}
    generated = []
    for step in range(1, count + 1):
        t = step / (count + 1)
        frame = [[-1] * w for _ in range(h)]
        for color in range(len(spec["palette"])):
            a, b = by_color_a[color], by_color_b[color]
            if not a and not b:
                continue
            if not a and t <= 0.5:
                continue
            if not b and t > 0.5:
                continue
            pairs = max(len(a), len(b))
            for i in range(pairs):
                if a and b:
                    p, q = a[min(len(a) - 1, i * len(a) // pairs)], b[min(len(b) - 1, i * len(b) // pairs)]
                    x = round(p[0] + (q[0] - p[0]) * t)
                    y = round(p[1] + (q[1] - p[1]) * t)
                else:
                    p = (a or b)[i]
                    x, y = p
                if 0 <= x < w and 0 <= y < h:
                    frame[y][x] = color
        generated.append(constrain_frame(frame, spec))
    return generated


def resize_frame(frame, width, height):
    old_h, old_w = len(frame), len(frame[0])
    return [[frame[min(old_h - 1, y * old_h // height)][min(old_w - 1, x * old_w // width)]
             for x in range(width)] for y in range(height)]


def make_project_data(profile_key, frames, active, fps, tiles):
    return {"version": FORMAT_VERSION, "profile": profile_key, "active": active,
            "fps": fps, "frames": frames, "tiles": tiles}


def encode_project(data):
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(body) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Project is too large to save.")
    return PROJECT_MAGIC + zlib.compress(body, level=6)


def decode_project(blob):
    if len(blob) > MAX_FILE_BYTES or not blob.startswith(PROJECT_MAGIC):
        raise ValueError("That is not a supported 8-Bit Sprite Studio project.")
    inflater = zlib.decompressobj()
    body = inflater.decompress(blob[len(PROJECT_MAGIC):], MAX_UNCOMPRESSED_BYTES + 1)
    if len(body) > MAX_UNCOMPRESSED_BYTES or inflater.unconsumed_tail or not inflater.eof:
        raise ValueError("The project data is too large or incomplete.")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("The project file is damaged or invalid.") from exc
    if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
        raise ValueError("This project version is not supported.")
    key = data.get("profile")
    frames, tiles = data.get("frames"), data.get("tiles", [])
    if key not in PROFILES or not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError("The project has an invalid profile or frame count.")
    total = 0
    for frame in frames:
        if not isinstance(frame, list) or not 1 <= len(frame) <= MAX_SIDE:
            raise ValueError("The project has an invalid frame height.")
        if not isinstance(frame[0], list) or not 1 <= len(frame[0]) <= MAX_SIDE:
            raise ValueError("The project has an invalid frame width.")
        width = len(frame[0])
        if any(not isinstance(row, list) or len(row) != width for row in frame):
            raise ValueError("Every row in a frame must have the same width.")
        if any(not isinstance(px, int) or isinstance(px, bool) or px < -1 or px >= len(PROFILES[key]["palette"])
               for row in frame for px in row):
            raise ValueError("The project has an invalid palette index.")
        total += len(frame) * width
    if total > MAX_PROJECT_PIXELS:
        raise ValueError("The project contains too many pixels to open safely.")
    if not isinstance(tiles, list) or len(tiles) > 256:
        raise ValueError("The project has an invalid tile collection.")
    for tile in tiles:
        if (not isinstance(tile, list) or not 1 <= len(tile) <= MAX_SIDE
                or not isinstance(tile[0], list) or not 1 <= len(tile[0]) <= MAX_SIDE):
            raise ValueError("The project contains an invalid tile.")
        tw = len(tile[0])
        if (any(not isinstance(row, list) or len(row) != tw for row in tile)
                or any(not isinstance(px, int) or isinstance(px, bool) or px < -1 or px >= len(PROFILES[key]["palette"])
                       for row in tile for px in row)):
            raise ValueError("The project contains invalid tile pixels.")
    data["active"] = max(0, min(len(frames) - 1, int(data.get("active", 0))))
    data["fps"] = max(1, min(60, int(data.get("fps", 8))))
    return data


def rgba_image(frame, spec):
    h, w = len(frame), len(frame[0])
    image = Image.new("RGBA", (w, h))
    palette = [color_rgb(c) for c in spec["palette"]]
    image.putdata([(0, 0, 0, 0) if px < 0 else (*palette[min(px, len(palette) - 1)], 255)
                   for row in frame for px in row])
    return image


def load_image_frame(path, spec):
    image = Image.open(path).convert("RGBA")
    if image.width > MAX_SIDE or image.height > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.NEAREST)
    out = []
    for y in range(image.height):
        row = []
        for x in range(image.width):
            r, g, b, a = image.getpixel((x, y))
            row.append(-1 if a < 128 else nearest_color((r, g, b), spec["palette"]))
        out.append(row)
    return constrain_frame(out, spec)


def cell_violations(frame, spec):
    if not spec["cell"]:
        return 0
    cw, ch = spec["cell"]
    return sum(len({frame[y][x] for y in range(cy, min(cy + ch, len(frame)))
                    for x in range(cx, min(cx + cw, len(frame[0]))) if frame[y][x] >= 0}) > spec["cell_limit"]
               for cy in range(0, len(frame), ch) for cx in range(0, len(frame[0]), cw))


class SpriteCanvas(QWidget):
    strokeStarted = pyqtSignal()
    pixelEdited = pyqtSignal(int, int, int)
    operationRequested = pyqtSignal(str, int, int, int, int)
    selectionChanged = pyqtSignal(int, int, int, int)
    colorPicked = pyqtSignal(int)

    def __init__(self, get_frame, get_color, get_tool, parent=None):
        super().__init__(parent)
        self.get_frame, self.get_color, self.get_tool = get_frame, get_color, get_tool
        self.zoom = 18
        self.dragging = False
        self.last_cell = None
        self.line_start = None
        self.selection = None
        self.hover_cell = None
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def sizeHint(self):
        return self.minimumSize()

    def board_geometry(self):
        frame = self.get_frame()
        if not frame:
            return QRect(), 1
        h, w = len(frame), len(frame[0])
        scale = max(1.0, min(float(self.zoom), (self.width() - 36) / w, (self.height() - 36) / h))
        bw, bh = round(w * scale), round(h * scale)
        return QRect((self.width() - bw) // 2, (self.height() - bh) // 2, bw, bh), scale

    def cell_at(self, pos):
        board, scale = self.board_geometry()
        if not board.contains(pos):
            return None
        frame = self.get_frame()
        x = int((pos.x() - board.x()) / scale)
        y = int((pos.y() - board.y()) / scale)
        return (min(len(frame[0]) - 1, x), min(len(frame) - 1, y))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#171a21"))
        board, scale = self.board_geometry()
        if board.isNull():
            return
        frame = self.get_frame()
        painter.fillRect(board, QColor("#272b34"))
        palette = self.window().machine["palette"]
        for y, row in enumerate(frame):
            for x, color in enumerate(row):
                rect = QRect(round(board.x() + x * scale), round(board.y() + y * scale),
                             max(1, round((x + 1) * scale) - round(x * scale)),
                             max(1, round((y + 1) * scale) - round(y * scale)))
                if color < 0:
                    painter.fillRect(rect, QColor("#30343d" if (x + y) % 2 == 0 else "#232730"))
                else:
                    painter.fillRect(rect, QColor(palette[min(color, len(palette) - 1)]))
                if scale >= 8:
                    painter.setPen(QPen(QColor(18, 20, 26, 70), 1))
                    painter.drawRect(rect.adjusted(0, 0, -1, -1))
        if self.get_tool() == "Line" and self.line_start and self.hover_cell:
            painter.setPen(QPen(QColor(palette[max(0, self.get_color())]), 2, Qt.DashLine))
            for x, y in bresenham(*self.line_start, *self.hover_cell):
                rect = QRect(round(board.x() + x * scale), round(board.y() + y * scale),
                             max(1, round(scale)), max(1, round(scale)))
                painter.drawRect(rect.adjusted(1, 1, -2, -2))
        if self.selection:
            x, y, w, h = self.selection
            painter.setPen(QPen(QColor("#f6c85f"), 2, Qt.DashLine))
            painter.drawRect(QRect(round(board.x() + x * scale), round(board.y() + y * scale),
                                   max(1, round(w * scale)), max(1, round(h * scale))).adjusted(1, 1, -1, -1))
        painter.setPen(QPen(QColor("#abb5c7"), 1))
        painter.drawRect(board.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        cell = self.cell_at(event.pos())
        if cell is None:
            return
        self.setFocus()
        tool = self.get_tool()
        if tool in ("Pencil", "Eraser"):
            self.strokeStarted.emit()
            self.dragging, self.last_cell = True, cell
            self.pixelEdited.emit(cell[0], cell[1], -1 if tool == "Eraser" else self.get_color())
        elif tool == "Fill":
            self.strokeStarted.emit()
            self.operationRequested.emit("Fill", *cell, *cell)
        elif tool == "Picker":
            self.colorPicked.emit(self.get_frame()[cell[1]][cell[0]])
        elif tool in ("Line", "Select"):
            self.dragging = True
            self.line_start = cell
            if tool == "Select":
                self.selection = (cell[0], cell[1], 1, 1)
        self.update()

    def mouseMoveEvent(self, event):
        cell = self.cell_at(event.pos())
        self.hover_cell = cell
        if self.dragging and cell:
            tool = self.get_tool()
            if tool in ("Pencil", "Eraser") and self.last_cell:
                color = -1 if tool == "Eraser" else self.get_color()
                for x, y in bresenham(*self.last_cell, *cell):
                    self.pixelEdited.emit(x, y, color)
                self.last_cell = cell
            elif tool == "Select" and self.line_start:
                x0, y0 = self.line_start
                self.selection = (min(x0, cell[0]), min(y0, cell[1]),
                                  abs(cell[0] - x0) + 1, abs(cell[1] - y0) + 1)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.dragging:
            return
        cell = self.cell_at(event.pos())
        tool, start = self.get_tool(), self.line_start
        self.dragging, self.last_cell = False, None
        if cell and tool == "Line" and start:
            self.strokeStarted.emit()
            self.operationRequested.emit("Line", *start, *cell)
        elif cell and tool == "Select" and start:
            x0, y0 = start
            self.selection = (min(x0, cell[0]), min(y0, cell[1]),
                              abs(cell[0] - x0) + 1, abs(cell[1] - y0) + 1)
            self.selectionChanged.emit(*self.selection)
        self.line_start = None
        self.update()

    def wheelEvent(self, event):
        self.zoom = max(2, min(48, self.zoom + (2 if event.angleDelta().y() > 0 else -2)))
        self.update()


class SpriteStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.profile_key = "generic"
        self.machine = PROFILES[self.profile_key]
        w, h = self.machine["default"]
        self.frames = [[[-1] * w for _ in range(h)]]
        self.active = 0
        self.fps = 8
        self.tiles = []
        self.selected_color = 1
        self.current_tool = "Pencil"
        self.project_path = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.selection = None
        self.playing = False
        self._syncing = False
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1120, 720)
        self.resize(1500, 920)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #20232b; color: #e7e9ef; }
            QToolBar, QMenuBar { background: #292d37; border: 0; spacing: 5px; }
            QToolButton, QPushButton { background: #323744; color: #e7e9ef; border: 1px solid #444a59; border-radius: 5px; padding: 6px 9px; }
            QToolButton:hover, QPushButton:hover { background: #414858; }
            QToolButton:checked { background: #426a8d; border-color: #79b4dc; }
            QComboBox, QSpinBox, QListWidget { background: #292d37; color: #e7e9ef; border: 1px solid #444a59; border-radius: 4px; padding: 5px; }
            QDockWidget::title { background: #292d37; padding: 7px; }
            QStatusBar { background: #292d37; }
        """)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.next_frame)
        self._build_canvas()
        self._build_toolbar()
        self._build_machine_dock()
        self._build_tile_dock()
        self._build_timeline()
        self._build_menus()
        self.statusBar().showMessage("Ready · transparent canvas · wheel to zoom")
        self.refresh_all()

    def _build_canvas(self):
        self.canvas = SpriteCanvas(lambda: self.frames[self.active], lambda: self.selected_color,
                                   lambda: self.current_tool, self)
        self.canvas.strokeStarted.connect(self.record_undo)
        self.canvas.pixelEdited.connect(self.set_pixel)
        self.canvas.operationRequested.connect(self.canvas_operation)
        self.canvas.selectionChanged.connect(self.set_selection)
        self.canvas.colorPicked.connect(self.pick_color)
        self.setCentralWidget(self.canvas)

    def _action(self, title, callback, shortcut=None):
        action = QAction(title, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        return action

    def _build_toolbar(self):
        bar = QToolBar("Project and drawing tools", self)
        bar.setMovable(False)
        self.addToolBar(bar)
        for title, cb, shortcut in (("New", self.new_project, "Ctrl+N"),
                                    ("Open", self.open_project, "Ctrl+O"),
                                    ("Save", self.save_project, "Ctrl+S"),
                                    ("Import image", self.import_image, None),
                                    ("Export", self.export, None),
                                    ("Undo", self.undo, "Ctrl+Z"),
                                    ("Redo", self.redo, "Ctrl+Shift+Z")):
            action = self._action(title, cb, shortcut)
            bar.addAction(action)
        bar.addSeparator()
        group = QActionGroup(self)
        group.setExclusive(True)
        self.tool_actions = {}
        for name, shortcut in (("Pencil", "B"), ("Eraser", "E"), ("Line", "L"),
                               ("Fill", "G"), ("Picker", "I"), ("Select", "M")):
            action = self._action(name, lambda checked=False, n=name: self.set_tool(n), shortcut)
            action.setCheckable(True)
            group.addAction(action)
            bar.addAction(action)
            self.tool_actions[name] = action
        self.tool_actions["Pencil"].setChecked(True)
        bar.addSeparator()
        self.play_action = self._action("▶ Play", self.toggle_play, "Space")
        bar.addAction(self.play_action)
        self.frame_count_label = QLabel()
        bar.addWidget(self.frame_count_label)

    def _build_machine_dock(self):
        dock = QDockWidget("Machine & tools", self)
        dock.setObjectName("machineDock")
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(9)
        layout.addWidget(QLabel("TARGET HARDWARE"))
        self.profile_combo = QComboBox()
        for key, spec in PROFILES.items():
            self.profile_combo.addItem(spec["name"], key)
        self.profile_combo.currentIndexChanged.connect(self.change_profile)
        layout.addWidget(self.profile_combo)
        self.profile_note = QLabel()
        self.profile_note.setWordWrap(True)
        self.profile_note.setStyleSheet("color:#aeb7c6; font-size:11px")
        layout.addWidget(self.profile_note)
        layout.addWidget(self._separator())
        layout.addWidget(QLabel("CANVAS SIZE · PIXELS"))
        dims = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, MAX_SIDE)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, MAX_SIDE)
        dims.addWidget(QLabel("W")); dims.addWidget(self.width_spin)
        dims.addWidget(QLabel("H")); dims.addWidget(self.height_spin)
        layout.addLayout(dims)
        self.width_spin.valueChanged.connect(self.resize_canvas)
        self.height_spin.valueChanged.connect(self.resize_canvas)
        layout.addWidget(self._separator())
        layout.addWidget(QLabel("COLOUR PALETTE"))
        self.color_grid = QGridLayout()
        self.color_grid.setSpacing(4)
        layout.addLayout(self.color_grid)
        self.transparent_button = QPushButton("Transparent / erase")
        self.transparent_button.clicked.connect(lambda: self.pick_color(-1))
        layout.addWidget(self.transparent_button)
        self.color_hint = QLabel()
        self.color_hint.setWordWrap(True)
        self.color_hint.setStyleSheet("color:#aeb7c6; font-size:11px")
        layout.addWidget(self.color_hint)
        layout.addStretch(1)
        dock.setWidget(body)
        dock.setMinimumWidth(252)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_tile_dock(self):
        dock = QDockWidget("Tile shelf", self)
        dock.setObjectName("tileDock")
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(QLabel("Select a canvas region, then capture it as a reusable tile."))
        self.tile_list = QListWidget()
        self.tile_list.setIconSize(self._qt_size(72, 56))
        layout.addWidget(self.tile_list, 1)
        self.tile_count_label = QLabel()
        layout.addWidget(self.tile_count_label)
        buttons = QHBoxLayout()
        add = QPushButton("Capture selection")
        add.clicked.connect(self.capture_tile)
        place = QPushButton("Stamp tile")
        place.clicked.connect(self.place_tile)
        buttons.addWidget(add); buttons.addWidget(place)
        layout.addLayout(buttons)
        remove = QPushButton("Remove selected tile")
        remove.clicked.connect(self.remove_tile)
        layout.addWidget(remove)
        dock.setWidget(body)
        dock.setMinimumWidth(240)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def _build_timeline(self):
        dock = QDockWidget("Animation timeline", self)
        dock.setObjectName("timelineDock")
        body = QWidget()
        layout = QVBoxLayout(body)
        row = QHBoxLayout()
        for title, callback in (("＋ Blank frame", self.add_frame),
                                ("⧉ Duplicate", self.duplicate_frame),
                                ("− Delete", self.delete_frame),
                                ("Generate in-betweens…", self.open_tween_dialog)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(QLabel("Playback FPS"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(self.fps)
        self.fps_spin.valueChanged.connect(self.change_fps)
        row.addWidget(self.fps_spin)
        layout.addLayout(row)
        self.timeline = QListWidget()
        self.timeline.setFlow(QListWidget.LeftToRight)
        self.timeline.setWrapping(False)
        self.timeline.setMaximumHeight(112)
        self.timeline.currentRowChanged.connect(self.select_frame)
        layout.addWidget(self.timeline)
        dock.setWidget(body)
        dock.setMinimumHeight(155)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

    def _build_menus(self):
        project = self.menuBar().addMenu("&Project")
        for title, cb, key in (("New project", self.new_project, "Ctrl+N"),
                               ("Open project…", self.open_project, "Ctrl+O"),
                               ("Save project", self.save_project, "Ctrl+S"),
                               ("Save project as…", self.save_project_as, "Ctrl+Shift+S"),
                               ("Import image…", self.import_image, "Ctrl+I"),
                               ("Export…", self.export, "Ctrl+E")):
            project.addAction(self._action(title, cb, key))
        project.addSeparator()
        project.addAction(self._action("Quit", self.close, "Ctrl+Q"))
        edit = self.menuBar().addMenu("&Edit")
        edit.addAction(self._action("Undo", self.undo, "Ctrl+Z"))
        edit.addAction(self._action("Redo", self.redo, "Ctrl+Shift+Z"))
        view = self.menuBar().addMenu("&View")
        for dock in self.findChildren(QDockWidget):
            view.addAction(dock.toggleViewAction())

    @staticmethod
    def _qt_size(width, height):
        from PyQt5.QtCore import QSize
        return QSize(width, height)

    @staticmethod
    def _separator():
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#3a3f4b")
        return line

    def set_tool(self, name):
        self.current_tool = name
        if name in self.tool_actions:
            self.tool_actions[name].setChecked(True)
        self.statusBar().showMessage(f"{name} tool · {len(self.frames[self.active][0])}×{len(self.frames[self.active])} pixels", 2500)
        self.canvas.update()

    def _set_dirty(self):
        self.dirty = True
        self._update_title()

    def _update_title(self):
        name = self.project_path.name if self.project_path else "Untitled"
        self.setWindowTitle(f"{'● ' if self.dirty else ''}{name} — {APP_NAME}")

    def _snapshot(self):
        return {"profile": self.profile_key, "frames": copy.deepcopy(self.frames), "active": self.active,
                "fps": self.fps, "tiles": copy.deepcopy(self.tiles)}

    def record_undo(self):
        self.undo_stack.append(self._snapshot())
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._set_dirty()

    def _restore(self, state):
        self.profile_key, self.machine = state["profile"], PROFILES[state["profile"]]
        self.frames, self.active, self.fps = state["frames"], state["active"], state["fps"]
        self.tiles = state["tiles"]
        self.refresh_all()
        self._set_dirty()

    def undo(self):
        if not self.undo_stack:
            self.statusBar().showMessage("Nothing to undo", 1800)
            return
        self.redo_stack.append(self._snapshot())
        self._restore(self.undo_stack.pop())

    def redo(self):
        if not self.redo_stack:
            self.statusBar().showMessage("Nothing to redo", 1800)
            return
        self.undo_stack.append(self._snapshot())
        self._restore(self.redo_stack.pop())

    def refresh_palette(self):
        while self.color_grid.count():
            item = self.color_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, color in enumerate(self.machine["palette"]):
            button = QToolButton()
            button.setFixedSize(31, 27)
            border = "#ffffff" if self.selected_color == i else "#555b67"
            button.setStyleSheet(f"background:{color}; border:2px solid {border}; border-radius:4px")
            button.setToolTip(f"Colour {i}: {color.upper()} · click to select")
            button.clicked.connect(lambda _checked=False, index=i: self.pick_color(index))
            self.color_grid.addWidget(button, i // 4, i % 4)
        limits = []
        if self.machine["sprite_limit"] is not None:
            limits.append(f"{self.machine['sprite_limit']} visible colours per sprite")
        if self.machine["cell_limit"]:
            cw, ch = self.machine["cell"]
            limits.append(f"{self.machine['cell_limit']} per {cw}×{ch} cell")
        self.color_hint.setText("Hardware limit: " + ("; ".join(limits) if limits else "unlimited colours") + ".")
        self.profile_note.setText(self.machine["note"])
        self.color_grid.parentWidget().adjustSize()

    def refresh_timeline(self):
        self.timeline.blockSignals(True)
        self.timeline.clear()
        for i, frame in enumerate(self.frames):
            w, h = dimensions(frame)
            pix = QPixmap(84, 62)
            pix.fill(QColor("#20232b"))
            p = QPainter(pix)
            scale = max(1, min(68 // w, 48 // h))
            ox, oy = (84 - w * scale) // 2, (48 - h * scale) // 2
            for y, row in enumerate(frame):
                for x, color in enumerate(row):
                    c = QColor("#4a505c") if color < 0 else QColor(self.machine["palette"][min(color, len(self.machine["palette"]) - 1)])
                    p.fillRect(ox + x * scale, oy + y * scale, scale, scale, c)
            p.end()
            item = QListWidgetItem(QIcon(pix), f"Frame {i + 1:02d}  ·  {w}×{h}")
            item.setSizeHint(self._qt_size(112, 78))
            self.timeline.addItem(item)
        self.active = min(self.active, len(self.frames) - 1)
        self.timeline.setCurrentRow(self.active)
        self.timeline.blockSignals(False)
        self.frame_count_label.setText(f"  {len(self.frames)} frame{'s' if len(self.frames) != 1 else ''}")

    def refresh_tiles(self):
        self.tile_list.clear()
        for i, tile in enumerate(self.tiles):
            h, w = len(tile), len(tile[0])
            pix = QPixmap(72, 56)
            pix.fill(QColor("#272b34"))
            p = QPainter(pix)
            scale = max(1, min(64 // w, 48 // h))
            ox, oy = (72 - w * scale) // 2, (56 - h * scale) // 2
            for y, row in enumerate(tile):
                for x, color in enumerate(row):
                    p.fillRect(ox + x * scale, oy + y * scale,
                               scale, scale, QColor("#454a55") if color < 0 else QColor(self.machine["palette"][min(color, len(self.machine["palette"]) - 1)]))
            p.end()
            item = QListWidgetItem(QIcon(pix), f"Tile {i + 1} · {w}×{h}")
            item.setData(Qt.UserRole, i)
            self.tile_list.addItem(item)
        self.tile_count_label.setText(f"{len(self.tiles)} / 256 tiles")

    def refresh_all(self):
        self._syncing = True
        index = self.profile_combo.findData(self.profile_key)
        if index >= 0:
            self.profile_combo.setCurrentIndex(index)
        w, h = dimensions(self.frames[self.active])
        self.width_spin.setValue(w)
        self.height_spin.setValue(h)
        self.fps_spin.setValue(self.fps)
        self._syncing = False
        self.refresh_palette()
        self.refresh_timeline()
        self.refresh_tiles()
        self.canvas.selection = self.selection
        self.canvas.update()
        self._update_title()

    def pick_color(self, color):
        self.selected_color = max(-1, min(color, len(self.machine["palette"]) - 1))
        self.refresh_palette()

    def set_pixel(self, x, y, color):
        frame = self.frames[self.active]
        if 0 <= y < len(frame) and 0 <= x < len(frame[0]):
            color = -1 if color < 0 else min(color, len(self.machine["palette"]) - 1)
            if frame[y][x] != color:
                frame[y][x] = color
                self.canvas.update()

    def canvas_operation(self, operation, x0, y0, x1, y1):
        frame = self.frames[self.active]
        if operation == "Line":
            for x, y in bresenham(x0, y0, x1, y1):
                self.set_pixel(x, y, self.selected_color)
        elif operation == "Fill":
            old = frame[y0][x0]
            new = self.selected_color
            if old == new:
                return
            stack = [(x0, y0)]
            while stack:
                x, y = stack.pop()
                if not (0 <= x < len(frame[0]) and 0 <= y < len(frame)) or frame[y][x] != old:
                    continue
                frame[y][x] = new
                stack.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
            self.canvas.update()

    def set_selection(self, x, y, w, h):
        self.selection = (x, y, w, h)
        self.canvas.selection = self.selection
        self.statusBar().showMessage(f"Selection: {w}×{h} pixels at ({x}, {y})", 2500)
        self.canvas.update()

    def change_profile(self, index):
        if self._syncing or index < 0:
            return
        key = self.profile_combo.itemData(index)
        if key == self.profile_key:
            return
        self.record_undo()
        old, target = self.machine, PROFILES[key]
        self.frames = [convert_frame(frame, old, target) for frame in self.frames]
        self.tiles = [convert_frame(tile, old, target) for tile in self.tiles]
        self.profile_key, self.machine = key, target
        self.selected_color = min(max(self.selected_color, 0), len(target["palette"]) - 1)
        self.selection = None
        self.refresh_all()
        self.statusBar().showMessage(f"Converted all frames and tiles to {target['name']} · canvas dimensions preserved", 5000)

    def resize_canvas(self, _value):
        if self._syncing:
            return
        width, height = self.width_spin.value(), self.height_spin.value()
        old_w, old_h = dimensions(self.frames[self.active])
        if (width, height) == (old_w, old_h):
            return
        self.record_undo()
        self.frames = [resize_frame(frame, width, height) for frame in self.frames]
        self.selection = None
        self.refresh_all()

    def select_frame(self, row):
        if self._syncing or not 0 <= row < len(self.frames) or row == self.active:
            return
        self.active = row
        self.selection = None
        self.refresh_all()

    def add_frame(self):
        if len(self.frames) >= MAX_FRAMES:
            return self.statusBar().showMessage(f"Maximum of {MAX_FRAMES} frames reached", 2500)
        self.record_undo()
        w, h = dimensions(self.frames[self.active])
        self.frames.insert(self.active + 1, [[-1] * w for _ in range(h)])
        self.active += 1
        self.refresh_all()

    def duplicate_frame(self):
        if len(self.frames) >= MAX_FRAMES:
            return self.statusBar().showMessage(f"Maximum of {MAX_FRAMES} frames reached", 2500)
        self.record_undo()
        self.frames.insert(self.active + 1, copy.deepcopy(self.frames[self.active]))
        self.active += 1
        self.refresh_all()

    def delete_frame(self):
        if len(self.frames) <= 1:
            return self.statusBar().showMessage("A project must contain at least one frame", 2500)
        self.record_undo()
        self.frames.pop(self.active)
        self.active = min(self.active, len(self.frames) - 1)
        self.refresh_all()

    def change_fps(self, fps):
        if self._syncing or fps == self.fps:
            return
        self.record_undo()
        self.fps = fps

    def toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            self.play_action.setText("■ Stop")
            self.timer.start(max(16, round(1000 / self.fps)))
        else:
            self.timer.stop()
            self.play_action.setText("▶ Play")

    def next_frame(self):
        if self.frames:
            self.active = (self.active + 1) % len(self.frames)
            self.refresh_all()

    def open_tween_dialog(self):
        if len(self.frames) < 2:
            return QMessageBox.information(self, "Need two keyframes", "Create at least two frames before generating in-betweens.")
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle("Generate in-between frames")
        form = QFormLayout(dialog)
        start_box, end_box, count_box = QComboBox(), QComboBox(), QSpinBox()
        for i in range(len(self.frames)):
            start_box.addItem(f"Frame {i + 1:02d}", i)
            end_box.addItem(f"Frame {i + 1:02d}", i)
        start_box.setCurrentIndex(self.active)
        end_box.setCurrentIndex(len(self.frames) - 1 if self.active != len(self.frames) - 1 else 0)
        count_box.setRange(1, 100)
        count_box.setValue(4)
        form.addRow("Start keyframe", start_box)
        form.addRow("End keyframe", end_box)
        form.addRow("In-between frames", count_box)
        hint = QLabel("Matches opaque pixels by colour and shape position; generated frames remain fully editable.")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        a, b = start_box.currentData(), end_box.currentData()
        if a == b:
            return QMessageBox.warning(self, "Choose two keyframes", "Start and end keyframes must be different.")
        try:
            tween = tween_frames(self.frames[a], self.frames[b], count_box.value(), self.machine)
        except ValueError as exc:
            return QMessageBox.warning(self, "Could not generate frames", str(exc))
        if len(self.frames) + len(tween) > MAX_FRAMES:
            return QMessageBox.warning(self, "Too many frames", f"A project can contain at most {MAX_FRAMES} frames.")
        self.record_undo()
        self.frames[a + 1:a + 1] = tween
        self.active = a + len(tween)
        self.refresh_all()
        self.statusBar().showMessage(f"Generated {len(tween)} editable in-between frames", 3500)

    def capture_tile(self):
        if len(self.tiles) >= 256:
            return self.statusBar().showMessage("Tile shelf is full", 2500)
        frame = self.frames[self.active]
        x, y, w, h = self.selection or (0, 0, len(frame[0]), len(frame))
        tile = [row[x:x + w] for row in frame[y:y + h]]
        if not tile or not tile[0]:
            return
        self.record_undo()
        self.tiles.append(tile)
        self.refresh_tiles()
        self.tile_list.setCurrentRow(len(self.tiles) - 1)
        self.statusBar().showMessage(f"Captured {w}×{h} tile", 2500)

    def place_tile(self):
        item = self.tile_list.currentItem()
        if not item:
            return self.statusBar().showMessage("Select a tile to stamp", 2000)
        tile = self.tiles[item.data(Qt.UserRole)]
        self.record_undo()
        frame = self.frames[self.active]
        h, w = len(frame), len(frame[0])
        ox, oy = (w - len(tile[0])) // 2, (h - len(tile)) // 2
        for ty, row in enumerate(tile):
            for tx, color in enumerate(row):
                x, y = ox + tx, oy + ty
                if color >= 0 and 0 <= x < w and 0 <= y < h:
                    frame[y][x] = color
        self.canvas.update()

    def remove_tile(self):
        item = self.tile_list.currentItem()
        if item:
            self.record_undo()
            self.tiles.pop(item.data(Qt.UserRole))
            self.refresh_tiles()

    def import_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import sprite image", "", "Images (*.png *.gif *.bmp *.jpg *.jpeg *.webp)")
        if not path:
            return
        try:
            frame = load_image_frame(path, self.machine)
        except Exception as exc:
            QMessageBox.warning(self, "Image import failed", str(exc))
            return
        if len(self.frames) >= MAX_FRAMES:
            return QMessageBox.warning(self, "Too many frames", f"A project can contain at most {MAX_FRAMES} frames.")
        self.record_undo()
        self.frames.insert(self.active + 1, frame)
        self.active += 1
        self.selection = None
        self.refresh_all()
        self.statusBar().showMessage(f"Imported {Path(path).name} · converted to {self.machine['name']}", 4000)

    def _export_image(self, path):
        suffix = Path(path).suffix.lower()
        if suffix == ".gif":
            images = [rgba_image(frame, self.machine).convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
                      for frame in self.frames]
            images[0].save(path, save_all=True, append_images=images[1:], duration=round(1000 / self.fps),
                           loop=0, optimize=False, transparency=0, disposal=2)
        elif suffix == ".bin":
            frame = self.frames[self.active]
            Path(path).write_bytes(bytes(255 if c < 0 else c for row in frame for c in row))
        elif suffix == ".png":
            rgba_image(self.frames[self.active], self.machine).save(path)
        else:
            raise ValueError("Choose a PNG, animated GIF, or raw .bin export.")

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export sprite", "sprite.png",
                                               "PNG image (*.png);;Animated GIF (*.gif);;Raw 8-bit indices (*.bin)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".png"
        try:
            self._export_image(path)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        description = {".png": "current frame as PNG", ".gif": "animation as GIF", ".bin": "current frame as row-major palette indices (FF = transparent)"}.get(Path(path).suffix.lower(), "file")
        self.statusBar().showMessage(f"Exported {description}: {path}", 5000)

    def new_project(self):
        key = "generic"
        self.record_undo()
        self.profile_key, self.machine = key, PROFILES[key]
        w, h = self.machine["default"]
        self.frames, self.active, self.tiles = [[[-1] * w for _ in range(h)]], 0, []
        self.fps, self.project_path, self.selection = 8, None, None
        self.undo_stack.clear(); self.redo_stack.clear()
        self.dirty = False
        self.refresh_all()

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open sprite project", "", "8-Bit Sprite Studio (*.rsp)")
        if not path:
            return
        try:
            data = decode_project(Path(path).read_bytes())
        except Exception as exc:
            QMessageBox.warning(self, "Could not open project", str(exc))
            return
        self.profile_key, self.machine = data["profile"], PROFILES[data["profile"]]
        self.frames, self.active, self.fps, self.tiles = data["frames"], data["active"], data["fps"], data["tiles"]
        self.project_path, self.dirty, self.selection = Path(path), False, None
        self.undo_stack.clear(); self.redo_stack.clear()
        self.refresh_all()
        self.statusBar().showMessage(f"Opened {Path(path).name}", 3000)

    def save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save sprite project", "untitled.rsp", "8-Bit Sprite Studio (*.rsp)")
        if path:
            if not Path(path).suffix:
                path += ".rsp"
            self._write_project(path)

    def save_project(self):
        if self.project_path is None:
            return self.save_project_as()
        self._write_project(str(self.project_path))

    def _write_project(self, path):
        try:
            data = make_project_data(self.profile_key, self.frames, self.active, self.fps, self.tiles)
            Path(path).write_bytes(encode_project(data))
        except Exception as exc:
            QMessageBox.warning(self, "Could not save project", str(exc))
            return
        self.project_path, self.dirty = Path(path), False
        self._update_title()
        self.statusBar().showMessage(f"Saved native project: {path}", 3500)


def run_self_test():
    spec = PROFILES["spectrum"]
    start = [[-1] * 16 for _ in range(16)]
    end = [[-1] * 16 for _ in range(16)]
    for y in range(3, 7):
        for x in range(2, 6):
            start[y][x] = 10
    for y in range(8, 12):
        for x in range(9, 13):
            end[y][x] = 11
    converted = convert_frame(start, PROFILES["generic"], spec)
    assert len(converted) == 16 and cell_violations(converted, spec) == 0
    tween = tween_frames(start, end, 3, PROFILES["generic"])
    assert len(tween) == 3 and all(dimensions(frame) == (16, 16) for frame in tween)
    assert tween_frames(start, end, 0, PROFILES["generic"]) == []
    assert resize_frame(start, 8, 8)[0][0] == start[0][0]
    data = make_project_data("generic", [start, end], 1, 12, [start[:4]])
    assert decode_project(encode_project(data)) == data
    png = rgba_image(start, PROFILES["generic"])
    import io
    stream = io.BytesIO()
    png.save(stream, format="PNG")
    stream.seek(0)
    assert Image.open(stream).size == (16, 16)
    assert PROJECT_MAGIC == b"RSPRITE\x01"
    try:
        decode_project(b"not a project")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid project accepted")
    print("Self-test passed: profile conversion, cell limits, tween frames, resizing, project round-trip, PNG export, and invalid-file rejection.")


def main():
    if "--self-test" in sys.argv:
        run_self_test()
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    window = SpriteStudio()
    window.show()
    if "--smoke-test" in sys.argv:
        app.processEvents()
        assert window.canvas is not None and window.timeline.count() == 1
        assert window.profile_combo.count() == len(PROFILES)
        print(f"UI smoke test passed: {window.profile_combo.count()} hardware profiles, native canvas, timeline, and tile shelf initialized.")
        window.close()
        return 0
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
