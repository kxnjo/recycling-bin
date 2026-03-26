import csv
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps

PROFILE_LOG = "profile_log.csv"


def now() -> float:
    return time.perf_counter()


def _stringify_extra(extra):
    if extra is None:
        return ""
    if isinstance(extra, dict):
        return "; ".join(f"{k}={v}" for k, v in extra.items())
    return str(extra)


def profile_cpu(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        cpu_start = time.process_time()
        wall_start = time.perf_counter()

        result = func(*args, **kwargs)

        cpu_end = time.process_time()
        wall_end = time.perf_counter()

        cpu_ms = (cpu_end - cpu_start) * 1000
        wall_ms = (wall_end - wall_start) * 1000

        print(f"[CPU] {func.__name__}: {cpu_ms:.2f} ms CPU, {wall_ms:.2f} ms wall")
        return result
    return wrapper


def log_profile(stage, duration_ms, extra=None):
    file_exists = os.path.exists(PROFILE_LOG)
    row = [
        time.strftime("%Y-%m-%d %H:%M:%S"),
        threading.current_thread().name,
        stage,
        round(duration_ms, 2),
        _stringify_extra(extra),
    ]

    with open(PROFILE_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "thread", "stage", "duration_ms", "extra"])
        writer.writerow(row)


@contextmanager
def profile_block(name, extra=None):
    start = now()
    try:
        yield
    finally:
        duration_ms = (now() - start) * 1000
        print(f"[PROFILE] {name}: {duration_ms:.2f} ms")
        log_profile(name, duration_ms, extra)