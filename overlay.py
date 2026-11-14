"""Small, click-through chest count shown over Fortnite."""

import ctypes
import random

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from widgets import ASH, BONE, DUSK, EMBER, GOLD, GOLD_LIGHT, LINE, MINT


BASE_WIDTH, BASE_HEIGHT = 220, 76
MIN_SCALE, MAX_SCALE = 0.7, 2.0
MIN_OPACITY, MAX_OPACITY = 0.3, 1.0


def clamp(value, low: float, high: float, default: float) -> float:
    """A setting from a file that may have been edited by hand: always lands in range."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(high, max(low, number))


class ChestOverlay(QWidget):
    """A compact count badge that can be dragged during setup and ignored in game."""

    position_saved = Signal(object)

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.scale = 1.0
        self.opacity = 1.0
        self.setFixedSize(BASE_WIDTH, BASE_HEIGHT)
        self.count = 0
        self.paused = False
        self.positioning = False
        self.click_through = False
        self._drag_offset: QPoint | None = None
        self._particles: list[list[float | QColor]] = []
        self._flash = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_scale(self, scale: float) -> None:
        self.scale = clamp(scale, MIN_SCALE, MAX_SCALE, 1.0)
        self.setFixedSize(round(BASE_WIDTH * self.scale), round(BASE_HEIGHT * self.scale))
        self.update()

    def set_opacity(self, opacity: float) -> None:
        self.opacity = clamp(opacity, MIN_OPACITY, MAX_OPACITY, 1.0)
        self.update()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.update()

    def set_count(self, count: int, celebrate: bool = False) -> None:
        self.count = count
        if celebrate:
            colors = [QColor(GOLD), QColor(GOLD_LIGHT), QColor(BONE), QColor(ASH)]
            for _ in range(24):
                self._particles.append([
                    random.uniform(20, BASE_WIDTH - 20),
                    random.uniform(20, 46),
                    random.uniform(-2.8, 2.8),
                    random.uniform(-5.4, -1.0),
                    1.0,
                    random.choice(colors),
                ])
            self._flash = 1.0
            self._timer.start()
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._hide_from_screen_capture()

    def _hide_from_screen_capture(self) -> None:
        """Keep the badge out of screenshots, so the tracker never mistakes it for part of the game.

        The tracker reads the screen to find the SEARCH prompt. Without this, a badge dragged
        over the middle of the screen would be part of every frame it checks. Needs Windows 10
        version 2004 or newer; on older versions the call fails and nothing changes.
        """
        try:
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            display_affinity = ctypes.windll.user32.SetWindowDisplayAffinity
            display_affinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            display_affinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE)
        except (AttributeError, OSError):
            pass

    def set_click_through(self, enabled: bool) -> None:
        """Use Windows extended styles so mouse input passes through the overlay."""
        if enabled == self.click_through:
            return
        self.click_through = enabled
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        get_style = user32.GetWindowLongW
        set_style = user32.SetWindowLongW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_long
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        style = get_style(hwnd, -20)  # GWL_EXSTYLE
        transparent = 0x00000020  # WS_EX_TRANSPARENT
        style = style | transparent if enabled else style & ~transparent
        set_style(hwnd, -20, style)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)  # refresh style, without moving or activating

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.positioning and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.positioning and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        event.ignore()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.positioning and event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.position_saved.emit(self.pos())
            event.accept()
            return
        event.ignore()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self.opacity)
        painter.scale(self.scale, self.scale)  # everything below is drawn at the base size
        background = QColor(DUSK)
        background.setAlpha(232)
        painter.setPen(QPen(QColor(LINE), 1.5))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(1, 1, BASE_WIDTH - 2, BASE_HEIGHT - 2), 13, 13)

        if self.positioning:
            heading, heading_color = "DRAG TO POSITION", MINT
        elif self.paused:
            heading, heading_color = "PAUSED", EMBER
        else:
            heading, heading_color = "CHESTS OPENED", GOLD
        painter.setPen(QColor(heading_color))
        small = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        painter.setFont(small)
        painter.drawText(14, 22, heading)

        painter.setPen(QColor(ASH if self.paused and not self.positioning else BONE))
        large = QFont("Bahnschrift SemiBold Condensed", 27, QFont.Weight.Bold)
        painter.setFont(large)
        painter.drawText(14, 59, f"{self.count:,}")

        if self._flash > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(212, 175, 55, int(self._flash * 70)))
            painter.drawRoundedRect(QRectF(2, 2, BASE_WIDTH - 4, BASE_HEIGHT - 4), 12, 12)
        for x, y, _vx, _vy, life, color in self._particles:
            painter.setPen(Qt.PenStyle.NoPen)
            particle = QColor(color)
            particle.setAlphaF(max(0.0, float(life)))
            painter.setBrush(particle)
            painter.drawEllipse(int(x), int(y), 5, 5)
        painter.end()

    def _tick(self) -> None:
        for particle in self._particles:
            particle[0] = float(particle[0]) + float(particle[2])
            particle[1] = float(particle[1]) + float(particle[3])
            particle[3] = float(particle[3]) + 0.22
            particle[4] = float(particle[4]) - 0.025
        self._particles = [p for p in self._particles if float(p[4]) > 0]
        self._flash = max(0.0, self._flash - 0.045)
        if not self._particles and self._flash == 0:
            self._timer.stop()
        self.update()
