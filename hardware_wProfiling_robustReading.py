import statistics
from threading import Lock
from time import sleep
import time

from gpiozero import AngularServo, Button, DistanceSensor

import config
from profiler import profile_block

# Lock prevents re-triggering while busy.
seq_lock = Lock()

# Assumes config.BIN_CONFIGS contains keys for bins and possibly detect sensor.
sensors = {
    key: DistanceSensor(echo=cfg["echo"], trigger=cfg["trigger"])
    for key, cfg in config.BIN_CONFIGS.items()
}

button1 = Button(config.BTN_1_PIN, pull_up=True)
button2 = Button(config.BTN_2_PIN, pull_up=True)
button3 = Button(config.BTN_3_PIN, pull_up=True)
BUTTON_ANGLES = {button1: 0, button2: 90, button3: 180}

servo = AngularServo(
    config.SERVO_MAIN_PIN,
    min_angle=0,
    max_angle=180,
    min_pulse_width=0.0005,
    max_pulse_width=0.0025,
)

servo2 = AngularServo(
    config.SERVO_LID_PIN,
    min_angle=0,
    max_angle=90,
    min_pulse_width=0.0010,
    max_pulse_width=0.0020,
)

servo.angle = config.MAIN_HOME
servo2.angle = config.FS90_HOME

# 1. Global State to track history
# 'val' stores the last successful cm, 'ts' stores the unix timestamp
BIN_STATE = {
    "a": {"val": 20.0, "ts": 0}, # Default starting values
    "b": {"val": 20.0, "ts": 0},
    "c": {"val": 20.0, "ts": 0}
}

def get_robust_reading(key):
    """
    Attempts to read a sensor. If it fails, it retries. 
    If it still fails, it returns the Last Known Good value.
    """
    cfg = config.BIN_CONFIGS[key]
    readings = []
    
    # --- PHASE 1: The 'Deep Scan' ---
    # We try up to 2 full 'attempts' (opening and closing the sensor)
    for attempt in range(2):
        temp_sensor = None
        try:
            # Re-initialize to clear any 'stuck' GPIO states
            temp_sensor = DistanceSensor(
                echo=cfg["echo"], 
                trigger=cfg["trigger"], 
                max_distance=1.5  # Prevents infinite hangs
            )
            
            time.sleep(0.1) # Stabilization wait
            
            for _ in range(8): # Increase samples for better hit rate
                try:
                    val = temp_sensor.distance * 100
                    if 2.0 <= val <= 100.0:
                        readings.append(val)
                except Exception:
                    pass
                time.sleep(0.05)
            
            if readings:
                break # We got data! Exit the attempt loop.
                
        finally:
            if temp_sensor:
                temp_sensor.close() # Clean up pins immediately
        
        if not readings:
            print(f"  [RETRY] Bin {key.upper()} failed attempt {attempt+1}. Recovering...")
            time.sleep(0.2) # Longer rest before next attempt

    # --- PHASE 2: Fallback Logic ---
    if readings:
        median_val = round(statistics.median(readings), 2)
        # Update our 'Memory'
        BIN_STATE[key]["val"] = median_val
        BIN_STATE[key]["ts"] = time.time()
        return median_val, "LIVE"
    else:
        # RETURN THE FALLBACK
        last_val = BIN_STATE[key]["val"]
        last_ts = BIN_STATE[key]["ts"]
        age = round(time.time() - last_ts, 1) if last_ts > 0 else "∞"
        print(f"  ⚠️ Bin {key.upper()} FAILURE. Using fallback: {last_val}cm ({age}s ago)")
        return last_val, "STALE"

def update_bin_levels():
    print("📊 Measuring bin fill levels...")
    results = {}

    with profile_block("bin_level_scan_total"):
        for key in ["a", "b", "c"]:
            # Standard sequential flow
            val, status = get_robust_reading(key)
            results[key] = val
            
            label = config.BIN_CONFIGS[key]["label"]
            status_icon = "✅" if status == "LIVE" else "⏳"
            print(f"  {status_icon} Bin {key.upper()} ({label}): {val} cm")
            
            # POWER RECOVERY: The most important part for Pi 5
            # We wait 0.4s between DIFFERENT physical sensors
            time.sleep(0.4)

    return results

def get_single_bin_median(key):
    """Helper to sample a specific sensor 5 times and return median."""
    readings = []
    sensor = sensors[key]
    
    with profile_block(f"bin_{key}_sample_loop"):
        for _ in range(5):
            try:
                val = sensor.distance * 100
                # Filters out negative or out-of-range values
                if 2.0 <= val <= 100.0:
                    readings.append(val)
            except Exception:
                pass
            sleep(0.06)
            
    return round(statistics.median(readings), 2) if readings else None

def run_sequence(target_angle):
    """Run one complete servo sequence. Returns immediately if already busy."""
    if not seq_lock.acquire(blocking=False):
        print("[INFO] Servo busy. Ignoring new request.")
        return False

    try:
        with profile_block("servo_sequence_total", extra={"target": target_angle}):
            with profile_block("servo_main_move", extra={"target": target_angle}):
                print(f"Moving main servo to {target_angle}")
                servo.angle = target_angle
                sleep(0.8)

            with profile_block("lid_open"):
                print("Opening lid")
                servo2.angle = config.FS90_TARGET
                sleep(2.0)

            with profile_block("lid_close"):
                print("Closing lid")
                servo2.angle = config.FS90_HOME
                sleep(0.5)

            with profile_block("servo_return_home"):
                print("Returning home...")
                servo.angle = config.MAIN_HOME
                sleep(1.0)

        return True
    finally:
        seq_lock.release()
        print("✅ Sequence Complete. Ready for next item.")