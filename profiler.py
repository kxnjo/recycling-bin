import csv
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps

import psutil


# ============================================================
# PROCESS / CPU SETUP
# ============================================================

# Get information about the current Python process
process = psutil.Process(os.getpid())

# Prime the CPU counters once so the first real reading is not weird / empty
psutil.cpu_percent(interval=None)
process.cpu_percent(interval=None)


# ============================================================
# LOG DIRECTORY + FILE PATHS
# ============================================================

# All logs will be stored inside this folder
LOG_DIR = "logs"

# Individual log files
PROFILE_LOG = os.path.join(LOG_DIR, "profile_log.csv")
CPU_USAGE_LOG = os.path.join(LOG_DIR, "cpu_usage_log.csv")
CPU_FUNC_LOG = os.path.join(LOG_DIR, "cpu_function_log.csv")

# Lock to prevent multiple threads from writing to the same CSV at the same time
log_lock = threading.Lock()


# ============================================================
# DIRECTORY / CSV HELPERS
# ============================================================

def ensure_log_dir():
    """
    Create the logs/ directory if it does not already exist.
    """
    os.makedirs(LOG_DIR, exist_ok=True)


def ensure_csv_header(filepath, header):
    """
    Create the CSV file with a header row if it does not already exist.
    """
    ensure_log_dir()

    if not os.path.exists(filepath):
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)


def append_csv_row(filepath, header, row):
    """
    Safely append one row to a CSV file.
    Uses a lock because your project has multiple threads.
    """
    with log_lock:
        ensure_csv_header(filepath, header)
        with open(filepath, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)


# ============================================================
# BASIC TIMING HELPERS
# ============================================================

def now() -> float:
    """
    High-precision timer for elapsed-time measurements.
    Good for profiling durations.
    """
    return time.perf_counter()


def _stringify_extra(extra):
    """
    Convert the 'extra' field into a CSV-friendly string.
    Example:
        {"attempt": 3, "label": "plastic"}
    becomes:
        "attempt=3; label=plastic"
    """
    if extra is None:
        return ""

    if isinstance(extra, dict):
        return "; ".join(f"{k}={v}" for k, v in extra.items())

    return str(extra)


# ============================================================
# SYSTEM / PROCESS CPU USAGE LOGGER
# ============================================================

def log_cpu_usage(stage="", attempt=None):
    """
    Log current system CPU usage, process CPU usage, and memory usage.

    Useful for resource snapshots at important stages, e.g.
    - before detection
    - after inference
    - during idle monitoring
    """
    system_cpu = psutil.cpu_percent(interval=None)
    process_cpu = process.cpu_percent(interval=None)   # may exceed 100% on multi-core systems
    memory_percent = process.memory_percent()

    row = [
        time.strftime("%Y-%m-%d %H:%M:%S"),       # human-readable timestamp
        threading.current_thread().name,          # which thread logged this
        attempt,                                  # attempt / round number
        stage,                                    # label / stage name
        round(system_cpu, 2),                     # total machine CPU %
        round(process_cpu, 2),                    # this Python process CPU %
        round(memory_percent, 2),                 # this Python process memory %
    ]

    append_csv_row(
        CPU_USAGE_LOG,
        ["timestamp", "thread", "attempt", "stage", "system_cpu_percent", "process_cpu_percent", "memory_percent"],
        row
    )

    print(
        f"[CPU] attempt={attempt} | {stage} | "
        f"system={system_cpu:.1f}% | "
        f"process={process_cpu:.1f}% | "
        f"mem={memory_percent:.2f}%"
    )


# ============================================================
# FUNCTION CPU / WALL TIME DECORATOR
# ============================================================

def profile_cpu(func):
    """
    Decorator that measures:
    - CPU time used by the process during the function
    - Wall-clock time elapsed during the function

    It prints the result and also writes it into:
        logs/cpu_function_log.csv
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        attempt = kwargs.get("attempt", None)

        cpu_start = time.process_time()
        wall_start = time.perf_counter()

        result = func(*args, **kwargs)

        cpu_end = time.process_time()
        wall_end = time.perf_counter()

        cpu_ms = (cpu_end - cpu_start) * 1000
        wall_ms = (wall_end - wall_start) * 1000

        row = [
            time.strftime("%Y-%m-%d %H:%M:%S"),   # human-readable timestamp
            threading.current_thread().name,      # thread name
            attempt,                              # attempt / round number
            func.__name__,                        # function name
            round(cpu_ms, 2),                     # CPU time in ms
            round(wall_ms, 2),                    # real elapsed time in ms
        ]

        append_csv_row(
            CPU_FUNC_LOG,
            ["timestamp", "thread", "attempt", "function", "cpu_ms", "wall_ms"],
            row
        )

        print(f"[CPU] attempt={attempt} | {func.__name__}: {cpu_ms:.2f} ms CPU, {wall_ms:.2f} ms wall")
        return result

    return wrapper


# ============================================================
# GENERIC STAGE / BLOCK PROFILING LOGGER
# ============================================================

def log_profile(stage, duration_ms, extra=None, attempt=None):
    """
    Write one profiling row into logs/profile_log.csv

    Parameters:
    - stage: name of the block / stage
    - duration_ms: elapsed time in milliseconds
    - extra: optional metadata (dict or string)
    - attempt: detection round number
    """
    row = [
        time.strftime("%Y-%m-%d %H:%M:%S"),       # timestamp
        threading.current_thread().name,          # thread
        attempt,                                  # attempt / round number
        stage,                                    # stage name
        round(duration_ms, 2),                    # duration in ms
        _stringify_extra(extra),                  # extra info
    ]

    append_csv_row(
        PROFILE_LOG,
        ["timestamp", "thread", "attempt", "stage", "duration_ms", "extra"],
        row
    )


@contextmanager
def profile_block(name, extra=None, attempt=None):
    """
    Context manager for profiling a code block.

    Example:
        with profile_block("camera_read", extra={"attempt": 1}, attempt=1):
            ret, frame = cap.read()

    This will:
    - measure elapsed wall time
    - print it
    - save it into logs/profile_log.csv
    """
    start = now()
    try:
        yield
    finally:
        duration_ms = (now() - start) * 1000
        print(f"[PROFILE] attempt={attempt} | {name}: {duration_ms:.2f} ms")
        log_profile(name, duration_ms, extra, attempt=attempt)


# ============================================================
# LOG FILE INITIALISATION
# ============================================================

def init_logs():
    """
    Ensure the logs folder and all CSV files exist with headers.
    Call this once at program startup.
    """
    ensure_log_dir()

    ensure_csv_header(
        PROFILE_LOG,
        ["timestamp", "thread", "attempt", "stage", "duration_ms", "extra"]
    )

    ensure_csv_header(
        CPU_USAGE_LOG,
        ["timestamp", "thread", "attempt", "stage", "system_cpu_percent", "process_cpu_percent", "memory_percent"]
    )

    ensure_csv_header(
        CPU_FUNC_LOG,
        ["timestamp", "thread", "attempt", "function", "cpu_ms", "wall_ms"]
    )


def reset_logs():
    """
    Delete old log files and recreate fresh empty ones with headers.
    Useful before each benchmark run so old data does not mix with new data.
    """
    ensure_log_dir()

    for path in [PROFILE_LOG, CPU_USAGE_LOG, CPU_FUNC_LOG]:
        if os.path.exists(path):
            os.remove(path)

    init_logs()