"""The Chest Tracker window."""

import ctypes
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from bot import BotRunner
from detection import CANCELLED, PENDING, PromptMatcher
from fortnite_stats import FortniteStatsError, fetch_stats
from overlay import ChestOverlay
from protocol import MAX_NAME_LENGTH, name_problem
from settings import Settings, Stats
from store import Store
from watcher import (
    VK_F8, VK_F10, KeyEdge, Sender, WatchEngine, capture_focused_screen, check_webhook, fortnite_focused,
    foreground_window_bounds, interact_check, selection_too_big, key_code
)
from widgets import (
    ASH, BONE, DUSK, EMBER, GOLD, GOLD_DEEP, GOLD_LIGHT, LINE, MINT, NIGHT, RAISED,
    ChestStage, LeaderRow, MatchMeter, StepBadge, TallyMarks,
)

log = logging.getLogger("chest-tracker")

STYLE = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 14px; color: {BONE}; }}
QMainWindow, QDialog, #page, QScrollArea, #scrollBody {{ background: {NIGHT}; }}
QScrollArea {{ border: none; }}
#topbar {{ background: {NIGHT}; border-bottom: 1px solid {LINE}; }}
#brand {{ font-family: "Bahnschrift SemiBold Condensed"; font-size: 23px; }}
#tab {{ background: transparent; border: none; border-bottom: 3px solid transparent; border-radius: 0;
        padding: 16px 16px 13px 16px; color: {ASH}; font-size: 15px; font-weight: 500; }}
#tab:hover {{ color: {BONE}; }}
#tab:checked {{ color: {GOLD}; border-bottom-color: {GOLD}; }}
#tab:focus {{ color: {BONE}; background: {DUSK}; }}
#heading {{ font-family: "Bahnschrift SemiBold Condensed"; font-size: 36px; }}
#subheading {{ font-family: "Bahnschrift SemiBold Condensed"; font-size: 21px; color: {BONE}; }}
#muted {{ color: {ASH}; }}
#small {{ color: {ASH}; font-size: 12px; }}
#rail {{ background: transparent; }}
#statCard, #activityCard {{ background: {DUSK}; border: 1px solid {LINE}; border-radius: 12px; }}
#cardTitle {{ color: {ASH}; font-size: 12px; font-weight: 700; letter-spacing: 1px; }}
#sessionCount {{ font-family: "Bahnschrift SemiBold Condensed"; font-size: 34px; color: {BONE}; }}
#activityList {{ background: transparent; border: none; outline: none; }}
#activityList::item {{ padding: 8px 0; border-bottom: 1px solid {LINE}; }}
#activityList::item:selected {{ background: transparent; }}
#todayCount {{ font-family: "Bahnschrift SemiBold Condensed"; font-size: 54px; color: {GOLD}; }}
#notice {{ background: {DUSK}; border: 1px solid {LINE}; border-radius: 8px; }}
#pill {{ border: 1px solid {LINE}; border-radius: 14px; min-height: 18px; padding: 4px 12px; font-size: 13px; }}
QLineEdit {{ background: {NIGHT}; border: 1px solid {LINE}; border-radius: 5px; padding: 9px 11px;
             selection-background-color: {GOLD}; selection-color: {NIGHT}; }}
