"""The palette stays readable: black, dark grey, gold and off-white must keep enough contrast."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import widgets


def luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("text,background", [
    ("BONE", "NIGHT"), ("BONE", "DUSK"), ("BONE", "RAISED"),
    ("ASH", "NIGHT"), ("ASH", "DUSK"),
    ("GOLD", "NIGHT"), ("GOLD", "DUSK"),
    ("NIGHT", "GOLD"), ("NIGHT", "GOLD_LIGHT"),
    ("MINT", "DUSK"), ("EMBER", "DUSK"), ("EMBER", "NIGHT"),
])
def test_text_colors_are_readable_on_their_backgrounds(text, background):
    assert contrast(getattr(widgets, text), getattr(widgets, background)) >= 4.5


def test_the_surfaces_are_black_and_dark_grey():
    for name in ("NIGHT", "DUSK", "RAISED"):
        assert luminance(getattr(widgets, name)) < 0.02
