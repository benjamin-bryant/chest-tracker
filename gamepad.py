"""Notices Xbox-style controller buttons while Fortnite is in focus.

Uses XInput, which Windows provides for Xbox controllers and for anything that
presents itself as one (many third-party pads, or a PlayStation pad running
through Steam Input or DS4Windows). It only reads button states: it can't send
input or change what the game receives.

A short tap can be over before the tracker looks, so a background thread polls
the pads many times a second and remembers each press until it is collected,
the same way the scroll wheel listener does.
"""

import ctypes
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

BUTTON_MASKS = {
    "PAD_UP": 0x0001, "PAD_DOWN": 0x0002, "PAD_LEFT": 0x0004, "PAD_RIGHT": 0x0008,
    "PAD_START": 0x0010, "PAD_BACK": 0x0020, "PAD_L3": 0x0040, "PAD_R3": 0x0080,
    "PAD_LB": 0x0100, "PAD_RB": 0x0200,
    "PAD_A": 0x1000, "PAD_B": 0x2000, "PAD_X": 0x4000, "PAD_Y": 0x8000,
}
TRIGGERS = ("PAD_LT", "PAD_RT")
PAD_NAMES = tuple(BUTTON_MASKS) + TRIGGERS
PAD_LABELS = {
    "PAD_UP": "D-pad up", "PAD_DOWN": "D-pad down", "PAD_LEFT": "D-pad left", "PAD_RIGHT": "D-pad right",
    "PAD_START": "Menu button", "PAD_BACK": "View button", "PAD_L3": "Left stick click",
    "PAD_R3": "Right stick click", "PAD_LB": "Left bumper", "PAD_RB": "Right bumper",
    "PAD_LT": "Left trigger", "PAD_RT": "Right trigger",
    "PAD_A": "A button", "PAD_B": "B button", "PAD_X": "X button", "PAD_Y": "Y button",
}
TRIGGER_THRESHOLD = 30  # of 255; the same value Microsoft documents as the "pressed" point
MAX_PADS = 4

ERROR_SUCCESS = 0


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


def is_pad_name(name: str) -> bool:
    return name.strip().upper() in PAD_NAMES


def pad_label(name: str) -> str:
    return "Controller " + PAD_LABELS.get(name.strip().upper(), name)


def buttons_down(buttons: int, left_trigger: int, right_trigger: int) -> set[str]:
    """The button names held in one XInput reading."""
    down = {name for name, mask in BUTTON_MASKS.items() if buttons & mask}
    if left_trigger >= TRIGGER_THRESHOLD:
        down.add("PAD_LT")
    if right_trigger >= TRIGGER_THRESHOLD:
        down.add("PAD_RT")
    return down


def _load_xinput() -> Callable[[int], set[str] | None] | None:
    """A function that reads one pad, or None if Windows has no XInput."""
    for dll_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            dll = ctypes.WinDLL(dll_name)
            get_state = dll.XInputGetState
        except (OSError, AttributeError):
            continue
        get_state.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_STATE)]
        get_state.restype = wintypes.DWORD

        def read(index: int, get_state=get_state) -> set[str] | None:
            state = XINPUT_STATE()
            if get_state(index, ctypes.byref(state)) != ERROR_SUCCESS:
                return None  # nothing plugged in at this number
            pad = state.Gamepad
            return buttons_down(pad.wButtons, pad.bLeftTrigger, pad.bRightTrigger)

        return read
    return None


class GamepadListener:
    """Remembers controller presses until they are collected."""

    POLL_SECONDS = 0.008
    RECHECK_SECONDS = 1.0  # empty pad numbers are slow to ask about, so look only now and then

    def __init__(self, read: Callable[[int], set[str] | None] | None = None):
        self._read = read
        self._lock = threading.Lock()
        self._down: set[str] = set()
        self._latched: set[str] = set()
        self._fresh: set[str] = set()
        self._connected = [False] * MAX_PADS
        self._last_checked = [0.0] * MAX_PADS
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if self._read is None:
            self._read = _load_xinput()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gamepad", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def poll_once(self, now: float | None = None) -> None:
        """Reads every pad once. The thread calls this in a loop; tests call it directly."""
        if self._read is None:
            return
        now = time.monotonic() if now is None else now
        down: set[str] = set()
        for index in range(MAX_PADS):
            if not self._connected[index] and now - self._last_checked[index] < self.RECHECK_SECONDS:
                continue
            self._last_checked[index] = now
            try:
                reading = self._read(index)
            except Exception:  # a driver hiccup must never stop the tracker
                reading = None
            self._connected[index] = reading is not None
            if reading:
                down |= reading
        with self._lock:
            newly = down - self._down
            self._latched |= newly
            self._fresh |= newly
            self._down = down

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.POLL_SECONDS)

    def pressed(self, name: str) -> bool:
        """True if the button is held, or was pressed since the last time this was asked."""
        name = name.strip().upper()
        with self._lock:
            hit = name in self._down or name in self._latched
            self._latched.discard(name)
            return hit

    def take_any(self) -> str | None:
        """The name of a button pressed since the last call, for choosing a button in Setup."""
        with self._lock:
            fresh, self._fresh = self._fresh, set()
        for name in PAD_NAMES:
            if name in fresh:
                return name
        return None


_shared: GamepadListener | None = None


def shared_gamepad() -> GamepadListener:
    """One listener for the whole program, started the first time it is needed."""
    global _shared
    if _shared is None:
        _shared = GamepadListener()
    _shared.start()
    return _shared
