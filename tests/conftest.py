"""Lets the tests run on any OS. The app is Windows-only, but its logic is plain Python."""

import ctypes
import sys


class _Stub:
    """Stands in for a Windows DLL: every function exists and returns 0."""

    def __getattr__(self, name):
        return _Stub()

    def __setattr__(self, name, value):
        pass

    def __call__(self, *args, **kwargs):
        return 0


if sys.platform != "win32":
    ctypes.WinDLL = lambda *args, **kwargs: _Stub()
    ctypes.windll = _Stub()
