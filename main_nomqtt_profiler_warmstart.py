from threading import Event, Thread
from time import sleep, time
import sys

import config
import hardware_wProfiling_robustReading as hardware_wProfiling
import ai_vision_delayedClose as ai_vision
from profiler import log_profile, now, profile_block

capture_event = Event()
run_once_completed = Event()
last_detected_at = None

NUM_RUNS = 10
WAIT_BETWEEN_RUNS = 2.0   # seconds between attempts
WAIT_FOR_OBJECT_REMOVAL = True


def handle_button_press(btn):
    angle = hardware_wProfiling.BUTTON_ANGLES[btn]
    Thread(target=hardware_wProfiling.run_sequence, args=(angle,), daemon=True).start()


hardware_wProfiling.button1.when_pressed = lambda: handle_button_press(hardware_wProfiling.button1)
hardware_wProfiling.button2.when_pressed = lambda: handle_button_press(hardware_wProfiling.button2)
hardware_wProfiling.button3.when_pressed = lambda: handle_button_press(hardware_wProfiling.button3)


def process_ai_detection(attempt_no):
    global last_detected_at
    pipeline_start = now()

    try:
        print(f"[INFO] Attempt {attempt_no}: starting AI pipeline")

        # AI stage total
        with profile_block("ai_capture_and_infer", extra={"attempt": attempt_no}):
            target_bin = ai_vision.capture_and_infer()

        if last_detected_at is not None:
            detect_to_infer_done_ms = (now() - last_detected_at) * 1000
            print(f"[PROFILE] detect_to_infer_done: {detect_to_infer_done_ms:.2f} ms")
            log_profile(
                "detect_to_infer_done",
                detect_to_infer_done_ms,
                {"label": target_bin, "attempt": attempt_no},
            )

        print(f"[ACTION] Routing item to {target_bin.upper()} bin...")

        with profile_block("route_decision", extra={"label": target_bin, "attempt": attempt_no}):
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
            name=f"servo-thread-{attempt_no}",
        )

        with profile_block(
            "servo_thread_start",
            extra={"label": target_bin, "target": target_angle, "attempt": attempt_no},
        ):
            servo_thread.start()

        with profile_block("wait_for_servo_finish", extra={"label": target_bin, "attempt": attempt_no}):
            servo_thread.join()

        with profile_block("update_bin_levels", extra={"label": target_bin, "attempt": attempt_no}):
            bin_levels = hardware_wProfiling.update_bin_levels()

        bin_levels["label"] = target_bin
        bin_levels["timestamp"] = int(time())
        bin_levels["attempt"] = attempt_no
        print("[INFO] Final bin levels:", bin_levels)

        total_pipeline_ms = (now() - pipeline_start) * 1000
        print(f"[PROFILE] total_ai_pipeline: {total_pipeline_ms:.2f} ms")
        log_profile(
            "total_ai_pipeline",
            total_pipeline_ms,
            {"label": target_bin, "attempt": attempt_no},
        )

    except Exception as e:
        print(f"[ERROR] Attempt {attempt_no}: AI pipeline failed: {e}")

    finally:
        capture_event.clear()
        run_once_completed.set()


def wait_until_object_removed():
    detect_sensor = hardware_wProfiling.sensors["d"]

    print("[INFO] Waiting for object to be removed before next attempt...")
    while True:
        try:
            distance = detect_sensor.distance * 100
            # assume object is removed when distance is above threshold
            if distance > config.DETECT_THRESHOLD:
                print("[INFO] Detection area is clear.")
                break
        except Exception as e:
            print(f"[WARN] Object-clear check failed: {e}")
        sleep(0.2)


def monitor_detection_once(attempt_no):
    global last_detected_at
    detect_sensor = hardware_wProfiling.sensors["d"]

    # reset per-attempt state
    capture_event.clear()
    run_once_completed.clear()
    last_detected_at = None

    print(f"\n===== ATTEMPT {attempt_no}/{NUM_RUNS} =====")
    print("Waiting for one object...")

    while not run_once_completed.is_set():
        if not hardware_wProfiling.seq_lock.locked() and not capture_event.is_set():
            try:
                with profile_block("detect_sensor_read", extra={"attempt": attempt_no}):
                    distance = detect_sensor.distance * 100

                if 0 < distance <= config.DETECT_THRESHOLD:
                    last_detected_at = now()
                    print(f"\n[DETECT] Object detected at {round(distance, 1)} cm!")
                    capture_event.set()

                    with profile_block("start_ai_thread", extra={"attempt": attempt_no}):
                        ai_thread = Thread(
                            target=process_ai_detection,
                            args=(attempt_no,),
                            daemon=True,
                            name=f"ai-thread-{attempt_no}",
                        )
                        ai_thread.start()

                    run_once_completed.wait()
                    break

            except Exception as e:
                print(f"[ERROR] Attempt {attempt_no}: sensor read failed: {e}")

        sleep(0.1)


if __name__ == "__main__":
    print(f"Smart Bin System Active (Loop Mode). Waiting for {NUM_RUNS} objects...")

    for attempt_no in range(1, NUM_RUNS + 1):
        monitor_detection_once(attempt_no)

        print(f"[INFO] Attempt {attempt_no} completed.")

        if attempt_no < NUM_RUNS:
            if WAIT_FOR_OBJECT_REMOVAL:
                wait_until_object_removed()

            if WAIT_BETWEEN_RUNS > 0:
                print(f"[INFO] Waiting {WAIT_BETWEEN_RUNS:.1f}s before next attempt...")
                sleep(WAIT_BETWEEN_RUNS)

    print("\n[INFO] All inference cycles completed. Exiting program.")
    sys.exit(0)