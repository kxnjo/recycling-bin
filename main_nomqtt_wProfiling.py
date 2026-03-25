from threading import Thread, Event
from time import sleep, time
import sys

import config
import hardware_wProfiling
import ai_vision
from profiler import profile_block, now, log_profile

capture_event = Event()

# Track one-cycle profiling
last_detected_at = None
run_once_completed = Event()


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
# 🧠 AI ROUTING (Fully Local, Run Once)
# ================================
def process_ai_detection():
    global last_detected_at

    pipeline_start = now()

    try:
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
                target_angle = 90
            elif target_bin.lower() == "paper":
                target_angle = 180
            else:  # general
                target_angle = 0

            servo_thread = Thread(
                target=hardware_wProfiling.run_sequence,
                args=(target_angle,),
                daemon=True
            )
            servo_thread.start()

        # 3. Wait until servo finishes
        servo_wait_start = now()
        hardware_wProfiling.seq_lock.acquire()   # blocks until run_sequence releases
        servo_wait_ms = (now() - servo_wait_start) * 1000
        print(f"[PROFILE] wait_for_servo_finish: {servo_wait_ms:.2f} ms")
        log_profile("wait_for_servo_finish", servo_wait_ms, f"label={target_bin}")

        try:
            # 4. Update bin levels
            with profile_block("update_bin_levels", extra=f"label={target_bin}"):
                bin_levels = hardware_wProfiling.update_bin_levels()

            bin_levels["label"] = target_bin
            bin_levels["timestamp"] = int(time())

            print("[INFO] Final bin levels:", bin_levels)

        finally:
            hardware_wProfiling.seq_lock.release()

        total_pipeline_ms = (now() - pipeline_start) * 1000
        print(f"[PROFILE] total_ai_pipeline: {total_pipeline_ms:.2f} ms")
        log_profile("total_ai_pipeline", total_pipeline_ms, f"label={target_bin}")

    except Exception as e:
        print(f"[ERROR] AI pipeline failed: {e}")

    finally:
        capture_event.clear()
        run_once_completed.set()


# ================================
# 👀 DISTANCE MONITOR (Run Once)
# ================================
def monitor_detection_once():
    """Monitor the detect sensor until one item is detected, then stop."""
    global last_detected_at
    detect_sensor = hardware_wProfiling.sensors["d"]

    while not run_once_completed.is_set():
        if not hardware_wProfiling.seq_lock.locked() and not capture_event.is_set():
            try:
                with profile_block("detect_sensor_read"):
                    distance = detect_sensor.distance * 100  # cm

                if 0 < distance <= config.DETECT_THRESHOLD:
                    last_detected_at = now()
                    print(f"\n[DETECT] Object detected at {round(distance, 1)} cm!")

                    capture_event.set()

                    with profile_block("start_ai_thread"):
                        ai_thread = Thread(target=process_ai_detection, daemon=True)
                        ai_thread.start()

                    # Wait for the one inference cycle to complete
                    run_once_completed.wait()
                    break

            except Exception as e:
                print(f"[ERROR] Sensor read failed: {e}")

        sleep(0.1)


# ================================
# 🚀 START SYSTEM
# ================================
if __name__ == "__main__":
    print("Smart Bin System Active (Run Once Mode). Waiting for one object...")

    monitor_detection_once()

    print("\n[INFO] One inference cycle completed. Exiting program.")
    sys.exit(0)