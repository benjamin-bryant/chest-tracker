"""Spots opened chests and delivers them to the Discord channel.

It only takes screenshots and checks whether keys are held. It never touches
Fortnite's memory or files, and screenshots are never saved or sent anywhere
(prompt setup saves only the small image you select, on your own PC).
"""

import ctypes
import json
import logging
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

import cv2
import mss
import numpy as np
import requests

from detection import CANCELLED, OPENED, PENDING, ChestDetector, PromptMatcher, scene_similarity, scene_thumb
from gamepad import is_pad_name, shared_gamepad
from protocol import format_delta, is_webhook_url
from settings import read_json
from wheel import WheelListener

log = logging.getLogger("chest-tracker")

VK_F8, VK_F9, VK_F10, VK_BACK = 0x77, 0x78, 0x79, 0x08
LOOKED_AWAY_BELOW = 0.5  # scene similarity under this means the aim left the chest
NAMED_KEYS = {
    "BACKSPACE": VK_BACK,
    "SPACE": 0x20, "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12, "TAB": 0x09,
    "MOUSE1": 0x01, "MOUSE2": 0x02, "MOUSE3": 0x04, "MOUSE4": 0x05, "MOUSE5": 0x06,
    **{f"F{n}": 0x6F + n for n in range(1, 13)},
}
WHEEL_KEYS = ("WHEELUP", "WHEELDOWN")

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]


def key_code(name: str) -> int:
    name = name.strip().upper()
    if name in NAMED_KEYS:
        return NAMED_KEYS[name]
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name)
    raise ValueError(f'Unknown key "{name}"')


def key_down(code: int) -> bool:
    # 0x8000 = held now, 0x0001 = pressed since the last check (catches quick taps).
    return bool(user32.GetAsyncKeyState(code) & 0x8001)


_wheel: WheelListener | None = None


def _shared_wheel() -> WheelListener:
    # Windows gives raw input to one window per process, so there can only be one listener.
    global _wheel
    if _wheel is None:
        listener = WheelListener()
        listener.start()  # raises if Windows refuses; then the next try starts fresh
        _wheel = listener
    return _wheel


def interact_check(names: list[str]) -> Callable[[], bool]:
    """Build a function that says whether any interact key, wheel direction or controller button was just used."""
    checks = []
    for name in names:
        upper = name.strip().upper()
        if upper == "WHEELUP":
            checks.append(_shared_wheel().scrolled_up)
        elif upper == "WHEELDOWN":
            checks.append(_shared_wheel().scrolled_down)
        elif upper.startswith("PAD_"):
            if not is_pad_name(upper):
                raise ValueError(f'Unknown controller button "{name}"')
            checks.append(lambda button=upper: shared_gamepad().pressed(button))
        else:
            code = key_code(name)
            checks.append(lambda code=code: key_down(code))
    return lambda: any([check() for check in checks])


class KeyEdge:
    """True once per press of a hotkey."""

    def __init__(self, code: int):
        self.code = code
        self.held = False

    def pressed(self) -> bool:
        down = bool(user32.GetAsyncKeyState(self.code) & 0x8000)
        fired = down and not self.held
        self.held = down
        return fired


def fortnite_focused() -> bool:
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(user32.GetForegroundWindow(), title, 256)
    return title.value.strip().lower() == "fortnite"


def foreground_window_bounds() -> tuple[int, int, int, int]:
    """Return the focused window's (left, top, right, bottom) screen coordinates."""
    rect = wintypes.RECT()
    user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def best_monitor(window: tuple[int, int, int, int], monitors: list[dict]) -> dict:
    """The monitor showing the most of a (left, top, right, bottom) window."""

    def overlap(monitor: dict) -> int:
        width = min(window[2], monitor["left"] + monitor["width"]) - max(window[0], monitor["left"])
        height = min(window[3], monitor["top"] + monitor["height"]) - max(window[1], monitor["top"])
        return max(0, width) * max(0, height)

    return max(monitors, key=overlap)


def foreground_monitor(sct) -> dict:
    rect = wintypes.RECT()
    user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(rect))
    return best_monitor((rect.left, rect.top, rect.right, rect.bottom), sct.monitors[1:])


def search_region(monitor: dict, area: list[float]) -> dict:
    left, top, right, bottom = area
    return {
        "left": monitor["left"] + int(monitor["width"] * left),
        "top": monitor["top"] + int(monitor["height"] * top),
        "width": int(monitor["width"] * (right - left)),
        "height": int(monitor["height"] * (bottom - top)),
    }


