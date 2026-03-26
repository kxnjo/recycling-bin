# profiler.py
import time
import csv
import os
from contextlib import contextmanager

PROFILE_LOG = "profile_log.csv"

def now():
    return time.perf_counter()

@contextmanager
def profile_block(name, extra=None):
    start = time.perf_counter()
    try:
        yield
    finally:
        end = time.perf_counter()
        duration_ms = (end - start) * 1000
        print(f"[PROFILE] {name}: {duration_ms:.2f} ms")
        log_profile(name, duration_ms, extra)

def log_profile(stage, duration_ms, extra=None):
    file_exists = os.path.exists(PROFILE_LOG)
    with open(PROFILE_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["stage", "duration_ms", "extra"])
        writer.writerow([stage, round(duration_ms, 2), extra if extra else ""])