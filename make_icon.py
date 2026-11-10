"""Draws the app icon (a simple treasure chest) into assets/. Run once, or to change it."""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen

ASSETS = Path(__file__).resolve().parent / "assets"


def draw(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#101112"))
    p.drawRoundedRect(QRectF(0, 0, 64, 64), 14, 14)

    outline = QPen(QColor("#2B1006"), 2.2)
    p.setPen(outline)
    p.setBrush(QColor("#F08A55"))
    p.drawRoundedRect(QRectF(10, 13, 44, 20), 9, 9)
    p.setBrush(QColor("#E8663C"))
    p.drawRoundedRect(QRectF(10, 29, 44, 23), 2, 2)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#F4B42A"))
    p.drawRect(QRectF(16, 14, 5, 37))
    p.drawRect(QRectF(43, 14, 5, 37))
    p.drawRect(QRectF(11, 28, 42, 3.2))
    p.setBrush(QColor("#FFD978"))
    p.setPen(QPen(QColor("#2B1006"), 1.8))
    p.drawRoundedRect(QRectF(27, 24, 10, 13), 2.5, 2.5)
    p.end()
    return image


def main() -> None:
    app = QGuiApplication([])
    ASSETS.mkdir(exist_ok=True)
    draw(256).save(str(ASSETS / "icon.png"))
    draw(256).save(str(ASSETS / "icon.ico"))
    del app


if __name__ == "__main__":
    main()
