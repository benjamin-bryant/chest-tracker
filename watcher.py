"""Spots opened chests and delivers them to the Discord channel.

It only takes screenshots and checks whether keys are held. It never touches
Fortnite's memory or files, and screenshots are never saved or sent anywhere
(prompt setup saves only the small image you select, on your own PC).
"""

import ctypes
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import mss
import numpy as np
import requests

from detection import CANCELLED, OPENED, PENDING, ChestDetector, PromptMatcher, scene_similarity, scene_thumb
from protocol import format_delta
from settings import quarantine_json, read_json_object
from wheel import WheelListener

log = logging.getLogger("chest-tracker")

VK_F8, VK_BACK, VK_F10 = 0x77, 0x08, 0x79
LOOKED_AWAY_BELOW = 0.5  # scene similarity under this means the aim left the chest
NAMED_KEYS = {
    "F10": VK_F10,
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
    if not isinstance(name, str):
        raise ValueError("Key name must be text")
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
        candidate = WheelListener()
        candidate.start()
        _wheel = candidate
    return _wheel


def interact_check(names: list[str]) -> Callable[[], bool]:
    """Build a function that says whether any interact key or wheel direction was just used."""
    checks = []
    for name in names:
        upper = name.strip().upper()
        if upper == "WHEELUP":
            checks.append(_shared_wheel().scrolled_up)
        elif upper == "WHEELDOWN":
            checks.append(_shared_wheel().scrolled_down)
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
        saved = read_json_object(self.path, "pending count")
        if saved is not None:
            amount = saved.get("pending")
            if isinstance(amount, int) and not isinstance(amount, bool):
                self.pending = amount
            else:
                log.warning("Ignoring invalid pending count and starting with an empty queue")
                quarantine_json(self.path, "pending count")

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
        if not is_discord_webhook_url(self.url):
            log.warning("Webhook URL rejected because it is not a valid Discord webhook URL")
            self.on_result(False, "Use a Discord webhook URL from discord.com or discordapp.com.")
            return 0.0
        blocked_name = any(word in self.player.casefold() for word in ("discord", "clyde"))
        try:
            response = requests.post(
                self.url,
                params={"wait": "true"},
                json={
                    "username": "Chest Tracker" if blocked_name else self.player,
                    "content": format_delta(amount, self.player if blocked_name else None),
                },
                timeout=10,
            )
        except requests.RequestException as error:
            log.warning("Can't reach Discord: %s", error.__class__.__name__)
            self.on_result(False, "Can't reach Discord. Will keep trying.")
            return 15.0
        if response.status_code == 429:
            return float(response.json().get("retry_after", 5)) + 0.1
        if not response.ok:
            log.warning("Discord refused the message: HTTP %s", response.status_code)
            self.on_result(False, f"Discord refused the post ({response.status_code}). Check the webhook.")
            return 60.0
        with self.lock:
            self.pending -= amount
            self._save()
        self.on_result(True, "")
        return 0.0

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
    if not is_discord_webhook_url(url):
        return False, "That doesn't look like a Discord webhook URL."
    try:
        response = requests.get(url, timeout=10)
    except requests.RequestException:
        return False, "Couldn't reach Discord. Check your internet connection."
    if response.ok:
        return True, f'Connected to webhook "{response.json().get("name", "webhook")}".'
    return False, "Discord doesn't recognize this webhook. Copy the URL again."


def is_discord_webhook_url(url: str) -> bool:
    """Accept only HTTPS webhook URLs hosted by Discord."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
        allowed_host = (
            host in {"discord.com", "discordapp.com"}
            or host.endswith(".discord.com")
            or host.endswith(".discordapp.com")
        )
        valid_path = re.fullmatch(r"/api/webhooks/\d+/[^/]+/?", parsed.path) is not None
        valid_port = parsed.port in (None, 443)
        return (
            parsed.scheme == "https" and allowed_host and valid_path and valid_port
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
        )
    except ValueError:
        return False


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
        was_focused = None
        reference = None
        lowest_similarity = 1.0
        attempt_started = 0.0
        attempt_prompt_score = 0.0
        attempt_center_luma = 0.0
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
                self.on_chest(-1, "hotkey")

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

            score, _ = self.matcher.best_match(frame)
            thumb = scene_thumb(frame)
            height, width = frame.shape[:2]
            center = frame[int(height * 0.4):int(height * 0.6), int(width * 0.4):int(width * 0.6)]
            center_luma = float(center.mean()) if center.size else 0.0
            similarity = scene_similarity(reference, thumb) if reference is not None else 1.0
            lowest_similarity = min(lowest_similarity, similarity)
            attempt_id = detector.attempt_id
            event = detector.update(
                now, score >= self.matcher.threshold, self.interact_used(),
                looked_away=similarity < LOOKED_AWAY_BELOW,
            )
            if event in (OPENED, CANCELLED):
                # Real numbers from real play, for tuning the rules later.
                log.info(
                    "Attempt %s: %.2fs after pressing, prompt match %.2f, "
                    "lowest aim match %.2f, center brightness %.1f -> %.1f",
                    event, now - attempt_started, attempt_prompt_score,
                    lowest_similarity, attempt_center_luma, center_luma,
                )
                reference = None
            if detector.armed and detector.attempt_id != attempt_id:
                reference = thumb
                lowest_similarity = 1.0
                attempt_started = now
                attempt_prompt_score = score
                attempt_center_luma = center_luma
            if event == OPENED:
                self.on_chest(1, "detected")
            elif event in (PENDING, CANCELLED):
                self.on_attempt(event)
            if now - last_score >= 0.5:
                self.on_score(score)
                last_score = now
            self.halt.wait(0.05)