QLineEdit:hover {{ border-color: {ASH}; }}
QLineEdit:focus {{ border: 1px solid {GOLD}; }}
QComboBox {{ background: {NIGHT}; border: 1px solid {LINE}; border-radius: 5px; padding: 8px 10px; min-height: 20px; }}
QComboBox:hover {{ border-color: {ASH}; }}
QComboBox:focus {{ border: 1px solid {GOLD}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {DUSK}; border: 1px solid {LINE}; selection-background-color: #2E2916; }}
QPushButton {{ background: {DUSK}; border: 1px solid {LINE}; border-radius: 5px; padding: 9px 15px; font-weight: 500; }}
QPushButton:hover {{ background: {RAISED}; border-color: {ASH}; }}
QPushButton:pressed {{ background: {NIGHT}; }}
QPushButton:focus {{ border: 1px solid {GOLD}; }}
QPushButton:disabled {{ color: #6C6B67; background: {NIGHT}; border-color: {DUSK}; }}
#primary {{ background: {GOLD}; color: {NIGHT}; border: 1px solid {GOLD}; font-weight: 600; }}
#primary:hover {{ background: {GOLD_LIGHT}; border-color: {GOLD_LIGHT}; }}
#primary:pressed {{ background: {GOLD_DEEP}; border-color: {GOLD_DEEP}; color: {BONE}; }}
#primary:focus {{ border: 2px solid {BONE}; }}
#primary:disabled {{ background: #2A2A2D; color: {ASH}; border-color: #2A2A2D; }}
#capture {{ text-align: left; padding: 12px 15px; font-family: "Bahnschrift SemiBold Condensed"; font-size: 19px; }}
#capture[listening="true"] {{ border: 1px solid {GOLD}; color: {GOLD}; background: {NIGHT}; }}
#step {{ background: transparent; }}
#stepHeader {{ background: transparent; border: none; border-radius: 5px; text-align: left; padding: 8px 10px;
               font-family: "Bahnschrift SemiBold Condensed"; font-size: 21px; }}
#stepHeader:hover {{ background: {DUSK}; }}
#stepHeader:focus {{ background: {DUSK}; color: {GOLD}; }}
#disclosure {{ background: transparent; border: none; text-align: left; padding: 6px 0; color: {ASH}; }}
#disclosure:hover {{ color: {BONE}; }}
#disclosure:focus {{ color: {GOLD}; }}
#promptThumb {{ background: {NIGHT}; border: 1px solid {LINE}; border-radius: 5px; padding: 8px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 6px 0; border: none; }}
QListWidget::item:selected {{ background: transparent; }}
QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{ width: 17px; height: 17px; border: 1px solid {ASH}; border-radius: 4px; background: {NIGHT}; }}
QCheckBox::indicator:checked {{ background: {GOLD}; border-color: {GOLD}; }}
QCheckBox:focus {{ color: {GOLD}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QMenu {{ background: {DUSK}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 7px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: {RAISED}; }}
QToolTip {{ background: {DUSK}; color: {BONE}; border: 1px solid {LINE}; padding: 4px; }}
"""

STATES = {
    "off": (ASH, "Paused"),
    "setup": (GOLD, "Setup isn't finished"),
    "waiting": (ASH, "Waiting for Fortnite"),
    "watching": (MINT, "Watching for chests"),
    "black": (EMBER, "Can't see the game"),
}

KEY_LABELS = {
    "BACKSPACE": "Backspace",
    "WHEELUP": "Scroll wheel up", "WHEELDOWN": "Scroll wheel down", "MOUSE1": "Left mouse",
    "MOUSE2": "Right mouse", "MOUSE3": "Middle mouse", "MOUSE4": "Mouse back", "MOUSE5": "Mouse forward",
}

MOUSE_NAMES = {
    Qt.MouseButton.LeftButton: "MOUSE1", Qt.MouseButton.RightButton: "MOUSE2",
    Qt.MouseButton.MiddleButton: "MOUSE3", Qt.MouseButton.BackButton: "MOUSE4",
    Qt.MouseButton.ForwardButton: "MOUSE5",
}


def key_label(name: str) -> str:
    return KEY_LABELS.get(name.upper(), name.upper())


def qt_key_name(key: int) -> str | None:
    """The settings name for a pressed Qt key, or None if it can't be used."""
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(key)
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
        number = key - Qt.Key.Key_F1 + 1
        return None if number == 8 else f"F{number}"  # F8 is used to capture the prompt
    return {
        Qt.Key.Key_Backspace: "BACKSPACE",
        Qt.Key.Key_Space: "SPACE", Qt.Key.Key_Shift: "SHIFT", Qt.Key.Key_Control: "CTRL",
        Qt.Key.Key_Alt: "ALT", Qt.Key.Key_Tab: "TAB",
    }.get(key)


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def dark_title_bar(window: QWidget) -> None:
    """Colors the Windows title bar to match the app. Older Windows just ignores this."""
    handle = int(window.winId())
    dwm = ctypes.windll.dwmapi
    enabled = ctypes.c_int(1)
    dwm.DwmSetWindowAttribute(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
    color = QColor(NIGHT)
    caption = ctypes.c_int(color.red() | color.green() << 8 | color.blue() << 16)
    dwm.DwmSetWindowAttribute(handle, 35, ctypes.byref(caption), ctypes.sizeof(caption))


def scroll_page(margins: tuple[int, int, int, int] = (40, 30, 40, 34)) -> tuple[QScrollArea, QVBoxLayout]:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget()
    body.setObjectName("scrollBody")
    scroll.setWidget(body)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(*margins)
    layout.setSpacing(14)
    return scroll, layout


class Pill(QLabel):
    def __init__(self):
        super().__init__()
        self.setObjectName("pill")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def show_state(self, color: str, text: str) -> None:
        self.setText(f'<span style="color:{color}">●</span>&nbsp; {text}')
        self.setAccessibleName(text)


class KeyCapture(QPushButton):
    """Click it to choose the key or input used for an action."""

    captured = Signal(str)

    def __init__(self, keyboard_only: bool = False, action_label: str = "interact key"):
        super().__init__()
        self.setObjectName("capture")
        self.current = "E"
        self.listening = False
        self.keyboard_only = keyboard_only
        self.action_label = action_label
        self.clicked.connect(self._listen)
        self._refresh()

    def set_key(self, name: str) -> None:
        self.current = name
        self._refresh()

    def _refresh(self) -> None:
        prompt = f"Press your {self.action_label} (Esc cancels)"
        self.setText(prompt if self.listening else key_label(self.current))
        self.setProperty("listening", self.listening)
        self.style().unpolish(self)
        self.style().polish(self)

    def _listen(self) -> None:
        self.listening = True
        self.grabKeyboard()
        if not self.keyboard_only:
            self.grabMouse()
        self._refresh()

    def _finish(self, name: str | None) -> None:
        self.listening = False
        self.releaseKeyboard()
        if not self.keyboard_only:
            self.releaseMouse()
        if name:
            self.current = name
            self.captured.emit(name)
        self._refresh()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self.listening:
            super().keyPressEvent(event)
        elif event.key() == Qt.Key.Key_Escape:
            self._finish(None)
        else:
            name = qt_key_name(event.key())
            if name:
                self._finish(name)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.listening:
            if self.keyboard_only:
                event.accept()
                return
            self._finish(MOUSE_NAMES.get(event.button()))
        else:
            super().mousePressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.listening and event.angleDelta().y():
            if self.keyboard_only:
                event.accept()
                return
            self._finish("WHEELUP" if event.angleDelta().y() > 0 else "WHEELDOWN")
        else:
            super().wheelEvent(event)


class SelectionView(QWidget):
    """Shows a screenshot and lets you drag a box. Big boxes zoom in so tiny text is easy to pick."""

    selected = Signal(object)  # (x, y, width, height) in image pixels, or None

    def __init__(self, image: np.ndarray):
        super().__init__()
        self.image = image
        height, width = image.shape[:2]
        self.qimage = QImage(image.data, width, height, image.strides[0], QImage.Format.Format_RGB32).copy()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(640, 360)
        self.reset()

    def reset(self) -> None:
        self.view = QRect(0, 0, self.qimage.width(), self.qimage.height())
        self.selection: QRect | None = None
        self.drag_start: QPoint | None = None
        self.drag_now: QPoint | None = None
        self.selected.emit(None)
        self.update()

    def target(self) -> QRect:
        scale = min(self.width() / self.view.width(), self.height() / self.view.height())
        width, height = int(self.view.width() * scale), int(self.view.height() * scale)
        return QRect((self.width() - width) // 2, (self.height() - height) // 2, width, height)

    def to_image(self, point: QPoint) -> QPoint:
        target = self.target()
        x = self.view.x() + (point.x() - target.x()) * self.view.width() / target.width()
        y = self.view.y() + (point.y() - target.y()) * self.view.height() / target.height()
        return QPoint(int(x), int(y))

    def to_widget(self, rect: QRect) -> QRect:
        target = self.target()
        scale = target.width() / self.view.width()
        return QRect(
            target.x() + int((rect.x() - self.view.x()) * scale),
            target.y() + int((rect.y() - self.view.y()) * scale),
            int(rect.width() * scale),
            int(rect.height() * scale),
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(DUSK))
        painter.drawImage(self.target(), self.qimage, self.view)
        painter.setPen(QPen(QColor(GOLD), 2))
        if self.drag_start and self.drag_now:
            painter.drawRect(QRect(self.drag_start, self.drag_now).normalized())
        elif self.selection:
            painter.drawRect(self.to_widget(self.selection))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.drag_start = self.drag_now = event.position().toPoint()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.drag_now = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        start, end = self.to_image(self.drag_start), self.to_image(event.position().toPoint())
        self.drag_start = self.drag_now = None
        box = QRect(start, end).normalized().intersected(self.qimage.rect())
        if box.width() < 6 or box.height() < 6:
            self.update()
            return
        if selection_too_big(box.width(), box.height(), self.qimage.height()):
            margin = 12
            self.view = box.adjusted(-margin, -margin, margin, margin).intersected(self.qimage.rect())
            self.selection = None
            self.selected.emit(None)
        else:
            self.selection = box
            self.selected.emit((box.x(), box.y(), box.width(), box.height()))
        self.update()


class PromptDialog(QDialog):
    """Guides the player through saving a picture of the SEARCH prompt."""

    def __init__(self, parent: QWidget, templates_dir: Path, search_area: list[float]):
        super().__init__(parent)
        self.templates_dir = templates_dir
        self.search_area = search_area
        self.setWindowTitle("Set up the chest prompt")
        self.setMinimumSize(560, 340)
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(28, 24, 28, 24)
        self.outer.setSpacing(14)
        self.heading = label("Capture the prompt", "heading")
        self.outer.addWidget(self.heading)
        self.instructions = label(
            "1.  Open Fortnite in <b>Windowed Fullscreen</b>.<br>"
            "2.  Walk up to a chest so the <b>SEARCH</b> prompt shows.<br>"
            "3.  Press <b>F8</b> while still in the game.",
            wrap=True,
        )
        self.outer.addWidget(self.instructions)
        self.status = label("Waiting for F8.", "muted", wrap=True)
        self.outer.addWidget(self.status)
        self.outer.addStretch(1)
        self.footer = QHBoxLayout()
        self.footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.footer.addWidget(cancel)
        self.outer.addLayout(self.footer)

        self.hotkey = KeyEdge(VK_F8)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(30)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    def done(self, result: int) -> None:
        # Cancel, Save or the window's X: either way, stop watching for F8. Otherwise the
        # hidden dialog stays alive and pops back up the next time F8 is pressed in game.
        self.timer.stop()
        super().done(result)

    def _poll(self) -> None:
        if not self.isVisible() or not self.hotkey.pressed():
            return
        if not fortnite_focused():
            self.status.setText("Fortnite wasn't the active window. Click into the game and press F8 again.")
            return
        self.timer.stop()
        self.show_selection(capture_focused_screen())
        self.raise_()
        self.activateWindow()

    def show_selection(self, image: np.ndarray) -> None:
        self.image = image
        self.selection = None
        self.heading.setText("Select the word SEARCH")
        self.instructions.setText(
            "Drag a box around the prompt to zoom in, then drag a tight box around only the word "
            "<b>SEARCH</b>. Leave out the chest, your character and the key icon."
        )
        self.status.hide()
        self.view = SelectionView(image)
        self.view.selected.connect(self._selected)
        self.outer.insertWidget(2, self.view, 1)
        self.outer.setStretch(4, 0)  # the empty space under the instructions goes to the screenshot

        self.preview = QLabel()
        self.footer.insertWidget(0, self.preview)
        again = QPushButton("Start over")
        again.clicked.connect(self.view.reset)
        self.footer.insertWidget(2, again)
        self.save = QPushButton("Save prompt")
        self.save.setObjectName("primary")
        self.save.setEnabled(False)
        self.save.clicked.connect(self._save)
        self.footer.addWidget(self.save)
        self.showMaximized()

    def _selected(self, box) -> None:
        self.selection = box
        self.save.setEnabled(box is not None)
        if box is None:
            self.preview.clear()
            return
        x, y, width, height = box
        rect = QRect(x, y, width, height)
        self.preview.setPixmap(QPixmap.fromImage(self.view.qimage.copy(rect)).scaledToHeight(
            min(44, height * 3), Qt.TransformationMode.FastTransformation
        ))

    def _save(self) -> None:
        x, y, width, height = self.selection
        screen_height, screen_width = self.image.shape[:2]
        left, top, right, bottom = self.search_area
        if (
            x < left * screen_width or x + width > right * screen_width
            or y < top * screen_height or y + height > bottom * screen_height
        ):
            QMessageBox.warning(
                self, "Outside the watched area",
                "That spot is too close to the screen edge for the app to watch. Select the prompt near the center.",
            )
            return
        gray = cv2.cvtColor(self.image[y:y + height, x:x + width], cv2.COLOR_BGRA2GRAY)
        self.templates_dir.mkdir(exist_ok=True)
        numbers = [int(p.stem.split("_")[-1]) for p in self.templates_dir.glob("chest_*.png")]
        cv2.imwrite(str(self.templates_dir / f"chest_{max(numbers, default=0) + 1}.png"), gray)
        self.accept()


class TrackerPage(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        columns = QHBoxLayout(self)
        columns.setContentsMargins(24, 16, 24, 20)
        columns.setSpacing(16)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        self.notice_frame = QFrame()
        self.notice_frame.setObjectName("notice")
        notice_row = QHBoxLayout(self.notice_frame)
        notice_row.setContentsMargins(16, 10, 12, 10)
        self.notice = label("", wrap=True)
        notice_row.addWidget(self.notice, 1)
        finish = QPushButton("Finish setup")
        finish.setObjectName("primary")
        finish.clicked.connect(lambda: window.go_to(1))
        notice_row.addWidget(finish)
        left.addWidget(self.notice_frame)

        left.addWidget(label("Your hunt at a glance", "heading"))
        left.addWidget(label("Your totals and recent activity, in one place.", "muted"))
        self.stage = ChestStage()
        left.addWidget(self.stage, 1)
        columns.addLayout(left, 1)

        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(360)
        side = QVBoxLayout(rail)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(10)

        today_card = QFrame()
        today_card.setObjectName("statCard")
        today_layout = QVBoxLayout(today_card)
        today_layout.setContentsMargins(18, 15, 18, 14)
        today_layout.setSpacing(5)
        today_layout.addWidget(label("TODAY", "cardTitle"))
        today_row = QHBoxLayout()
        today_row.setSpacing(8)
        self.today = label("0", "todayCount")
        today_row.addWidget(self.today)
        today_row.addStretch(1)
        self.tally = TallyMarks()
        self.tally.setFixedWidth(142)
        today_row.addWidget(self.tally, 0, Qt.AlignmentFlag.AlignVCenter)
        today_layout.addLayout(today_row)
        today_layout.addWidget(label("chests opened today", "muted"))
        side.addWidget(today_card)

        session_card = QFrame()
        session_card.setObjectName("statCard")
        session_layout = QVBoxLayout(session_card)
        session_layout.setContentsMargins(18, 14, 18, 13)
        session_layout.setSpacing(3)
        session_layout.addWidget(label("THIS SESSION", "cardTitle"))
        session_row = QHBoxLayout()
        self.session = label("0", "sessionCount")
        session_row.addWidget(self.session)
        session_row.addWidget(label("chests since launch", "muted"), 0, Qt.AlignmentFlag.AlignBottom)
        session_row.addStretch(1)
        session_layout.addLayout(session_row)
        side.addWidget(session_card)

        activity_card = QFrame()
        activity_card.setObjectName("activityCard")
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.setContentsMargins(18, 14, 18, 10)
        activity_layout.setSpacing(4)
        activity_layout.addWidget(label("Recent activity", "subheading"))
        activity_layout.addWidget(label("Latest tracker events", "small"))
        self.activity = QListWidget()
        self.activity.setObjectName("activityList")
        self.activity.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.activity.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.activity.setWordWrap(True)
        activity_layout.addWidget(self.activity, 1)
        self.empty = QListWidgetItem("Open a chest in Fortnite and it appears here.")
        self.empty.setForeground(QColor(ASH))
        self.activity.addItem(self.empty)
        side.addWidget(activity_card, 1)

        actions = QHBoxLayout()
        self.remove = QPushButton("Remove last chest")
        self.remove.setToolTip("Counted one by mistake? Press your undo key while Fortnite is focused.")
        self.remove.clicked.connect(lambda: window.record_chest(-1, "manual"))
        actions.addWidget(self.remove, 1)
        self.pause = QPushButton("Pause tracking")
        self.pause.setObjectName("primary")
        self.pause.clicked.connect(window.toggle_pause)
        actions.addWidget(self.pause)
        side.addLayout(actions)
        columns.addWidget(rail)

    def show_stats(self, stats: Stats) -> None:
        self.today.setText(f"{stats.today:,}")
        self.tally.set_count(stats.today)
        self.session.setText(f"{stats.session:,}")
        self.remove.setEnabled(stats.total > 0)

    def log(self, text: str, color: str = BONE) -> None:
        if self.empty is not None:
            self.activity.takeItem(self.activity.row(self.empty))
            self.empty = None
        top = self.activity.item(0)
        line = f"{datetime.now():%H:%M}   {text}"
        if top and top.text() == line:
            return
        item = QListWidgetItem(line)
        item.setForeground(QColor(color))
        self.activity.insertItem(0, item)
        while self.activity.count() > 100:
            self.activity.takeItem(self.activity.count() - 1)


class Step(QWidget):
    """One numbered setup step. Its body opens when the header is clicked."""

    opened = Signal(object)

    def __init__(self, number: int, title: str, last: bool = False):
        super().__init__()
        self.setObjectName("step")
        self.last = last
        self.title = title
        grid = QVBoxLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.badge = StepBadge(number)
        row.addWidget(self.badge)
        self.header = QPushButton(title)
        self.header.setObjectName("stepHeader")
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.header.clicked.connect(lambda: self.opened.emit(self))
        row.addWidget(self.header, 1)
        self.summary = label("", "muted")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.summary)
        grid.addLayout(row)
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(46, 6, 2, 14)
        self.content.setSpacing(10)
        grid.addWidget(self.body)
        grid.addSpacing(14)
        self.done = False
        self.set_open(False)

    def set_open(self, is_open: bool) -> None:
        self.is_open = is_open
        self.body.setVisible(is_open)
        self.summary.setVisible(not is_open)
        self.badge.set_status(self.done, is_open)

    def set_done(self, done: bool, summary: str) -> None:
        self.done = done
        self.summary.setText(summary if done else "Not done yet")
        self.badge.set_status(done, self.is_open)

    def paintEvent(self, event) -> None:
        if self.last:
            return
        p = QPainter(self)
        p.setPen(QPen(QColor(LINE), 2))
        p.drawLine(15, 34, 15, self.height())


class SetupPage(QWidget):
    webhook_result = Signal(bool, str)

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window_ = window
        settings = window.settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = scroll_page()
        outer.addWidget(scroll)
        layout.setSpacing(4)
        layout.addWidget(label("Setup", "heading"))
        layout.addWidget(label("Save a chest prompt to start. Name and Discord sharing are optional.", "muted"))
        layout.addSpacing(14)

        self.name_step = Step(1, "Your name")
        self.name_step.content.addWidget(label(
            "Optional. This is how you appear on your group's leaderboard if you connect Discord.",
            "muted", wrap=True,
        ))
        self.name = QLineEdit(settings["player_name"])
        self.name.setPlaceholderText("Player name")
        self.name.setMaxLength(MAX_NAME_LENGTH)
        self.name.editingFinished.connect(self._save_name)
        self.name_step.content.addWidget(self.name)
        self.name_status = label("", "small", wrap=True)
        self.name_status.hide()
        self.name_step.content.addWidget(self.name_status)

        self.webhook_step = Step(2, "Discord channel link")
        self.webhook_step.content.addWidget(label(
            "Optional. Without a webhook, counts stay on this PC. To share counts, paste the webhook "
            "URL your group shares. In Discord, open the channel's Edit Channel, "
            "then Integrations, then Webhooks. Keep it within your group, because anyone who has it "
            "can post to the channel.",
            "muted", wrap=True,
        ))
        row = QHBoxLayout()
        self.webhook = QLineEdit(settings["webhook_url"])
        self.webhook.setPlaceholderText("https://discord.com/api/webhooks/...")
        self.webhook.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.webhook.editingFinished.connect(self._save_webhook)
        row.addWidget(self.webhook, 1)
        self.test = QPushButton("Check link")
        self.test.clicked.connect(self._test_webhook)
        row.addWidget(self.test)
        self.webhook_step.content.addLayout(row)
        self.webhook_status = label("", "small", wrap=True)
        self.webhook_status.hide()
        self.webhook_step.content.addWidget(self.webhook_status)
        self.webhook_result.connect(self._show_webhook_result)

        self.key_step = Step(3, "Interact key")
        self.key_step.content.addWidget(label(
            "Click the button, then press the key or scroll the wheel the way you open chests in Fortnite.",
            "muted", wrap=True,
        ))
        self.capture = KeyCapture()
        self.capture.set_key(settings["interact_keys"][0])
        self.capture.captured.connect(self._save_key)
        self.key_step.content.addWidget(self.capture)
        self.key_step.content.addWidget(label(
            "Click to choose an undo key. It works only while Fortnite is focused; F10 is the default.",
            "muted", wrap=True,
        ))
        self.remove_capture = KeyCapture(keyboard_only=True, action_label="undo key")
        self.remove_capture.set_key(settings["remove_key"])
        self.remove_capture.captured.connect(self._save_remove_key)
        self.key_step.content.addWidget(self.remove_capture)

        self.prompt_step = Step(4, "Chest prompt", last=True)
        self.prompt_step.content.addWidget(label(
            "A small picture of the word SEARCH, so the app recognizes the prompt. Set it up again if "
            "you change your resolution or HUD scale.",
            "muted", wrap=True,
        ))
        self.gallery = QHBoxLayout()
        self.gallery.setSpacing(10)
        self.prompt_step.content.addLayout(self.gallery)
        add_row = QHBoxLayout()
        self.add_prompt = QPushButton("Set up prompt")
        self.add_prompt.setObjectName("primary")
        self.add_prompt.clicked.connect(self._calibrate)
        add_row.addWidget(self.add_prompt)
        add_row.addStretch(1)
        self.prompt_step.content.addLayout(add_row)
        self.prompt_step.content.addSpacing(8)
        self.prompt_step.content.addWidget(label("Live match", "subheading"))
        self.prompt_step.content.addWidget(label(
            "Look at a chest in Fortnite. The live bar should reach the marker. Drag the marker to "
            "change the required match: lower values are more permissive, while higher values are stricter.",
            "muted", wrap=True,
        ))
        self.meter = MatchMeter(settings["match_threshold"])
        self.meter.threshold_changed.connect(self._save_threshold)
        self.prompt_step.content.addWidget(self.meter)
        self.score_text = label("Not in game", "small")
        self.prompt_step.content.addWidget(self.score_text)

        self.steps = [self.name_step, self.webhook_step, self.key_step, self.prompt_step]
        for step in self.steps:
            step.opened.connect(self._open_step)
            layout.addWidget(step)

        layout.addSpacing(12)
        layout.addWidget(label("In-game overlay", "subheading"))
        layout.addWidget(label(
            "Show your live chest count over Fortnite. Turn on positioning to drag the badge somewhere "
            "that does not cover your HUD, then finish positioning before you play.",
            "muted", wrap=True,
        ))
        overlay_row = QHBoxLayout()
        self.overlay_enabled = QCheckBox("Show the chest count over Fortnite")
        self.overlay_enabled.setChecked(settings["overlay_enabled"])
        self.overlay_enabled.toggled.connect(window.set_overlay_enabled)
        overlay_row.addWidget(self.overlay_enabled, 1)
        self.overlay_position = QPushButton("Set overlay position")
        self.overlay_position.clicked.connect(self._toggle_overlay_position)
        overlay_row.addWidget(self.overlay_position)
        layout.addLayout(overlay_row)
        layout.addStretch(1)
        self.refresh_gallery()
        self.refresh_steps()
        self._show_name_problem(name_problem(settings["player_name"]))
        self._open_step(next((s for s in self.steps if not s.done), self.prompt_step))

    def _open_step(self, chosen: Step) -> None:
        for step in self.steps:
            step.set_open(step is chosen and not (step.is_open and step is chosen))

    def _toggle_overlay_position(self) -> None:
        self.window_.toggle_overlay_positioning()
        self.overlay_position.setText(
            "Done positioning" if self.window_._overlay_positioning else "Set overlay position"
        )

    def refresh_steps(self) -> None:
        settings = self.window_.settings
        name_summary = settings["player_name"] or (
            "Needed to share" if settings["webhook_url"] else "Optional"
        )
        self.name_step.set_done(not settings["webhook_url"] or bool(settings["player_name"]), name_summary)
        self.webhook_step.set_done(True, "Linked" if settings["webhook_url"] else "Local only")
        self.key_step.set_done(
            True,
            f"{key_label(settings['interact_keys'][0])} interact · {key_label(settings['remove_key'])} undo",
        )
        count = len(list(self.window_.templates_dir.glob("*.png")))
        self.prompt_step.set_done(count > 0, f"{count} saved")

    def refresh_gallery(self) -> None:
        while self.gallery.count():
            item = self.gallery.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        paths = sorted(self.window_.templates_dir.glob("*.png"))
        if not paths:
            self.gallery.addWidget(label("No prompt saved yet.", "small"))
        for path in paths:
            box = QWidget()
            holder = QVBoxLayout(box)
            holder.setContentsMargins(0, 0, 0, 0)
            holder.setSpacing(6)
            thumb = QLabel()
            thumb.setObjectName("promptThumb")
            image = QPixmap(str(path))
            if image.width() > 150 or image.height() > 60:
                image = image.scaled(150, 60, Qt.AspectRatioMode.KeepAspectRatio)
            thumb.setPixmap(image)
            remove = QPushButton("Remove")
            remove.clicked.connect(lambda _=False, p=path: self._remove(p))
            holder.addWidget(thumb)
            holder.addWidget(remove)
            self.gallery.addWidget(box)
        self.gallery.addStretch(1)

    def _remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)
        self.refresh_gallery()
        self.refresh_steps()
        self.window_.restart_engine()

    def _calibrate(self) -> None:
        dialog = PromptDialog(self, self.window_.templates_dir, self.window_.settings["search_area"])
        saved = dialog.exec()
        dialog.deleteLater()
        if saved:
            self.refresh_gallery()
            self.refresh_steps()
            self.window_.restart_engine()

    def _show_name_problem(self, problem: str | None) -> None:
        self.name_status.setVisible(bool(problem))
        if problem:
            self.name_status.setText(f'<span style="color:{EMBER}">{problem}</span>')

    def _save_name(self) -> None:
        name = self.name.text().strip()
        problem = name_problem(name)
        self._show_name_problem(problem)
        if problem:  # Discord would refuse every post under this name, so don't keep it
            return
        if name != self.window_.settings["player_name"]:
            self.window_.settings.set(player_name=name)
            self.refresh_steps()
            self.window_.apply_identity()

    def _save_webhook(self) -> None:
        url = self.webhook.text().strip()
        if url != self.window_.settings["webhook_url"]:
            self.window_.settings.set(webhook_url=url)
            self.refresh_steps()
            self.window_.apply_identity()
            if url:
                self._test_webhook()

    def _save_key(self, name: str) -> None:
        if name == self.window_.settings["remove_key"]:
            self.capture.set_key(self.window_.settings["interact_keys"][0])
            QMessageBox.warning(self, "Keys must be different", "Choose a different key for interact and undo.")
            return
        self.window_.settings.set(interact_keys=[name])
        self.refresh_steps()
        self.window_.restart_engine()

    def _save_remove_key(self, name: str) -> None:
        if name == self.window_.settings["interact_keys"][0]:
            self.remove_capture.set_key(self.window_.settings["remove_key"])
            QMessageBox.warning(self, "Keys must be different", "Choose a different key for interact and undo.")
            return
        if name == "BACKSPACE" or len(name) == 1 or name == "SPACE":
            # Typing in Fortnite (chat, search boxes, party names) would remove chests.
            answer = QMessageBox.question(
                self, "This key is used for typing",
                f"{key_label(name)} is pressed while typing in Fortnite, so chat or a search box could "
                "remove chests by accident. Use it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.remove_capture.set_key(self.window_.settings["remove_key"])
                return
        self.window_.settings.set(remove_key=name)
        self.refresh_steps()
        self.window_.restart_engine()

    def _save_threshold(self, value: float) -> None:
        if value != self.window_.settings["match_threshold"]:
            self.window_.settings.set(match_threshold=value)
            self.window_.restart_engine()

    def _test_webhook(self) -> None:
        url = self.webhook.text().strip()
        self.test.setEnabled(False)
        self.webhook_status.setText("Checking the link.")
        self.webhook_status.show()
        threading.Thread(
            target=lambda: self.webhook_result.emit(*check_webhook(url)), daemon=True
        ).start()

    def _show_webhook_result(self, ok: bool, message: str) -> None:
        self.test.setEnabled(True)
        color = MINT if ok else EMBER
        self.webhook_status.setText(f'<span style="color:{color}">{message}</span>')

    def show_score(self, score: float) -> None:
        self.meter.set_score(score)
        seen = score >= self.window_.settings["match_threshold"]
        self.score_text.setText(f"Match {score:.2f}, needs {self.meter.threshold:.2f}" + (". Prompt seen." if seen else ""))

    def clear_score(self, text: str) -> None:
        self.meter.set_score(0)
        self.score_text.setText(text)


class FortniteStatsPage(QWidget):
    result_ready = Signal(object, str)

    METRICS = (
        ("wins", "Wins"),
        ("winRate", "Win rate"),
        ("kd", "K/D"),
        ("kills", "Eliminations"),
    )

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window_ = window
        settings = window.settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = scroll_page()
        outer.addWidget(scroll)

        layout.addWidget(label("Fortnite stats", "heading"))
        layout.addWidget(label(
            "Look up Battle Royale lifetime or current-season stats. Fortnite-API.com is an independent "
            "service, not an Epic Games API. You provide its key and a public player name; this app does "
            "not sign in to Epic.",
            "muted", wrap=True,
        ))

        form = QFrame()
        form.setObjectName("statCard")
        fields = QGridLayout(form)
        fields.setContentsMargins(18, 16, 18, 16)
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(10)
        fields.addWidget(label("Fortnite-API key", "small"), 0, 0)
        self.api_key = QLineEdit(settings["fortnite_api_key"])
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Paste your API key; it stays in this app's local settings")
        fields.addWidget(self.api_key, 0, 1, 1, 2)
        api_link = QPushButton("Get API key")
        api_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://dash.fortnite-api.com/account")))
        fields.addWidget(api_link, 0, 3)

        fields.addWidget(label("Player name", "small"), 1, 0)
        self.account_name = QLineEdit(settings["fortnite_account_name"])
        self.account_name.setPlaceholderText("Fortnite display name")
        fields.addWidget(self.account_name, 1, 1)
        fields.addWidget(label("Platform", "small"), 1, 2)
        self.account_type = QComboBox()
        for text, value in (("Epic", "epic"), ("PlayStation", "psn"), ("Xbox", "xbl")):
            self.account_type.addItem(text, value)
        self._set_combo_data(self.account_type, settings["fortnite_account_type"])
        fields.addWidget(self.account_type, 1, 3)

        fields.addWidget(label("Stats period", "small"), 2, 0)
        self.time_window = QComboBox()
        self.time_window.addItem("Lifetime", "lifetime")
        self.time_window.addItem("Current season", "season")
        self._set_combo_data(self.time_window, settings["fortnite_stats_window"])
        fields.addWidget(self.time_window, 2, 1)
        self.refresh = QPushButton("Refresh stats")
        self.refresh.setObjectName("primary")
        self.refresh.clicked.connect(self._fetch)
        fields.addWidget(self.refresh, 2, 3)
        layout.addWidget(form)

        self.status = label("Enter your key and a player name, then select Refresh stats.", "small", wrap=True)
        layout.addWidget(self.status)
        self.profile_title = label("No stats loaded yet", "subheading")
        layout.addWidget(self.profile_title)

        layout.addWidget(label("Overall", "subheading"))
        self.overall_values = self._add_metric_cards(layout, 4)

        layout.addWidget(label("By game mode", "subheading"))
        self.mode_values = {}
        mode_grid = QGridLayout()
        mode_grid.setHorizontalSpacing(12)
        mode_grid.setVerticalSpacing(12)
        for column, (mode, title) in enumerate((("solo", "Solo"), ("duo", "Duos"), ("squad", "Squads"))):
            self.mode_values[mode] = self._add_mode_card(mode_grid, column, title)
        layout.addLayout(mode_grid)
        layout.addStretch(1)

        self.result_ready.connect(self._show_result)
        cached = settings["fortnite_stats_cache"]
        if isinstance(cached, dict) and cached.get("account"):
            self._render_stats(cached, cached=True)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _add_metric_cards(self, layout: QVBoxLayout, columns: int) -> dict[str, QLabel]:
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        values = {}
        for index, (key, title) in enumerate(self.METRICS):
            card = QFrame()
            card.setObjectName("statCard")
            box = QVBoxLayout(card)
            box.setContentsMargins(16, 12, 16, 12)
            box.setSpacing(4)
            box.addWidget(label(title, "cardTitle"))
            value = label("—", "sessionCount")
            box.addWidget(value)
            grid.addWidget(card, index // columns, index % columns)
            values[key] = value
        layout.addLayout(grid)
        return values

    def _add_mode_card(self, grid: QGridLayout, column: int, title: str) -> dict[str, QLabel]:
        card = QFrame()
        card.setObjectName("statCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)
        box.addWidget(label(title, "subheading"))
        values = {}
        for key, caption in (("wins", "Wins"), ("winRate", "Win rate"), ("kd", "K/D")):
            row = QHBoxLayout()
            row.addWidget(label(caption, "muted"))
            row.addStretch(1)
            value = label("—", "small")
            row.addWidget(value)
            box.addLayout(row)
            values[key] = value
        grid.addWidget(card, 0, column)
        return values

    def _fetch(self) -> None:
        key = self.api_key.text().strip()
        name = self.account_name.text().strip()
        account_type = self.account_type.currentData()
        time_window = self.time_window.currentData()
        self.window_.settings.set(
            fortnite_api_key=key,
            fortnite_account_name=name,
            fortnite_account_type=account_type,
            fortnite_stats_window=time_window,
        )
        self.status.setText("Fetching stats…")
        self.refresh.setEnabled(False)

        def request() -> None:
            try:
                result = fetch_stats(key, name, account_type, time_window)
            except FortniteStatsError as error:
                self.result_ready.emit(None, str(error))
            except Exception as error:
                log.warning("Fortnite stats lookup failed: %s", error.__class__.__name__)
                self.result_ready.emit(None, "The stats lookup failed unexpectedly. Please try again.")
            else:
                self.result_ready.emit(result, "")

        threading.Thread(target=request, name="fortnite-stats", daemon=True).start()

    def _show_result(self, result: object, error: str) -> None:
        self.refresh.setEnabled(True)
        if error:
            self.status.setText(error)
            return
        if not isinstance(result, dict):
            self.status.setText("The stats service returned no usable stats.")
            return
        self.window_.settings.set(fortnite_stats_cache=result)
        self._render_stats(result, cached=False)

    def _render_stats(self, result: dict, cached: bool) -> None:
        account = result.get("account") or {}
        stats = result.get("stats") or {}
        overall = stats.get("overall") or {}
        self.profile_title.setText(f"{account.get('name', 'Player')} · {result.get('time_window', 'lifetime').title()}")
        for key, _title in self.METRICS:
            self.overall_values[key].setText(self._format_stat(key, overall.get(key)))
        for mode, values in self.mode_values.items():
            row = stats.get(mode) or {}
            for key, value in values.items():
                value.setText(self._format_stat(key, row.get(key)))
        self.status.setText("Showing saved stats." if cached else f"Updated {datetime.now():%Y-%m-%d %H:%M}.")

    @staticmethod
    def _format_stat(key: str, value) -> str:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return "—"
        if key == "winRate":
            return f"{value:.1f}%"
        if key == "kd":
            return f"{value:.2f}"
        return f"{int(value):,}"


class GroupPage(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window_ = window
        settings = window.settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = scroll_page()
        outer.addWidget(scroll)

        header = QHBoxLayout()
        header.addWidget(label("Group", "heading"))
        header.addStretch(1)
        self.group_total = label("", "muted")
        header.addWidget(self.group_total, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(header)

        self.board = QVBoxLayout()
        self.board.setSpacing(2)
        layout.addLayout(self.board)
        layout.addSpacing(14)

        bot_row = QHBoxLayout()
        self.disclosure = QPushButton()
        self.disclosure.setObjectName("disclosure")
        self.disclosure.clicked.connect(lambda: self._show_connection(not self.connection.isVisible()))
        bot_row.addWidget(self.disclosure)
        bot_row.addStretch(1)
        self.pill = Pill()
        self.pill.show_state(ASH, "Bot off")
        bot_row.addWidget(self.pill)
        layout.addLayout(bot_row)

        self.connection = QWidget()
        section = QVBoxLayout(self.connection)
        section.setContentsMargins(0, 4, 0, 0)
        section.setSpacing(8)
        section.addWidget(label(
            "One person in your group runs the bot, and everyone else can skip this. It keeps the "
            "leaderboard and answers /chests and /leaderboard in Discord. If it's off, it catches up "
            "the next time it starts.",
            "muted", wrap=True,
        ))
        section.addWidget(label("Bot token", "small"))
        self.token = QLineEdit(settings["bot_token"])
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("From the Discord Developer Portal, on the Bot page")
        self.token.editingFinished.connect(self._save)
        section.addWidget(self.token)
        section.addWidget(label("Channel ID", "small"))
        self.channel = QLineEdit(settings["channel_id"])
        self.channel.setPlaceholderText("Right-click the channel in Discord, then Copy Channel ID")
        self.channel.editingFinished.connect(self._save)
        section.addWidget(self.channel)
        self.auto = QCheckBox("Start the bot when the app opens")
        self.auto.setChecked(settings["run_bot"])
        self.auto.toggled.connect(lambda on: settings.set(run_bot=on))
        section.addWidget(self.auto)
        row = QHBoxLayout()
        self.toggle = QPushButton("Start bot")
        self.toggle.setObjectName("primary")
        self.toggle.clicked.connect(window.toggle_bot)
        row.addWidget(self.toggle)
        portal = QPushButton("Open Developer Portal")
        portal.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://discord.com/developers/applications"))
        )
        row.addWidget(portal)
        row.addStretch(1)
        section.addLayout(row)
        self.message = label("", "small", wrap=True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        section.addWidget(self.message)
        layout.addWidget(self.connection)
        layout.addStretch(1)
        self._show_connection(not settings["bot_token"])

    def _show_connection(self, visible: bool) -> None:
        self.connection.setVisible(visible)
        self.disclosure.setText(("Hide" if visible else "Show") + " bot connection")

    def _save(self) -> None:
        self.window_.settings.set(bot_token=self.token.text().strip(), channel_id=self.channel.text().strip())

    def show_leaderboard(self, rows: list[tuple[str, int]], total: int) -> None:
        while self.board.count():
            item = self.board.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not rows:
            self.board.addWidget(label(
                "No chests on the leaderboard yet. The player running the bot sees totals here once "
                "anyone opens a chest.", "muted", wrap=True,
            ))
        me = self.window_.settings["player_name"].lower()
        top = rows[0][1] if rows else 0
        for rank, (name, count) in enumerate(rows, 1):
            self.board.addWidget(LeaderRow(rank, name, count, top, name.lower() == me))
        self.group_total.setText(f"{total:,} chests between you" if rows else "")


class MainWindow(QMainWindow):
    chest = Signal(int, str)
    attempt = Signal(str)
    engine_state = Signal(str)
    match_score = Signal(float)
    delivery = Signal(bool, str)
    bot_status = Signal(str, str)

    def __init__(self, folder: Path, icon: QIcon):
        super().__init__()
        self.folder = folder
        self.templates_dir = folder / "templates"
        self.settings = Settings(folder)
        self.stats = Stats(folder)
        self.engine: WatchEngine | None = None
        self.bot: BotRunner | None = None
        self.paused = False
        self.delivery_ok = True
        self.current_state = "off"
        self.leaderboard_seen = None
        self.overlay = ChestOverlay()
        self.overlay.set_count(self.stats.total)
        self.overlay.position_saved.connect(self._save_overlay_position)
        self._overlay_positioning = False
        self._overlay_screen = None

        self.setWindowTitle("Chest Tracker")
        self.setWindowIcon(icon)
        self.resize(1160, 780)
        self.setMinimumSize(920, 650)
        self.setStyleSheet(STYLE)

        root = QWidget()
        self.setCentralWidget(root)
        rows = QVBoxLayout(root)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("topbar")
        top = QHBoxLayout(topbar)
        top.setContentsMargins(20, 0, 20, 0)
        top.setSpacing(4)
        logo = QLabel()
        logo.setPixmap(icon.pixmap(28, 28))
        top.addWidget(logo)
        top.addSpacing(6)
        top.addWidget(label("Chest Tracker", "brand"))
        top.addSpacing(26)

        self.stack = QStackedWidget()
        self.tracker = TrackerPage(self)
        self.setup = SetupPage(self)
        self.group = GroupPage(self)
        self.fortnite_stats = FortniteStatsPage(self)
        self.nav = QButtonGroup(self)
        for index, (name, page) in enumerate(
            (("Tracker", self.tracker), ("Setup", self.setup), ("Group", self.group),
             ("Fortnite Stats", self.fortnite_stats))
        ):
            self.stack.addWidget(page)
            button = QPushButton(name)
            button.setObjectName("tab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav.addButton(button, index)
            top.addWidget(button)
        self.nav.idClicked.connect(self.stack.setCurrentIndex)
        self.nav.button(0).setChecked(True)
        top.addStretch(1)
        self.pill = Pill()
        top.addWidget(self.pill)
        rows.addWidget(topbar)
        rows.addWidget(self.stack, 1)

        self.chest.connect(self.record_chest)
        self.attempt.connect(self._on_attempt)
        self.engine_state.connect(self.set_state)
        self.match_score.connect(self.setup.show_score)
        self.delivery.connect(self._on_delivery)
        self.bot_status.connect(self._on_bot_status)

        self.sender = Sender(
            self.settings["webhook_url"], self.settings["player_name"],
            folder / "pending.json", self.delivery.emit,
        )
        threading.Thread(target=self.sender.run_forever, daemon=True).start()
        if self.sender.pending:
            self.sender.wake.set()

        self._build_tray(icon)
        self._show_stats()
        self.tracker.stage.set_count(self.stats.total)
        self.restart_engine()
        if self.settings["run_bot"] and self.bot_ready():
            self.toggle_bot()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh)
        self.refresh_timer.start(5000)
        self.overlay_timer = QTimer(self)
        self.overlay_timer.timeout.connect(self._sync_overlay)
        self.overlay_timer.start(200)
        self._refresh()
        self._sync_overlay()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    def _build_tray(self, icon: QIcon) -> None:
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        show = QAction("Open Chest Tracker", menu)
        show.triggered.connect(self.bring_to_front)
        menu.addAction(show)
        self.tray_pause = QAction("Pause", menu)
        self.tray_pause.triggered.connect(self.toggle_pause)
        menu.addAction(self.tray_pause)
        menu.addSeparator()
        leave = QAction("Quit", menu)
        leave.triggered.connect(self.quit_app)
        menu.addAction(leave)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.bring_to_front()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self.tray.show()

    def _show_stats(self) -> None:
        self.tracker.show_stats(self.stats)
        self.tray.setToolTip(f"Chest Tracker: {self.stats.total:,} chests")

    def go_to(self, index: int) -> None:
        self.nav.button(index).setChecked(True)
        self.stack.setCurrentIndex(index)

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def missing_setup(self) -> list[str]:
        missing = []
        if not list(self.templates_dir.glob("*.png")):
            missing.append("the chest prompt")
        return missing

    def restart_engine(self) -> None:
        if self.engine:
            self.engine.stop()
            self.engine.join(2)
            self.engine = None
        self._update_notice()
        if self.missing_setup():
            self.set_state("setup")
            return
        if self.paused:
            self.set_state("off")
            return
        threshold = min(0.99, max(0.5, float(self.settings["match_threshold"])))  # the meter's range
        matcher = PromptMatcher.from_folder(self.templates_dir, threshold)
        try:
            remove_code = key_code(self.settings["remove_key"])
        except ValueError as error:
            log.error("Undo key problem: %s. Using F10.", error)
            self.settings.set(remove_key="F10")
            self.setup.remove_capture.set_key("F10")
            self.setup.refresh_steps()
            remove_code = VK_F10
        try:
            interact = interact_check(self.settings["interact_keys"])
        except (ValueError, RuntimeError) as error:
            log.error("Interact key problem: %s", error)
            self.tracker.log(f"Interact key problem: {error}", EMBER)
            self.set_state("setup")
            return
        self.engine = WatchEngine(
            matcher, interact, self.settings["search_area"],
            self.chest.emit, self.engine_state.emit, self.match_score.emit, self.attempt.emit,
            remove_hotkey=remove_code,
        )
        self.engine.start()

    def apply_identity(self) -> None:
        self.sender.url = self.settings["webhook_url"]
        self.sender.player = self.settings["player_name"]
        self.sender.wake.set()
        self.restart_engine()

    def _update_notice(self) -> None:
        missing = self.missing_setup()
        self.tracker.notice_frame.setVisible(bool(missing))
        if missing:
            needed = missing[0] if len(missing) == 1 else f"{', '.join(missing[:-1])} and {missing[-1]}"
            self.tracker.notice.setText(f"To start counting, add {needed}.")

    def set_state(self, state: str) -> None:
        self.current_state = state
        color, text = STATES[state]
        self.pill.show_state(color, text)
        if state == "black":
            self.tracker.log("The game looks blank to the app. Set Fortnite to Windowed Fullscreen, and try turning HDR off.", EMBER)
        if state != "watching":
            self.setup.clear_score("Not in game" if state == "waiting" else text)

    def _on_attempt(self, event: str) -> None:
        if event == PENDING:
            self.tracker.stage.show_pending()
        elif event == CANCELLED:
            self.tracker.stage.show_cancelled()
            self.tracker.log("Search cancelled, not counted", ASH)

    def record_chest(self, amount: int, source: str) -> None:
        changed = self.stats.add(amount)  # 0 when there was nothing left to take off
        self.overlay.set_count(self.stats.total, celebrate=source == "detected" and changed > 0)
        # Only share what really changed, and only if Discord sharing is set up. Sending a
        # -1 for a chest that was never counted would push the group total below the truth.
        if changed and self.settings["webhook_url"] and self.settings["player_name"]:
            self.sender.add(changed)
        self._show_stats()
        if source == "detected":
            self.tracker.stage.show_opened(self.stats.total)
            self.tracker.log("Chest opened", GOLD)
        elif changed:
            self.tracker.stage.set_count(self.stats.total)
            self.tracker.log("Removed a chest", ASH)
        else:
            self.tracker.log("Nothing to remove, the count is already 0", ASH)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        text = "Resume tracking" if self.paused else "Pause tracking"
        self.tracker.pause.setText(text)
        self.tray_pause.setText("Resume" if self.paused else "Pause")
        self.restart_engine()

    def set_overlay_enabled(self, enabled: bool) -> None:
        self.settings.set(overlay_enabled=enabled)
        if not enabled and self._overlay_positioning:
            self.toggle_overlay_positioning()
        self._sync_overlay()

    def toggle_overlay_positioning(self) -> None:
        self._overlay_positioning = not self._overlay_positioning
        self.overlay.positioning = self._overlay_positioning
        if self._overlay_positioning:
            self._overlay_screen = self.screen() or QGuiApplication.primaryScreen()
            self._place_overlay(self._overlay_screen)
            self.overlay.set_click_through(False)
            self.overlay.show()
            self.overlay.raise_()
        else:
            self._sync_overlay()

    def _place_overlay(self, screen) -> None:
        if screen is None:
            return
        self._overlay_screen = screen
        bounds = screen.geometry()
        max_x = max(0, bounds.width() - self.overlay.width())
        max_y = max(0, bounds.height() - self.overlay.height())
        position = self.settings["overlay_position"]
        x = bounds.left() + round(max_x * min(1.0, max(0.0, position[0])))
        y = bounds.top() + round(max_y * min(1.0, max(0.0, position[1])))
        self.overlay.move(x, y)

    def _save_overlay_position(self, point: QPoint) -> None:
        screen = QGuiApplication.screenAt(self.overlay.frameGeometry().center())
        screen = screen or self._overlay_screen or self.overlay.screen()
        if screen is None:
            return
        self._overlay_screen = screen
        bounds = screen.geometry()
        max_x = max(1, bounds.width() - self.overlay.width())
        max_y = max(1, bounds.height() - self.overlay.height())
        left = min(bounds.left() + max_x, max(bounds.left(), point.x()))
        top = min(bounds.top() + max_y, max(bounds.top(), point.y()))
        self.overlay.move(left, top)
        x = (left - bounds.left()) / max_x
        y = (top - bounds.top()) / max_y
        self.settings.set(overlay_position=[x, y])

    def _sync_overlay(self) -> None:
        if self._overlay_positioning:
            self.overlay.set_click_through(False)
            if not self.overlay.isVisible():
                self.overlay.show()
            return
        if not self.settings["overlay_enabled"] or not fortnite_focused():
            self.overlay.set_click_through(False)
            self.overlay.hide()
            return

        bounds = foreground_window_bounds()
        center = QPoint((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)
        screen = QGuiApplication.screenAt(center) or self.screen() or QGuiApplication.primaryScreen()
        if screen is not self._overlay_screen:
            self._place_overlay(screen)
        self.overlay.set_click_through(True)
        if not self.overlay.isVisible():
            self.overlay.show()

    def _on_delivery(self, ok: bool, message: str) -> None:
        if ok != self.delivery_ok:
            if ok:
                self.tracker.log("Back online. Your chests were sent.", MINT)
            else:
                self.tracker.log(message, EMBER)
        self.delivery_ok = ok

    def bot_ready(self) -> bool:
        return bool(self.settings["bot_token"]) and self.settings["channel_id"].isdigit()

    def toggle_bot(self) -> None:
        self.group._save()
        if self.bot:
            self.group.toggle.setEnabled(False)
            threading.Thread(target=self.bot.stop, daemon=True).start()
            return
        if not self.bot_ready():
            self._on_bot_status("error", "Enter the bot token and a numeric channel ID first.")
            return
        self.bot = BotRunner(
            self.settings["bot_token"], int(self.settings["channel_id"]),
            self.folder / "chests.db", self.bot_status.emit,
        )
        self.group.toggle.setText("Stop bot")
        self.bot.start()

    def _on_bot_status(self, status: str, message: str) -> None:
        page = self.group
        color = {"running": MINT, "starting": GOLD, "problem": EMBER, "error": EMBER}.get(status, ASH)
        text = {"running": "Bot online", "starting": "Bot connecting", "problem": "Bot problem", "error": "Bot problem"}.get(status, "Bot off")
        page.pill.show_state(color, text)
        page.message.setText(message)
        if status in ("stopped", "error"):  # the bot's thread has finished
            self.bot = None
        if status in ("problem", "error"):
            page._show_connection(True)
        page.toggle.setEnabled(True)
        page.toggle.setText("Stop bot" if self.bot else "Start bot")
        self._refresh()

    def _refresh(self) -> None:
        self.stats.roll_over()
        self._show_stats()
        path = self.folder / "chests.db"
        rows, total = [], 0
        if path.exists():
            store = Store.read_only(path)
            try:
                rows, total = store.leaderboard(), store.total()
            except sqlite3.Error:  # the bot is creating the database right now
                pass
            finally:
                store.db.close()
        if (rows, total) != self.leaderboard_seen:  # rebuild only when something changed
            self.leaderboard_seen = (rows, total)
            self.group.show_leaderboard(rows, total)

    def closeEvent(self, event) -> None:
        if self._overlay_positioning:
            self.toggle_overlay_positioning()
            self.setup.overlay_position.setText("Set overlay position")
        if not QSystemTrayIcon.isSystemTrayAvailable():
            event.accept()
            self.quit_app()
            return
        event.ignore()
        self.hide()
        if not self.settings["tray_notice_shown"]:
            self.settings.set(tray_notice_shown=True)
            self.tray.showMessage(
                "Chest Tracker is still counting",
                "It's in the system tray. Right-click the icon and select Quit to exit.",
                self.windowIcon(), 5000,
            )

    def quit_app(self) -> None:
        self._shut_down()
        QApplication.quit()

    def _shut_down(self) -> None:
        self.refresh_timer.stop()
        self.overlay_timer.stop()
        self.overlay.close()
        if self.engine:
            self.engine.stop()
            self.engine.join(2)
        if self.bot:
            self.bot.stop()
        self.tray.hide()
        deadline = time.monotonic() + 2
        while self.sender.pending and time.monotonic() < deadline:
            time.sleep(0.1)
