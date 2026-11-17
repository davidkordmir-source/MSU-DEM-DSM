"""Resource budgets honor container limits as well as physical hardware."""

import os
from pathlib import Path


def physical_memory() -> int:
    """Use the Windows memory API as well as POSIX; no optional package required."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total_phys",
                    "avail_phys",
                    "total_page",
                    "avail_page",
                    "total_virtual",
                    "avail_virtual",
                    "avail_extended",
                )
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total_phys
    elif hasattr(os, "sysconf"):
        try:
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError):
            pass
    return 2 * 1024**3


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def resource_budget() -> dict:
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    quota = _read("/sys/fs/cgroup/cpu.max").split()
    if len(quota) == 2 and quota[0] != "max":
        cores = min(cores, max(1, (int(quota[0]) + int(quota[1]) - 1) // int(quota[1])))
    if not quota:
        q, p = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if q and p and int(q) > 0:
            cores = min(cores, max(1, (int(q) + int(p) - 1) // int(p)))
    memory = physical_memory()
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        limit = _read(path)
        if limit.isdigit():
            memory = min(memory, int(limit))
    # GDAL's cache and warp buffers share the budget with NumPy, previews and the OS.
    cache_mb = max(32, min(4096, memory // 1024**2 // 4))
    warp_mb = max(16, min(1024, memory // 1024**2 // 8))
    return {
        "cpu_threads": cores,
        "memory_mb": memory // 1024**2,
        "gdal_cache_mb": cache_mb,
        "warp_memory_mb": warp_mb,
        "block_size": 1024 if memory >= 2 * 1024**3 else 512,
        "preview_max_size": 2048 if memory >= 2 * 1024**3 else 1024,
    }
