import statistics
from threading import Lock
from time import sleep

from gpiozero import AngularServo, Button, DistanceSensor

import config
from profiler_adjusted import profile_block

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


def update_bin_levels():
    """Sequentially sample all three bins and return median distances in cm."""
    print("📊 Measuring bin fill levels...")
    results = {}

    with profile_block("bin_level_scan_total"):
        for key in ["a", "b", "c"]:
            readings = []
            sensor = sensors[key]

            with profile_block(f"bin_{key}_sample_loop"):
                for _ in range(5):
                    try:
                        val = sensor.distance * 100
                        if 2.0 <= val <= 100.0:
                            readings.append(val)
                    except Exception:
                        pass
                    sleep(0.06)

            median_val = round(statistics.median(readings), 2) if readings else None
            results[key] = median_val
            label = config.BIN_CONFIGS[key]["label"]
            print(f"  - Bin {key.upper()} ({label}): {median_val} cm")

            # Keep this separate so you can see how much time is just the settle delay.
            with profile_block(f"bin_{key}_settle_delay"):
                sleep(0.1)

    return results


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