def selection_too_big(width: int, height: int, screen_height: int) -> bool:
    """The prompt word is small. A big box would capture scenery that changes every time."""
    scale = screen_height / 1080
    return width > 200 * scale or height > 60 * scale


def capture_focused_screen() -> np.ndarray:
    """A BGRA image of the monitor the focused window is on."""
    with mss.MSS() as sct:
        return np.array(sct.grab(foreground_monitor(sct)))


class ScreenCapture:
    """Grabs the part of Fortnite's monitor that the prompt can appear in."""

    def __init__(self, area: list[float]):
        self.area = area
        self.sct = mss.MSS()
        self.region = None

    def refresh(self) -> None:
        self.region = search_region(foreground_monitor(self.sct), self.area)

    def grab(self) -> np.ndarray:
        return cv2.cvtColor(np.asarray(self.sct.grab(self.region)), cv2.COLOR_BGRA2GRAY)

    def close(self) -> None:
        self.sct.close()


class Sender:
    """Delivers chest counts to the webhook, keeping them on disk until they arrive."""

    def __init__(self, webhook_url: str, player: str, pending_path: Path, on_result=None):
        self.url = webhook_url
        self.player = player
        self.path = pending_path
        self.on_result = on_result or (lambda ok, message: None)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.pending = 0
        saved = read_json(self.path)
        if saved:
            try:
                self.pending = int(saved["pending"])
            except (KeyError, TypeError, ValueError):
                self.pending = 0

    def add(self, amount: int) -> None:
        with self.lock:
            self.pending += amount
            self._save()
        self.wake.set()

    def _save(self) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"pending": self.pending}))
        temp.replace(self.path)

    def send_once(self) -> float:
        """Try to deliver what is pending. Returns seconds to wait before retrying (0 = done)."""
        with self.lock:
            amount = self.pending
        if amount == 0:
            return 0.0
        if not self.url or not self.player:
            return 15.0
        try:
            response = requests.post(
                self.url,
                params={"wait": "true"},
                json={"username": self.player, "content": format_delta(amount)},
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            log.warning("Can't reach Discord: %s", error.__class__.__name__)
            self.on_result(False, "Can't reach Discord. Will keep trying.")
            return 15.0
        if response.status_code == 429:
            try:
                wait = float(response.json().get("retry_after", 5))
            except (ValueError, AttributeError):
                wait = 5.0
            return wait + 0.1
        if not response.ok:
            log.warning("Discord refused the message: HTTP %s", response.status_code)
            self.on_result(False, self._refusal_message(response))
            return 60.0
        with self.lock:
            self.pending -= amount
            self._save()
        self.on_result(True, "")
        return 0.0

    @staticmethod
    def _refusal_message(response) -> str:
        if response.status_code == 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            if isinstance(body, dict) and "username" in body:
                return "Discord rejected your player name. Pick another one in Setup."
        if response.status_code in (401, 403, 404):
            return "Discord doesn't accept this webhook any more. Copy a fresh link in Setup."
        return f"Discord refused the post ({response.status_code}). Check the webhook."

    def run_forever(self) -> None:
        while True:
            self.wake.wait()
            self.wake.clear()
            delay = self.send_once()
            while delay:
                time.sleep(delay)
                delay = self.send_once()
            # Space messages out so a burst of chests can't hit Discord's rate limit.
            time.sleep(1)


def check_webhook(url: str) -> tuple[bool, str]:
    """Check a webhook exists without posting anything in the channel."""
    if not is_webhook_url(url):
        return False, "That doesn't look like a Discord webhook URL."
    try:
        response = requests.get(url, timeout=10, allow_redirects=False)
    except requests.RequestException:
        return False, "Couldn't reach Discord. Check your internet connection."
    if response.ok:
        return True, f'Connected to webhook "{response.json().get("name", "webhook")}".'
    return False, "Discord doesn't recognize this webhook. Copy the URL again."


class WatchEngine(threading.Thread):
    """The detection loop. Reports through callbacks, which run on this thread."""

    BLACK_SECONDS = 5

    def __init__(
        self,
        matcher: PromptMatcher,
        interact_used: Callable[[], bool],
        area: list[float],
        on_chest: Callable[[int, str], None],
        on_state: Callable[[str], None],
        on_score: Callable[[float], None],
        on_attempt: Callable[[str], None] = lambda event: None,
        focused: Callable[[], bool] = fortnite_focused,
        capture_factory=ScreenCapture,
        remove_hotkey: int = VK_F10,
        pause_hotkey: int | None = None,
        on_pause: Callable[[], None] = lambda: None,
    ):
        super().__init__(daemon=True)
        self.matcher = matcher
        self.interact_used = interact_used
        self.area = area
        self.on_chest = on_chest
        self.on_state = on_state
        self.on_score = on_score
        self.on_attempt = on_attempt
        self.focused = focused
        self.capture_factory = capture_factory
        self.remove_hotkey = remove_hotkey
        self.pause_hotkey = pause_hotkey
        self.on_pause = on_pause
        self.paused = threading.Event()  # while set, nothing is counted
        self.halt = threading.Event()

    def stop(self) -> None:
        self.halt.set()

    def run(self) -> None:
        capture = self.capture_factory(self.area)
        try:
            self._loop(capture)
        finally:
            capture.close()

    def _loop(self, capture) -> None:
        detector = ChestDetector()
        remove_one = KeyEdge(self.remove_hotkey)
        pause_one = KeyEdge(self.pause_hotkey) if self.pause_hotkey is not None else None
        was_focused = None
        reference = None
        lowest_similarity = 1.0
        attempt_started = 0.0
        last_key_at = 0.0
        last_prompt_at = 0.0
        last_count_at: float | None = None
        last_score = 0.0
        black_since = None
        state = None

        def set_state(new: str) -> None:
            nonlocal state
            if new != state:
                state = new
                self.on_state(new)

        while not self.halt.is_set():
            focused = self.focused()
            # Always sample the key, but only act on a fresh press in Fortnite.
            if remove_one.pressed() and focused:
                if last_count_at is not None:
                    # A quick undo after a count usually means it wasn't a chest: useful when tuning.
                    log.info("Undo pressed %.1fs after the last counted chest", time.monotonic() - last_count_at)
                self.on_chest(-1, "hotkey")
            if pause_one is not None and pause_one.pressed() and focused:
                self.on_pause()  # the window flips the pause state and tells this thread

            if self.paused.is_set():
                if detector.armed:
                    self.on_attempt(CANCELLED)  # paused mid-search: that search is not counted
                detector.reset()
                reference = None
                black_since = None
                was_focused = None  # look at the game afresh once resumed
                self.interact_used()  # presses while paused must not count later
                set_state("off")
                self.halt.wait(0.03)  # short, so a quick tap on the resume key is not missed
                continue

            if focused != was_focused:
                was_focused = focused
                if detector.armed:
                    self.on_attempt(CANCELLED)  # left the game mid-search
                detector.reset()
                reference = None
                black_since = None
                if focused:
                    capture.refresh()
            if not focused:
                set_state("waiting")
                self.interact_used()  # scrolling outside the game must not count later
                self.halt.wait(0.25)
                continue

            try:
                frame = capture.grab()
            except Exception as error:  # e.g. the screen is locked or a Windows prompt is up
                log.warning("Screen capture failed: %s", error)
                if detector.armed:
                    self.on_attempt(CANCELLED)
                detector.reset()
                reference = None
                self.halt.wait(1)
                continue

            now = time.monotonic()
            if frame.std() < 1:
                black_since = black_since if black_since is not None else now
            else:
                black_since = None
            blank = black_since is not None and now - black_since >= self.BLACK_SECONDS
            set_state("black" if blank else "watching")

            score = self.matcher.best_score(frame)
            seen = score >= self.matcher.threshold
            if seen:
                last_prompt_at = now
            thumb = scene_thumb(frame)
            similarity = scene_similarity(reference, thumb) if reference is not None else 1.0
            lowest_similarity = min(lowest_similarity, similarity)
            attempt_id = detector.attempt_id
            pressed = self.interact_used()
            if pressed:
                last_key_at = now
            event = detector.update(
                now, seen, pressed, looked_away=similarity < LOOKED_AWAY_BELOW,
            )
            if event in (OPENED, CANCELLED):
                # Real numbers from real play, for telling chests from look-alikes later.
                log.info(
                    "Attempt %s: %.2fs after pressing, key used for %.2fs, prompt gone %.2fs, "
                    "lowest aim match %.2f, final match %.2f",
                    event, now - attempt_started, max(0.0, last_key_at - attempt_started),
                    max(0.0, now - last_prompt_at), lowest_similarity, score,
                )
                reference = None
            if detector.armed and detector.attempt_id != attempt_id:
                reference = thumb
                lowest_similarity = 1.0
                attempt_started = now
            if event == OPENED:
                last_count_at = now
                self.on_chest(1, "detected")
            elif event in (PENDING, CANCELLED):
                self.on_attempt(event)
            if now - last_score >= 0.5:
                self.on_score(score)
                last_score = now
            self.halt.wait(0.05)
