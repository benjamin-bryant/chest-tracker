"""Notices scroll wheel movement while another program (Fortnite) is in focus.

Uses Windows raw input, which hands this program its own copy of mouse events.
Unlike a mouse hook it doesn't sit in the path of the mouse, so it can't add
input lag and can't change or block anything the game receives.
"""

import ctypes
import threading
from ctypes import wintypes

WM_INPUT = 0x00FF
HWND_MESSAGE = wintypes.HWND(-3)
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RI_MOUSE_WHEEL = 0x0400

user32 = ctypes.WinDLL("user32", use_last_error=True)


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("_padding", wintypes.USHORT),  # usButtonFlags sits on a 4-byte boundary in Windows
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.SHORT),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]


class WheelListener:
    """Remembers wheel movement until it is collected with scrolled_up/scrolled_down."""

    def __init__(self):
        self._up = threading.Event()
        self._down = threading.Event()
        self._ready = threading.Event()
        self._error: str | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(5)
        if self._error or not self._ready.is_set():
            raise RuntimeError(self._error or "Scroll wheel listener did not start")

    def scrolled_up(self) -> bool:
        return self._collect(self._up)

    def scrolled_down(self) -> bool:
        return self._collect(self._down)

    @staticmethod
    def _collect(event: threading.Event) -> bool:
        if event.is_set():
            event.clear()
            return True
        return False

    def _run(self) -> None:
        # A built-in window class means no window procedure of our own is needed.
        window = user32.CreateWindowExW(
            0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None
        )
        device = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, window)  # generic desktop mouse
        if not window or not user32.RegisterRawInputDevices(
            ctypes.byref(device), 1, ctypes.sizeof(device)
        ):
            self._error = f"Scroll wheel listener failed (Windows error {ctypes.get_last_error()})"
            self._ready.set()
            return
        self._ready.set()

        message = wintypes.MSG()
        raw = RAWINPUT()
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_INPUT:
                size = wintypes.UINT(ctypes.sizeof(raw))
                copied = user32.GetRawInputData(
                    message.lParam, RID_INPUT, ctypes.byref(raw), ctypes.byref(size), header_size
                )
                if (
                    copied != wintypes.UINT(-1).value
                    and raw.header.dwType == RIM_TYPEMOUSE
                    and raw.mouse.usButtonFlags & RI_MOUSE_WHEEL
                ):
                    (self._up if raw.mouse.usButtonData > 0 else self._down).set()
            user32.DispatchMessageW(ctypes.byref(message))
