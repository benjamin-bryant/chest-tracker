"""A short chime for each counted chest, for people who miss the on-screen animation.

The sound is generated here, so there is no audio file to ship, and it is played with
Windows' built-in sound player, so nothing extra is needed.
"""

import io
import math
import struct
import wave
from collections.abc import Callable

SAMPLE_RATE = 44100
MIN_VOLUME, MAX_VOLUME = 0.1, 1.0
DEFAULT_VOLUME = 0.6
# Two quick notes, a perfect fifth apart: a soft "ding-ding" that is easy to tell from game sounds.
NOTES = ((880.0, 0.0), (1318.5, 0.09))  # (pitch in Hz, start time in seconds)
LENGTH_SECONDS = 0.42


def clamp_volume(value) -> float:
    try:
        volume = float(value)
    except (TypeError, ValueError):
        return DEFAULT_VOLUME
    if math.isnan(volume):
        return DEFAULT_VOLUME
    return min(MAX_VOLUME, max(MIN_VOLUME, volume))


def make_chime(volume: float = DEFAULT_VOLUME) -> bytes:
    """The chime as the bytes of a WAV file (mono, 16-bit)."""
    volume = clamp_volume(volume)
    peak = int(32767 * 0.5 * volume)  # leave headroom: two notes add up
    frames = int(SAMPLE_RATE * LENGTH_SECONDS)
    samples = []
    for i in range(frames):
        t = i / SAMPLE_RATE
        value = 0.0
        for pitch, start in NOTES:
            if t >= start:
                age = t - start
                attack = min(1.0, age / 0.004)  # a few milliseconds of fade-in avoids a click
                value += attack * math.exp(-age * 11.0) * math.sin(2 * math.pi * pitch * age)
        samples.append(int(max(-1.0, min(1.0, value)) * peak))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def _windows_player(data: bytes) -> None:
    import winsound

    # Played from memory and without blocking. The caller keeps `data` alive while it plays.
    winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


class Chime:
    """Plays the chime, remembering the last one built so each chest is quick."""

    def __init__(self, player: Callable[[bytes], None] = _windows_player):
        self._player = player
        self._volume: float | None = None
        self._data = b""

    def play(self, volume: float) -> bool:
        """True if a sound was started. A sound problem must never get in the way of counting."""
        volume = round(clamp_volume(volume), 2)
        if volume != self._volume:
            self._data = make_chime(volume)
            self._volume = volume
        try:
            self._player(self._data)
        except Exception:
            return False
        return True
