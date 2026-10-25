"""Stop a second copy of the app from opening the same vault."""

from __future__ import annotations

import sys

ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """A Windows mutex held for the life of the process."""

    def __init__(self, name: str = "Local\\BogusSecurityAnswers") -> None:
        self.name = name
        self._handle = None
        self._kernel32 = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        self._kernel32 = kernel32
        return True

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(handle)
