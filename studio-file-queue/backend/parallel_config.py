#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import math, os

DEFAULT_CPU_RATIO = 0.75
MAX_CPU_RATIO = 0.79
MAX_WORKERS = 16  # Avoid process storms on high-core workstations.
RESERVED_LOGICAL_CPUS = 4

def logical_cpu_count() -> int:
    return max(1, int(os.cpu_count() or 1))

def worker_count(requested: int | None = None, ratio: float = DEFAULT_CPU_RATIO) -> int:
    cpus = logical_cpu_count()
    if requested is not None:
        # Never allow a user/configured value to exceed the 79% safety ceiling.
        safe_max = max(1, math.floor(cpus * MAX_CPU_RATIO)) if cpus > 1 else 1
        return max(1, min(int(requested), safe_max, MAX_WORKERS))
    # 75% default. For a 1-core machine one worker is unavoidable.
    return max(1, min(MAX_WORKERS, math.floor(cpus * min(ratio, MAX_CPU_RATIO)))) if cpus > 1 else 1

def limit_process_cpu_affinity(reserved: int = RESERVED_LOGICAL_CPUS) -> int:
    """Best effort hard limit that leaves logical CPUs available to foreground apps."""
    cpus=logical_cpu_count(); usable=max(1,cpus-int(reserved))
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            usable=min(usable,63)
            mask=(1 << usable)-1
            kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel32.GetCurrentProcess.argtypes=[]
            kernel32.GetCurrentProcess.restype=wintypes.HANDLE
            kernel32.SetProcessAffinityMask.argtypes=[wintypes.HANDLE,ctypes.c_size_t]
            kernel32.SetProcessAffinityMask.restype=wintypes.BOOL
            kernel32.SetProcessAffinityMask(kernel32.GetCurrentProcess(),ctypes.c_size_t(mask))
        elif hasattr(os,'sched_setaffinity'):
            os.sched_setaffinity(0,set(range(usable)))
    except Exception:
        pass
    return usable

def lower_process_priority() -> None:
    """Best effort: keep background analysis responsive without starving foreground apps."""
    try:
        if os.name == 'nt':
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(5)
    except Exception:
        pass
