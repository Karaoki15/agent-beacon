"""Small process identity probes; never read process command lines."""

from __future__ import annotations

import os
from pathlib import Path


def provider_process_names(provider: str) -> set[str]:
    if provider == "antigravity":
        return {"antigravity", "antigravity.exe", "agy", "agy.exe"}
    return {provider, provider + ".exe"}


def process_has_exited(pid: int) -> bool:
    """Confirm exit separately from a failed identity lookup."""
    if os.name != "nt":
        return not Path(f"/proc/{pid}").exists()
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87  # No such PID; access denied is inconclusive.
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value != 259
    finally:
        kernel32.CloseHandle(handle)


def process_info(pid: int) -> tuple[str, str, int] | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                                 ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                                 ctypes.POINTER(wintypes.FILETIME)]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            process = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not process:
                return None
            try:
                path_buffer = ctypes.create_unicode_buffer(32768)
                path_length = wintypes.DWORD(len(path_buffer))
                if not kernel32.QueryFullProcessImageNameW(process, 0, path_buffer, ctypes.byref(path_length)):
                    return None
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel_time = wintypes.FILETIME()
                user_time = wintypes.FILETIME()
                if not kernel32.GetProcessTimes(process, ctypes.byref(creation), ctypes.byref(exit_time),
                                                ctypes.byref(kernel_time), ctypes.byref(user_time)):
                    return None
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return Path(path_buffer.value).name.lower(), str(ticks), _windows_parent_pid(pid)
            finally:
                kernel32.CloseHandle(process)
        except (AttributeError, OSError, ValueError):
            return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        comm_end = stat.rfind(")")
        if comm_end < 0:
            return None
        name = stat[stat.find("(") + 1:comm_end].lower()
        fields = stat[comm_end + 2:].split()
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        return name, f"{boot_id}:{fields[19]}", int(fields[1])
    except (OSError, IndexError, ValueError, UnicodeError):
        return None


def _windows_parent_pid(pid: int) -> int:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return 0
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return 0
        while True:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return 0
    finally:
        kernel32.CloseHandle(snapshot)


def find_agent_process(provider: str, first_parent_pid: int) -> tuple[int, str] | None:
    """Walk parent ids and accept only a process whose image name is the agent."""
    expected = provider_process_names(provider)
    pid = first_parent_pid
    visited: set[int] = set()
    for _ in range(12):
        if pid <= 0 or pid in visited:
            break
        visited.add(pid)
        info = process_info(pid)
        if info is None:
            break
        name, created, parent_pid = info
        if name in expected:
            return pid, created
        pid = parent_pid
    return None
