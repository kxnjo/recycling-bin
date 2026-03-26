from threading import Event, Thread
from time import sleep, time
import sys

import config
# If your file really lives in profiling/hardware_wProfiling.py, switch this import back.
import hardware_wProfiling as hardware_wProfiling
import ai_vision as ai_vision
from profiler import log_profile, now, profile_block

capture_event = Event()
run_once_completed = Event()
last_detected_at = None


def handle_button_press(btn):
    angle = hardware_wProfiling.BUTTON_ANGLES[btn]
    Thread(target=hardware_wProfiling.run_sequence, args=(angle,), daemon=True).start()


hardware_wProfiling.button1.when_pressed = lambda: handle_button_press(hardware_wProfiling.button1)
hardware_wProfiling.button2.when_pressed = lambda: handle_button_press(hardware_wProfiling.button2)
hardware_wProfiling.button3.when_pressed = lambda: handle_button_press(hardware_wProfiling.button3)


def process_ai_detection():
    global last_detected_at
    pipeline_start = now()

    try:
        # AI stage total. The ai_vision module now also breaks this into smaller sub-stages.
        with profile_block("ai_capture_and_infer"):
            target_bin = ai_vision.capture_and_infer()

        if last_detected_at is not None:
            detect_to_infer_done_ms = (now() - last_detected_at) * 1000
            print(f"[PROFILE] detect_to_infer_done: {detect_to_infer_done_ms:.2f} ms")
            log_profile("detect_to_infer_done", detect_to_infer_done_ms, {"label": target_bin})

        print(f"[ACTION] Routing item to {target_bin.upper()} bin...")

        with profile_block("route_decision", extra={"label": target_bin}):
            if target_bin.lower() == "plastic":
                target_angle = 90
            elif target_bin.lower() == "paper":
                target_angle = 180
            else:
                target_angle = 0

        servo_thread = Thread(
            target=hardware_wProfiling.run_sequence,
            args=(target_angle,),
            daemon=True,
            name="servo-thread",
        )

        with profile_block("servo_thread_start", extra={"label": target_bin, "target": target_angle}):
            servo_thread.start()

        # This is the cleanest wait measurement for your current design.
        with profile_block("wait_for_servo_finish", extra={"label": target_bin}):
            servo_thread.join()

        with profile_block("update_bin_levels", extra={"label": target_bin}):
            bin_levels = hardware_wProfiling.update_bin_levels()

        bin_levels["label"] = target_bin
        bin_levels["timestamp"] = int(time())
        print("[INFO] Final bin levels:", bin_levels)

        total_pipeline_ms = (now() - pipeline_start) * 1000
        print(f"[PROFILE] total_ai_pipeline: {total_pipeline_ms:.2f} ms")
        log_profile("total_ai_pipeline", total_pipeline_ms, {"label": target_bin})

    except Exception as e:
        print(f"[ERROR] AI pipeline failed: {e}")

    finally:
        capture_event.clear()
        run_once_completed.set()


def monitor_detection_once():
    global last_detected_at
    detect_sensor = hardware_wProfiling.sensors["d"]

    while not run_once_completed.is_set():
        if not hardware_wProfiling.seq_lock.locked() and not capture_event.is_set():
            try:
                with profile_block("detect_sensor_read"):
                    distance = detect_sensor.distance * 100

                if 0 < distance <= config.DETECT_THRESHOLD:
                    last_detected_at = now()
                    print(f"\n[DETECT] Object detected at {round(distance, 1)} cm!")
                    capture_event.set()

                    with profile_block("start_ai_thread"):
                        ai_thread = Thread(target=process_ai_detection, daemon=True, name="ai-thread")
                        ai_thread.start()

                    run_once_completed.wait()
                    break

            except Exception as e:
                print(f"[ERROR] Sensor read failed: {e}")

        sleep(0.1)


if __name__ == "__main__":
    print("Smart Bin System Active (Run Once Mode). Waiting for one object...")
    monitor_detection_once()
    print("\n[INFO] One inference cycle completed. Exiting program.")
    sys.exit(0)