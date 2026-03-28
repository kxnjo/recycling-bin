import csv
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps

# psutil
import psutil
import time
import csv
import os

process = psutil.Process(os.getpid())

# prime the counters once
psutil.cpu_percent(interval=None)
process.cpu_percent(interval=None)

PROFILE_LOG = "profile_log.csv"

def log_cpu_usage(stage=""):
    system_cpu = psutil.cpu_percent(interval=None)
    process_cpu = process.cpu_percent(interval=None)  # can exceed 100 on multi-core systems
    memory_percent = process.memory_percent()

    timestamp = time.time()

    with open("cpu_usage_log.csv", "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, stage, system_cpu, process_cpu, memory_percent])

    print(f"[CPU] {stage} | system={system_cpu:.1f}% | process={process_cpu:.1f}% | mem={memory_percent:.2f}%")


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