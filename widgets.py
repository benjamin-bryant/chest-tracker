"""The app's hand-drawn pieces: the chest stage, tally marks, match meter and leaderboard rows."""

import ctypes
import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

NIGHT = "#101112"
DUSK = "#202123"
LINE = "#3B3C3F"
GOLD = "#D6AE4F"
EMBER = "#D95F45"
BONE = "#F2F2EE"
LILAC = "#A5A7AB"
MINT = "#69C7A0"


def display_font(size: float, weight: str = "SemiBold") -> QFont:
    """Bahnschrift Condensed ships with Windows 10 and 11. Qt falls back cleanly without it."""
    font = QFont(f"Bahnschrift {weight} Condensed")
    font.setPixelSize(max(1, int(size)))
    return font


def animations_enabled() -> bool:
    """False when the player turned off animation effects in Windows."""
    enabled = ctypes.c_int(1)
    ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    return bool(enabled.value)


@dataclass
class Coin:
    x: float
    y: float
    vx: float
    vy: float
    spin: float
    size: float
    life: float = 1.0


class ChestStage(QWidget):
    """The main stage: a chest that reacts to what the tracker sees, above the count."""

    HOVER_LID = 0.14
    PENDING_LID = 0.3

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(380, 400)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAccessibleName("Chests opened: 0")
        self.count = 0
        self.lid = 0.0
        self.lid_speed = 0.0
        self.lid_target = 0.0
        self.resting_lid = 0.0
        self.searching = False
        self.flash = 0.0
        self.shake = 0.0
        self.pop = 0.0
        self.wobble = 0.0
        self.coins: list[Coin] = []
        self.animate = animations_enabled()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.timeout.connect(self._settle)

    def set_count(self, count: int) -> None:
        self.count = count
        self.setAccessibleName(f"Chests opened: {count:,}")
        self.update()

    def show_pending(self) -> None:
        """A search looks real so far: the lid lifts while the app confirms it."""
        self.searching = True
        self.close_timer.stop()
        self._move_lid(self.PENDING_LID)

    def show_opened(self, count: int) -> None:
        self.searching = False
        self.set_count(count)
        self._move_lid(1.0)
        if self.animate:
            self.flash = 1.0
            self.pop = 1.0
            self._burst()
            self.timer.start()
        self.close_timer.start(1500)

    def show_cancelled(self) -> None:
        self.searching = False
        self.close_timer.stop()
        self._move_lid(self.resting_lid)
        if self.animate:
            self.shake = 1.0
            self.timer.start()

    def _settle(self) -> None:
        self._move_lid(self.resting_lid)

    def _move_lid(self, target: float) -> None:
        self.lid_target = target
        if self.animate:
            self.timer.start()
        else:
            self.lid = target
            self.update()

    def enterEvent(self, event) -> None:
        self.resting_lid = self.HOVER_LID
        if not self.searching and not self.close_timer.isActive():
            self._move_lid(self.resting_lid)

    def leaveEvent(self, event) -> None:
        self.resting_lid = 0.0
        if not self.searching and not self.close_timer.isActive():
            self._move_lid(0.0)

    def _burst(self) -> None:
        center, _, width = self._chest_box()
        for _ in range(34):
            angle = random.uniform(-2.65, -0.5)
            speed = random.uniform(0.34, 0.95) * width * 0.052
            self.coins.append(Coin(
                center + random.uniform(-0.2, 0.2) * width,
                self._body_top(),
                math.cos(angle) * speed,
                math.sin(angle) * speed,
                random.uniform(0, 6.28),
                random.uniform(0.018, 0.034) * width,
            ))

    def _tick(self) -> None:
        # A lightly damped spring, so the lid overshoots and slams like something with weight.
        self.lid_speed += (self.lid_target - self.lid) * 0.075
        self.lid_speed *= 0.76
        self.lid += self.lid_speed
        self.flash = max(0.0, self.flash - 0.024)
        self.shake = max(0.0, self.shake - 0.05)
        self.pop = max(0.0, self.pop - 0.035)
        if self.searching:
            self.wobble += 0.3

        gravity = self._chest_box()[2] * 0.0024
        for coin in self.coins:
            coin.vy += gravity
            coin.x += coin.vx
            coin.y += coin.vy
            coin.spin += 0.3
            coin.life -= 0.016
        self.coins = [coin for coin in self.coins if coin.life > 0]

        at_rest = abs(self.lid_target - self.lid) < 0.002 and abs(self.lid_speed) < 0.002
        if at_rest and not self.searching and not (self.coins or self.flash or self.shake or self.pop):
            self.lid = self.lid_target
            self.timer.stop()
        self.update()

    def _chest_box(self) -> tuple[float, float, float]:
        width = min(self.width() * 0.49, self.height() * 0.49)
        return self.width() * 0.73, self.height() * 0.83, width

    def _body_top(self) -> float:
        _, bottom, width = self._chest_box()
        return bottom - width * 0.42

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_backdrop(p)

        center, bottom, width = self._chest_box()
        if self.shake:
            center += math.sin(self.shake * 26) * self.shake * width * 0.03
        if self.searching:
            center += math.sin(self.wobble) * width * 0.006

        openness = max(0.0, self.lid)
        self._paint_light(p, center, width, openness)
        self._paint_chest(p, center, bottom, width, openness)
        self._paint_coins(p)
        self._paint_count(p)
        p.end()

    def _paint_backdrop(self, p: QPainter) -> None:
        p.fillRect(self.rect(), QColor(NIGHT))
        center, bottom, width = self._chest_box()
        glow = QRadialGradient(QPointF(center, bottom - width * 0.2), width * 1.25)
        strength = 0.08 + 0.24 * self.flash
        color = QColor(GOLD)
        color.setAlphaF(strength)
        glow.setColorAt(0, color)
        glow.setColorAt(1, QColor(16, 17, 18, 0))
        card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = QPainterPath()
        shape.addRoundedRect(card, 18, 18)
        p.setPen(QPen(QColor(LINE), 1))
        p.setBrush(QColor(DUSK))
        p.drawPath(shape)
        p.save()
        p.setClipPath(shape)
        p.fillRect(self.rect(), QBrush(glow))
        p.restore()

        floor = QRadialGradient(QPointF(center, bottom + width * 0.015), width * 0.68)
        floor.setColorAt(0, QColor(8, 8, 9, 170))
        floor.setColorAt(1, QColor(8, 8, 9, 0))
        p.save()
        p.translate(center, bottom + width * 0.015)
        p.scale(1, 0.13)
        p.translate(-center, -(bottom + width * 0.015))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(floor))
        p.drawEllipse(QPointF(center, bottom + width * 0.015), width * 0.68, width * 0.68)
        p.restore()

    def _paint_light(self, p: QPainter, center: float, width: float, openness: float) -> None:
        if openness < 0.03:
            return
        top = self._body_top()
        strength = min(1.0, openness * 1.15) * (0.5 + 0.5 * self.flash)
        p.setPen(Qt.PenStyle.NoPen)
        for offset, reach, spread in ((-0.27, 0.5, 0.15), (0.0, 0.6, 0.2), (0.27, 0.5, 0.15)):
            beam = QPainterPath(QPointF(center + (offset - 0.09) * width, top))
            beam.lineTo(center + (offset * 1.9 - spread) * width, top - reach * width * openness)
            beam.lineTo(center + (offset * 1.9 + spread) * width, top - reach * width * openness)
            beam.lineTo(center + (offset + 0.09) * width, top)
            beam.closeSubpath()
            gradient = QLinearGradient(0, top, 0, top - reach * width * openness)
            near = QColor(GOLD)
            near.setAlphaF(0.72 * strength)
            far = QColor(GOLD)
            far.setAlphaF(0.0)
            gradient.setColorAt(0, near)
            gradient.setColorAt(1, far)
            p.setBrush(QBrush(gradient))
            p.drawPath(beam)

    def _paint_chest(self, p: QPainter, center: float, bottom: float, width: float, openness: float) -> None:
        half = width / 2
        body_top = self._body_top()
        lid_height = width * 0.33
        angle = min(openness, 1.2) * math.radians(112)
        lid_gap = lid_height * 0.72 * math.sin(min(angle, math.pi / 2))
        lid_bottom = body_top - lid_gap
        face = lid_height * math.cos(angle)
        outline = QPen(QColor("#2B1006"), width * 0.016)
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        # The inside of the chest shows behind the lifting lid.
        if openness > 0.02:
            p.setPen(outline)
            mouth = QLinearGradient(0, lid_bottom, 0, body_top)
            mouth.setColorAt(0, QColor("#47210E"))
            mouth.setColorAt(1, QColor(GOLD))
            p.setBrush(QBrush(mouth))
            p.drawRect(QRectF(center - half * 0.96, lid_bottom, width * 0.96, body_top - lid_bottom + width * 0.02))

        # Lid.
        p.setPen(outline)
        if face >= 0:
            lid = QPainterPath()
            lid.moveTo(center - half, lid_bottom)
            lid.lineTo(center - half, lid_bottom - face * 0.55)
            lid.cubicTo(
                center - half, lid_bottom - face * 1.05,
                center + half, lid_bottom - face * 1.05,
                center + half, lid_bottom - face * 0.55,
            )
            lid.lineTo(center + half, lid_bottom)
            lid.closeSubpath()
            shade = QLinearGradient(0, lid_bottom - face, 0, lid_bottom)
            shade.setColorAt(0, QColor("#F08A55"))
            shade.setColorAt(1, QColor("#C4471F"))
            p.setBrush(QBrush(shade))
            p.drawPath(lid)
            self._paint_bands(p, center, width, lid_bottom - face * 0.82, lid_bottom)
        else:
            back = QRectF(center - half, lid_bottom + face, width, -face)
            p.setBrush(QColor("#6B2C14"))
            p.drawRoundedRect(back, width * 0.03, width * 0.03)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(GOLD))
            p.drawRect(QRectF(center - half, lid_bottom - width * 0.012, width, width * 0.024))

        # Body with plank lines and metal corners.
        p.setPen(outline)
        planks = QLinearGradient(0, body_top, 0, bottom)
        planks.setColorAt(0, QColor(EMBER))
        planks.setColorAt(1, QColor("#9E3414"))
        p.setBrush(QBrush(planks))
        p.drawRoundedRect(QRectF(center - half, body_top, width, bottom - body_top), width * 0.02, width * 0.02)
        p.setPen(QPen(QColor(70, 22, 8, 170), width * 0.008))
        for step in (0.34, 0.67):
            y = body_top + (bottom - body_top) * step
            p.drawLine(QPointF(center - half * 0.96, y), QPointF(center + half * 0.96, y))
        self._paint_bands(p, center, width, body_top, bottom)

        # Rim and clasp ride with the body, so the clasp stays put as the lid lifts.
        p.setPen(outline)
        p.setBrush(QColor(GOLD))
        p.drawRect(QRectF(center - half, body_top - width * 0.012, width, width * 0.042))
        clasp = QRectF(center - width * 0.075, body_top - width * 0.05, width * 0.15, width * 0.17)
        p.setBrush(QColor("#FFD978"))
        p.drawRoundedRect(clasp, width * 0.024, width * 0.024)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2B1006"))
        p.drawEllipse(clasp.center(), width * 0.021, width * 0.021)
        p.drawRect(QRectF(clasp.center().x() - width * 0.008, clasp.center().y(), width * 0.016, width * 0.05))

    def _paint_bands(self, p: QPainter, center: float, width: float, top: float, bottom: float) -> None:
        if bottom - top < 1:
            return
        p.setPen(QPen(QColor("#2B1006"), width * 0.008))
        p.setBrush(QColor(GOLD))
        for x in (-0.36, 0.36):
            p.drawRect(QRectF(center + (x - 0.04) * width, top, width * 0.08, bottom - top))

    def _paint_coins(self, p: QPainter) -> None:
        for coin in self.coins:
            p.save()
            p.setOpacity(min(1.0, coin.life * 2.2))
            p.translate(coin.x, coin.y)
            flat = abs(math.cos(coin.spin))
            p.setPen(QPen(QColor("#8F5B09"), max(1.0, coin.size * 0.16)))
            p.setBrush(QColor("#FFD978"))
            p.drawEllipse(QPointF(0, 0), max(1.5, coin.size * flat), coin.size)
            p.restore()

    def _paint_count(self, p: QPainter) -> None:
        text = f"{self.count:,}"
        size = min(self.height() * 0.13, self.width() * 0.11) * (1 + 0.14 * self.pop)
        p.setPen(QColor(LILAC))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        p.drawText(
            QRectF(self.width() * 0.07, self.height() * 0.28, self.width() * 0.43, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "ALL-TIME CHESTS",
        )
        count_rect = QRectF(self.width() * 0.065, self.height() * 0.32, self.width() * 0.40, self.height() * 0.23)
        count_font = display_font(size)
        while size > 20 and QFontMetricsF(count_font).horizontalAdvance(text) > count_rect.width():
            size -= 1
            count_font = display_font(size)
        p.setPen(QColor(GOLD))
        p.setFont(count_font)
        p.drawText(count_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(QColor(LILAC))
        caption = QFont("Segoe UI")
        caption.setPixelSize(14)
        p.setFont(caption)
        text = "Checking this chest..." if self.searching else ("chest opened" if self.count == 1 else "chests opened")
        p.drawText(
            QRectF(self.width() * 0.07, self.height() * 0.55, self.width() * 0.43, 30),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text,
        )


class TallyMarks(QWidget):
    """Today's chests as groups of five hand-scratched marks."""

    GROUP_WIDTH = 46
    ROW_HEIGHT = 30

    def __init__(self):
        super().__init__()
        self.count = 0
        self.setMinimumHeight(self.ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.ROW_HEIGHT * 2)

    def set_count(self, count: int) -> None:
        self.count = count
        self.setAccessibleName(f"{count} chests today")
        self.setVisible(count > 0)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        per_row = max(1, self.width() // self.GROUP_WIDTH)
        capacity = per_row * 2 * 5
        shown = min(self.count, capacity)
        rng = random.Random(11)  # the same marks every paint, so they don't jitter
        p.setPen(QPen(QColor(GOLD), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for index in range(shown):
            group, mark = divmod(index, 5)
            row, column = divmod(group, per_row)
            x, top = column * self.GROUP_WIDTH + 3, row * self.ROW_HEIGHT + 4
            if mark < 4:
                lean = rng.uniform(-1.4, 1.4)
                px = x + mark * 8
                p.drawLine(QPointF(px + lean, top), QPointF(px - lean, top + 21))
            else:
                p.drawLine(QPointF(x - 4, top + 17), QPointF(x + 28, top + 4))


class MatchMeter(QWidget):
    """Shows the live prompt match. Drag the marker, or use arrow keys, to set the required match."""

    threshold_changed = Signal(float)
    MIN_THRESHOLD = 0.5
    MAX_THRESHOLD = 0.99

    def __init__(self, threshold: float):
        super().__init__()
        self.score = 0.0
        self.threshold = min(self.MAX_THRESHOLD, max(self.MIN_THRESHOLD, threshold))
        self.setFixedHeight(48)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip(
            "Required match: 0.50 is more permissive and may cause false matches; "
            "0.99 is stricter and may miss prompts. Drag to change it."
        )
        self.setAccessibleName("Required prompt match")
        self.setAccessibleDescription(
            "The range goes from 0.50, more permissive, to 0.99, stricter."
        )

    def set_score(self, score: float) -> None:
        self.score = max(0.0, min(1.0, score))
        self.update()

    def _track(self) -> QRectF:
        return QRectF(8, 8, self.width() - 16, 9)

    @classmethod
    def _threshold_at_fraction(cls, fraction: float) -> float:
        fraction = min(1.0, max(0.0, fraction))
        value = cls.MIN_THRESHOLD + fraction * (cls.MAX_THRESHOLD - cls.MIN_THRESHOLD)
        return round(value, 2)

    @classmethod
    def _fraction_at_threshold(cls, threshold: float) -> float:
        return (threshold - cls.MIN_THRESHOLD) / (cls.MAX_THRESHOLD - cls.MIN_THRESHOLD)

    def _set_threshold_at(self, x: float) -> None:
        track = self._track()
        fraction = (x - track.left()) / track.width()
        self.threshold = self._threshold_at_fraction(fraction)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._set_threshold_at(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._set_threshold_at(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.threshold_changed.emit(self.threshold)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        step = {Qt.Key.Key_Left: -0.01, Qt.Key.Key_Right: 0.01}.get(event.key())
        if step is None:
            super().keyPressEvent(event)
            return
        self.threshold = round(
            min(self.MAX_THRESHOLD, max(self.MIN_THRESHOLD, self.threshold + step)), 2
        )
        self.threshold_changed.emit(self.threshold)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        gradient = QLinearGradient(track.topLeft(), track.topRight())
        gradient.setColorAt(0.0, QColor(EMBER))
        gradient.setColorAt(0.5, QColor(GOLD))
        gradient.setColorAt(1.0, QColor(MINT))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gradient))
        p.drawRoundedRect(track, 4, 4)
        if self.score > 0:
            score_fraction = self._fraction_at_threshold(self.score)
            score_fraction = min(1.0, max(0.0, score_fraction))
            fill = QRectF(track.left(), track.top(), track.width() * score_fraction, track.height())
            score_color = QColor(MINT if self.score >= self.threshold else LILAC)
            score_color.setAlpha(110)
            p.setBrush(score_color)
            p.drawRoundedRect(fill, 4, 4)
        x = track.left() + track.width() * self._fraction_at_threshold(self.threshold)
        p.setPen(QPen(QColor(BONE if not self.hasFocus() else GOLD), 2))
        p.setBrush(QColor(GOLD))
        marker = QPainterPath(QPointF(x, 24))
        marker.lineTo(x - 6, 31)
        marker.lineTo(x + 6, 31)
        marker.closeSubpath()
        p.drawLine(QPointF(x, 3), QPointF(x, 26))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(marker)

        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor(EMBER))
        p.drawText(
            QRectF(track.left(), 32, track.width() / 2, 15),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{self.MIN_THRESHOLD:.2f}  More permissive",
        )
        p.setPen(QColor(MINT))
        p.drawText(
            QRectF(track.left() + track.width() / 2, 32, track.width() / 2, 15),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"Stricter  {self.MAX_THRESHOLD:.2f}",
        )


class LeaderRow(QWidget):
    """One leaderboard line: a bar showing the player's share of the top score."""

    def __init__(self, rank: int, name: str, count: int, top: int, is_you: bool):
        super().__init__()
        self.rank, self.name, self.count, self.top, self.is_you = rank, name, count, top, is_you
        self.setFixedHeight(46)
        self.setAccessibleName(f"{rank}. {name}: {count} chests")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(0, 3, 0, -3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(DUSK))
        p.drawRoundedRect(area, 5, 5)
        share = max(0.0, self.count / self.top) if self.top > 0 else 0.0
        bar = QColor(GOLD if self.rank == 1 else LILAC)
        bar.setAlphaF(0.32 if self.rank == 1 else 0.17)
        p.setBrush(bar)
        p.drawRoundedRect(QRectF(area.left(), area.top(), area.width() * share, area.height()), 5, 5)

        p.setFont(display_font(21))
        p.setPen(QColor(BONE if self.rank == 1 else LILAC))
        p.drawText(area.adjusted(14, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, str(self.rank))
        name = QFont("Segoe UI")
        name.setPixelSize(15)
        name.setWeight(QFont.Weight.DemiBold if self.is_you else QFont.Weight.Normal)
        p.setFont(name)
        p.setPen(QColor(BONE))
        label = f"{self.name} (you)" if self.is_you else self.name
        p.drawText(area.adjusted(44, 0, -90, 0), Qt.AlignmentFlag.AlignVCenter, label)
        p.setFont(display_font(22))
        p.setPen(QColor(BONE))
        p.drawText(
            area.adjusted(0, 0, -14, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{self.count:,}",
        )


class StepBadge(QWidget):
    """A setup step's number, which becomes a check mark when the step is done."""

    def __init__(self, number: int):
        super().__init__()
        self.number = number
        self.done = False
        self.active = False
        self.setFixedSize(30, 30)

    def set_status(self, done: bool, active: bool) -> None:
        self.done, self.active = done, active
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = QRectF(2, 2, 26, 26)
        if self.done:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(MINT))
            p.drawEllipse(circle)
            pen = QPen(QColor(NIGHT), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(9, 15), QPointF(13.5, 19.5), QPointF(21, 11)])
            return
        p.setPen(QPen(QColor(GOLD if self.active else LINE), 2))
        p.setBrush(QColor(NIGHT))
        p.drawEllipse(circle)
        p.setPen(QColor(GOLD if self.active else LILAC))
        p.setFont(display_font(17))
        p.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(self.number))
