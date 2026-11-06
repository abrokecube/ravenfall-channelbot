"""Stops a second copy of the bot from starting on the same machine."""

import ctypes
from ctypes import wintypes

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183


def acquire_single_instance(name: str) -> bool:
    """Returns False if another process on this machine already holds `name`.

    Uses a named Windows mutex rather than a lock file so it covers every checkout
    and worktree, and every login session. Windows frees it when the process dies,
    even from a crash or a kill, so there's no stale lock to clean up. Child
    processes don't inherit it. The handle stays open until the process exits.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, f"Global\\{name}")
    error = ctypes.get_last_error()
    if not handle:
        # Held by a process we can't open, such as one running as administrator
        if error == ERROR_ACCESS_DENIED:
            return False
        raise ctypes.WinError(error)
    return error != ERROR_ALREADY_EXISTS
