"""Local Windows process and clipboard primitives for the dev launcher."""

import ctypes
from ctypes import wintypes as w

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
user = ctypes.WinDLL("user32", use_last_error=True)


def _api(library, name, result, *args):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = args
    return function


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", w.DWORD), ("min_working_set", ctypes.c_size_t), ("max_working_set", ctypes.c_size_t),
        ("active_process_limit", w.DWORD), ("affinity", ctypes.c_size_t),
        ("priority", w.DWORD), ("scheduling", w.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io", ctypes.c_uint64 * 6),
        ("memory", ctypes.c_size_t * 4),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_int64 * 4), ("page_faults", w.DWORD), ("total", w.DWORD),
        ("active", w.DWORD), ("terminated", w.DWORD),
    ]


class ParentProcess:
    """Track the PowerShell process even when its PID is later reused."""

    def __init__(self, pid):
        open_process = _api(kernel, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
        self.handle = open_process(0x00100000, False, pid)  # SYNCHRONIZE
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def running(self):
        wait = _api(kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
        status = wait(self.handle, 0)
        if status == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return status == 0x102  # WAIT_TIMEOUT

    def close(self):
        close = _api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        close(self.handle)


class ProcessTree:
    """Include the supervisor so subprocesses join the job before they execute."""

    def __init__(self):
        create = _api(kernel, "CreateJobObjectW", w.HANDLE, ctypes.c_void_p, w.LPCWSTR)
        current = _api(kernel, "GetCurrentProcess", w.HANDLE)
        configure = _api(kernel, "SetInformationJobObject", w.BOOL, w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
        assign = _api(kernel, "AssignProcessToJobObject", w.BOOL, w.HANDLE, w.HANDLE)
        close = _api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self.handle = create(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not configure(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not assign(self.handle, current()):
            error = ctypes.WinError(ctypes.get_last_error())
            close(self.handle)
            raise error
        # The non-inheritable handle is closed by Windows when this supervisor exits.

    def has_children(self):
        query = _api(kernel, "QueryInformationJobObject", w.BOOL, w.HANDLE, ctypes.c_int,
                     ctypes.c_void_p, w.DWORD, ctypes.c_void_p)
        info = _Accounting()
        if not query(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.active > 1


class TemporaryClipboard:
    """Explicit token handoff with Windows clipboard-history exclusion."""

    def __init__(self):
        create = _api(user, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p)
        self.window = create(0, "STATIC", "RepoSherlock token", 0, 0, 0, 0, 0, w.HWND(-3), None, None, None)
        self.sequence = None
        if not self.window:
            raise ctypes.WinError(ctypes.get_last_error())

    def copy(self, token):
        register = _api(user, "RegisterClipboardFormatW", w.UINT, w.LPCWSTR)
        exclude = register("ExcludeClipboardContentFromMonitorProcessing")
        open_clipboard = _api(user, "OpenClipboard", w.BOOL, w.HWND)
        close = _api(user, "CloseClipboard", w.BOOL)
        empty = _api(user, "EmptyClipboard", w.BOOL)
        if not exclude or not open_clipboard(self.window):
            return False
        try:
            if not empty():
                return False
            if not self._put(exclude, bytes(4)) or not self._put(13, (token + "\0").encode("utf-16-le")):
                empty()
                return False
            return True
        finally:
            sequence = _api(user, "GetClipboardSequenceNumber", w.DWORD)
            self.sequence = sequence()
            close()

    @staticmethod
    def _put(format_id, contents):
        allocate = _api(kernel, "GlobalAlloc", w.HGLOBAL, w.UINT, ctypes.c_size_t)
        lock = _api(kernel, "GlobalLock", ctypes.c_void_p, w.HGLOBAL)
        unlock = _api(kernel, "GlobalUnlock", w.BOOL, w.HGLOBAL)
        free = _api(kernel, "GlobalFree", w.HGLOBAL, w.HGLOBAL)
        put = _api(user, "SetClipboardData", w.HANDLE, w.UINT, w.HANDLE)
        storage = allocate(2, len(contents))  # GMEM_MOVEABLE
        if not storage:
            return False
        pointer = lock(storage)
        if pointer:
            ctypes.memmove(pointer, contents, len(contents))
            unlock(storage)
            if put(format_id, storage):
                return True  # The clipboard now owns the allocation.
        free(storage)
        return False

    def clear(self):
        if self.sequence is None:
            return True
        open_clipboard = _api(user, "OpenClipboard", w.BOOL, w.HWND)
        close = _api(user, "CloseClipboard", w.BOOL)
        sequence = _api(user, "GetClipboardSequenceNumber", w.DWORD)
        owner = _api(user, "GetClipboardOwner", w.HWND)
        empty = _api(user, "EmptyClipboard", w.BOOL)
        if not open_clipboard(self.window):
            return False
        try:
            if sequence() == self.sequence and owner() == self.window and not empty():
                return False
            self.sequence = None
            return True
        finally:
            close()

    def close(self):
        destroy = _api(user, "DestroyWindow", w.BOOL, w.HWND)
        destroy(self.window)
