"""Named-mutex helpers: poller leadership and per-front-end single instance.

A Windows named mutex is released automatically when its owning process exits (the next
waiter gets WAIT_ABANDONED), so leadership survives crashes as well as clean exits.
Mutex ownership is per thread: acquire, hold and release it from the same thread.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = wt.HANDLE
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_k32.WaitForSingleObject.restype = wt.DWORD
_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.ReleaseMutex.argtypes = [wt.HANDLE]
_k32.CloseHandle.argtypes = [wt.HANDLE]

WAIT_OBJECT_0, WAIT_ABANDONED, ERROR_ALREADY_EXISTS = 0x0, 0x80, 183


class LeaderLock:
    def __init__(self, name: str):
        self.name = name
        self._handle = None
        self.held = False

    def try_acquire(self) -> bool:
        if self.held:
            return True
        if self._handle is None:
            self._handle = _k32.CreateMutexW(None, False, self.name)
            if not self._handle:
                raise OSError(ctypes.get_last_error(), f"CreateMutexW({self.name}) failed")
        result = _k32.WaitForSingleObject(self._handle, 0)
        self.held = result in (WAIT_OBJECT_0, WAIT_ABANDONED)
        return self.held

    def release(self) -> None:
        if self.held:
            _k32.ReleaseMutex(self._handle)
            self.held = False
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None


def single_instance(name: str):
    """Return a handle if this is the only instance holding `name`, else None. Keep the handle alive."""
    handle = _k32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _k32.CloseHandle(handle)
        return None
    return handle
