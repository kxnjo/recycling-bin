# main.py
from threading import Thread, Event
from signal import pause
from time import sleep, time
from profiler import profile_block, now, log_profile
import cv2

import config
import hardware_wProfiling
import ai_vision

capture_event = Event()
last_detected_at = None
last_ai_start_at = None

# ================================
# 🎯 BUTTON ROUTING
# ================================
def handle_button_press(btn):
    angle = hardware_wProfiling.BUTTON_ANGLES[btn]
    Thread(target=hardware_wProfiling.run_sequence, args=(angle,), daemon=True).start()

# Attach the handlers
hardware_wProfiling.button1.when_pressed = lambda: handle_button_press(hardware_wProfiling.button1)
hardware_wProfiling.button2.when_pressed = lambda: handle_button_press(hardware_wProfiling.button2)
hardware_wProfiling.button3.when_pressed = lambda: handle_button_press(hardware_wProfiling.button3)


# ================================
# 🧠 AI ROUTING (Fully Local)
# ================================
def process_ai_detection():
    global last_ai_start_at, last_detected_at

    pipeline_start = now()
    last_ai_start_at = pipeline_start

    # 1. Take picture and get result from ai_vision module
    with profile_block("ai_capture_and_infer"):
        target_bin = ai_vision.capture_and_infer()

    if last_detected_at is not None:
        detect_to_infer_done_ms = (now() - last_detected_at) * 1000
        print(f"[PROFILE] detect_to_infer_done: {detect_to_infer_done_ms:.2f} ms")
        log_profile("detect_to_infer_done", detect_to_infer_done_ms, f"label={target_bin}")

    # 2. Trigger hardware_wProfiling based on result
    print(f"[ACTION] Routing item to {target_bin.upper()} bin...")

    with profile_block("route_decision", extra=f"label={target_bin}"):
        if target_bin.lower() == "plastic":
            Thread(target=hardware_wProfiling.run_sequence, args=(90,), daemon=True).start()
        elif target_bin.lower() == "paper":
            Thread(target=hardware_wProfiling.run_sequence, args=(180,), daemon=True).start()
        else:  # general
            Thread(target=hardware_wProfiling.run_sequence, args=(0,), daemon=True).start()

    # 3. Update levels after sorting
    servo_wait_start = now()
    hardware_wProfiling.seq_lock.acquire()  # wait until servo finishes
    servo_wait_ms = (now() - servo_wait_start) * 1000
    print(f"[PROFILE] wait_for_servo_finish: {servo_wait_ms:.2f} ms")
    log_profile("wait_for_servo_finish", servo_wait_ms, f"label={target_bin}")

    try:
        with profile_block("update_bin_levels", extra=f"label={target_bin}"):
            bin_levels = hardware_wProfiling.update_bin_levels()

        bin_levels["label"] = target_bin
        bin_levels["timestamp"] = int(time())

    finally:
        hardware_wProfiling.seq_lock.release()

    total_pipeline_ms = (now() - pipeline_start) * 1000
    print(f"[PROFILE] total_ai_pipeline: {total_pipeline_ms:.2f} ms")
    log_profile("total_ai_pipeline", total_pipeline_ms, f"label={target_bin}")

    # 4. Unlock the event
    capture_event.clear()


# ================================
# 👀 DISTANCE MONITOR
# ================================
def monitor_detection():
    """Continuously monitor the 'd' sensor and trigger Local AI."""
    global last_detected_at
    detect_sensor = hardware_wProfiling.sensors["d"]

    while True:
        if not hardware_wProfiling.seq_lock.locked() and not capture_event.is_set():
            try:
                with profile_block("detect_sensor_read"):
                    distance = detect_sensor.distance * 100  # cm

                if 0 < distance <= config.DETECT_THRESHOLD:
                    last_detected_at = now()
                    print(f"\n[DETECT] Object detected at {round(distance, 1)} cm!")

                    # Lock the capture event and spin up the Local AI thread
                    capture_event.set()

                    with profile_block("start_ai_thread"):
                        Thread(target=process_ai_detection, daemon=True).start()

                    sleep(2)  # Cooldown

            except Exception as e:
                print(f"[ERROR] Sensor read failed: {e}")

        sleep(0.1)

# ================================
# 🚀 START SYSTEM
# ================================
if __name__ == "__main__":
    Thread(target=monitor_detection, daemon=True).start()
    print("Smart Bin System Active with Local AI. Waiting for objects...")
    pause()