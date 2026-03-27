# main.py
from threading import Thread, Event
from queue import Queue, Empty
from signal import pause
from time import sleep, time
from enum import Enum

import cv2
import requests
import json
import uuid
import RPi.GPIO as GPIO

import config
import hardware
import ai_vision
import mqtt_publisher


# ================================
# GLOBAL STATE
# ================================
capture_event = Event()          # True while one item is being processed
inference_event = Event()        # True when MQTT inference response arrives

inference_result = {"label": None}
current_request_id = None
SYSTEM_RUNNING = True

# Single background queue for post-drop telemetry work
telemetry_queue = Queue()

API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"
LOG_FILE = "offline_bin_logs.jsonl"


# ================================
# BUTTON ROUTING (MANUAL TESTING)
# ================================
def handle_button_press(btn):
    angle = hardware.BUTTON_ANGLES[btn]
    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()


hardware.button1.when_pressed = lambda: handle_button_press(hardware.button1)
hardware.button2.when_pressed = lambda: handle_button_press(hardware.button2)
hardware.button3.when_pressed = lambda: handle_button_press(hardware.button3)


# ================================
# TELEMETRY / REPORTING
# Runs in background AFTER item drop is complete
# ================================
def telemetry_worker():
    while SYSTEM_RUNNING:
        try:
            job = telemetry_queue.get(timeout=0.5)
        except Empty:
            continue

        try:
            label = job["label"]
            inference_id = job.get("inference_id")
            timestamp = job.get("timestamp", int(time()))

            print("[TELEMETRY] Measuring bin levels in background...")
            bin_levels = hardware.update_bin_levels()

            bin_levels["label"] = label
            bin_levels["timestamp"] = timestamp
            if inference_id:
                bin_levels["inference_id"] = inference_id

            print("[TELEMETRY] Sending bin levels to manager Pi...")
            send_bin_levels_http(bin_levels)

            print("[TELEMETRY] Sending bin levels to dashboard...")
            mqtt_publisher.send_bin_levels(bin_levels)

        except Exception as e:
            print(f"[TELEMETRY] Error: {e}")
        finally:
            telemetry_queue.task_done()


def send_bin_levels_http(bin_levels):
    try:
        response = requests.post(API_URL, json=bin_levels, timeout=5)
        if response.status_code == 200:
            print(f"[HTTP] Sent bin levels successfully, status: {response.status_code}")
        else:
            print(f"[HTTP] Failed to send bin levels, status: {response.status_code}")
            save_to_local_log(bin_levels)
    except Exception as e:
        print(f"[HTTP] Failed: {e}")
        save_to_local_log(bin_levels)


def save_to_local_log(data):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(data) + "\n")
        print("[LOG] Saved data locally")
    except Exception as e:
        print(f"[LOG ERROR] Could not write to file: {e}")


def resend_offline_logs():
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    except Exception as e:
        print(f"[LOG] Failed to read offline log: {e}")
        return

    if not lines:
        return

    unsent = []

    for line in lines:
        try:
            data = json.loads(line.strip())
            response = requests.post(API_URL, json=data, timeout=5)
            if response.status_code == 200:
                print("[LOG] Resent one offline record successfully")
            else:
                unsent.append(line)
        except Exception:
            unsent.append(line)

    try:
        with open(LOG_FILE, "w") as f:
            f.writelines(unsent)
    except Exception as e:
        print(f"[LOG ERROR] Could not rewrite offline log: {e}")


# ================================
# LOCAL AI FALLBACK
# Used only if Pi 2 / MQTT inference fails
# ================================
def process_local_ai(frame):
    try:
        result = ai_vision.infer_frame(frame)
        return result
    except Exception as e:
        print(f"[LOCAL AI] Failed: {e}")
        return None


# ================================
# CLOUD FALLBACK
# ================================
def send_image_cloud(image_bytes):
    """
    Replace this with your real cloud API logic.
    Must return label string or None.
    """
    # Example placeholder:
    # response = requests.post(CLOUD_URL, files={"file": image_bytes}, timeout=5)
    # return response.json().get("label")
    return None


# ================================
# FRAME CAPTURE
# ================================
fallback_cap = None
prev_frame = None

def get_frame():
    global fallback_cap

    if fallback_cap is not None:
        cap = fallback_cap
    else:
        cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[ERROR] Camera not available")
        return False, None

    ret, frame = cap.read()

    if fallback_cap is None:
        cap.release()

    return ret, frame


def start_fallback_camera():
    global fallback_cap
    if fallback_cap is None:
        print("[FALLBACK] Starting camera...")
        fallback_cap = cv2.VideoCapture(0)


def stop_fallback_camera():
    global fallback_cap, prev_frame
    if fallback_cap is not None:
        print("[FALLBACK] Stopping camera...")
        fallback_cap.release()
        fallback_cap = None
    prev_frame = None


def camera_detects_object():
    global prev_frame

    if fallback_cap is None:
        return False

    ret, frame = get_frame()
    if not ret:
        return False

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (21, 21), 0)

    if prev_frame is None:
        prev_frame = gray
        return False

    diff = cv2.absdiff(prev_frame, gray)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

    motion = thresh.sum()
    prev_frame = gray

    return motion > 300000  # tune if needed


# ================================
# MQTT INFERENCE RESULT CALLBACK
# ================================
def handle_inference_result(client, userdata, msg):
    global current_request_id

    try:
        data = json.loads(msg.payload.decode())
    except Exception as e:
        print("[ERROR] Invalid MQTT message:", e)
        return

    if current_request_id is None:
        print("[MQTT] Too slow / no active request")
        return

    if data.get("id") != current_request_id:
        print("[IGNORE] Old/duplicate MQTT message")
        return

    label = data.get("label")
    if not label:
        print("[ERROR] Missing label in MQTT result")
        return

    print("[MQTT] Result received:", label)
    inference_result["label"] = label
    inference_event.set()


# ================================
# FINAL ITEM HANDLING
# This is the key flow:
# 1. classify item
# 2. route item
# 3. wait for servo to return home
# 4. allow next item
# 5. telemetry runs in background
# ================================
def handle_final_result(result, inference_id):
    result_lower = result.lower()
    print("[FINAL RESULT]:", result)
    print("[FINAL]: DONE BY:", inference_id)

    if result_lower == "plastic":
        angle = 90
    elif result_lower == "paper":
        angle = 180
    else:
        angle = 0

    print(f"[ACTION] Routing item to {result.upper()} bin...")
    hardware.run_sequence(angle)   # blocking on purpose

    print("[PIPE] Drop complete, servo returned home")

    telemetry_queue.put({
        "label": result,
        "timestamp": int(time()),
        "inference_id": inference_id
    })

    capture_event.clear()


# ================================
# MAIN ITEM PROCESSING PIPELINE
# ================================
def camera_capture():
    global current_request_id

    if capture_event.is_set():
        print("[CAMERA] Already processing, skipping.")
        return

    capture_event.set()
    print("[CAMERA] Capturing image...")

    ret, frame = get_frame()
    if not ret:
        print("[ERROR] Could not capture frame")
        capture_event.clear()
        return

    success, buffer = cv2.imencode(".jpg", frame)
    if not success:
        print("[ERROR] Failed to encode image")
        capture_event.clear()
        return

    image_bytes = buffer.tobytes()
    current_request_id = str(uuid.uuid4())

    inference_event.clear()
    inference_result["label"] = None

    result = None
    inference_id = None

    # 1. Try MQTT / Pi 2
    print("[PIPE] Sending via MQTT, waiting for result...")
    message = {
        "id": current_request_id,
        "image": image_bytes.hex()
    }

    try:
        mqtt_publisher.send_image(json.dumps(message), qos=1)
    except Exception as e:
        print(f"[PIPE] Failed to publish MQTT image: {e}")

    if inference_event.wait(timeout=0.1):
        result = inference_result["label"]
        if result:
            print(f"[PIPE] MQTT succeeded: {result}")
            inference_id = "mqtt"
        else:
            print("[PIPE] MQTT returned empty label")
            result = None

    # 2. Try local AI
    if result is None:
        print("[PIPE] MQTT failed, trying Local AI...")
        result = process_local_ai(frame)
        if result:
            print(f"[PIPE] Local AI succeeded: {result}")
            inference_id = "local"

    # 3. Try cloud
    if result is None:
        print("[PIPE] Local AI failed, trying Cloud...")
        try:
            result = send_image_cloud(image_bytes)
            if result:
                print(f"[PIPE] Cloud succeeded: {result}")
                inference_id = "cloud"
            else:
                print("[PIPE] Cloud returned None")
        except Exception as e:
            print(f"[PIPE] Cloud failed: {e}")
            result = None

    # 4. Final action
    if result:
        handle_final_result(result, inference_id)
    else:
        print("[PIPE] All inference sources failed, no action taken")
        capture_event.clear()


# ================================
# DISTANCE MONITOR
# ================================
ultra_history = []
ULTRA_HISTORY_SIZE = 5
ULTRASONIC_FAIL_THRESHOLD = 5
prev_distance = None

class DetectState(Enum):
    ULTRASONIC = 1
    CAMERA_FALLBACK = 2


def is_ultrasonic_healthy(distance):
    return True


def monitor_detection():
    global ultra_history, prev_distance

    state = DetectState.ULTRASONIC
    fail_count = 0
    last_retry_time = 0

    while SYSTEM_RUNNING:
        if time() - last_retry_time > 10:
            resend_offline_logs()
            last_retry_time = time()

        # Do not detect new item if:
        # - servo is still moving
        # - current item is still being processed
        if hardware.seq_lock.locked() or capture_event.is_set():
            sleep(0.1)
            continue

        distance = None

        try:
            distance = hardware.read_ultrasonic_sensor("d")
            if distance is not None:
                ultra_history.append(round(distance, 1))
                if len(ultra_history) > ULTRA_HISTORY_SIZE:
                    ultra_history.pop(0)
        except Exception as e:
            print(f"[ERROR] Sensor read failed: {e}")

        healthy = is_ultrasonic_healthy(distance)

        if state == DetectState.ULTRASONIC:
            if not healthy:
                fail_count += 1
                print(f"[WARN] Ultrasonic unhealthy ({fail_count}/{ULTRASONIC_FAIL_THRESHOLD})")
                if fail_count >= ULTRASONIC_FAIL_THRESHOLD:
                    print("[WARN] Confirmed failure -> switching to CAMERA fallback")
                    start_fallback_camera()
                    state = DetectState.CAMERA_FALLBACK
                    fail_count = 0
            else:
                fail_count = 0

            print(f"prev distance: {prev_distance}")

            if distance:
                triggered = False

                if 0 < distance <= config.DETECT_THRESHOLD:
                    triggered = True

                if prev_distance is not None:
                    delta = prev_distance - distance
                    if delta > 10:
                        print(f"[DEBUG] Sudden drop detected: {round(delta, 1)} cm")
                        triggered = True

                if triggered:
                    print(f"\n[DETECT] Triggered at {round(distance, 1)} cm")
                    camera_capture()
                    sleep(2)

                prev_distance = distance

        elif state == DetectState.CAMERA_FALLBACK:
            if camera_detects_object():
                print("[FALLBACK CAMERA] Object detected!")
                camera_capture()
                sleep(2)

            if healthy:
                print("[RECOVERY] Ultrasonic recovering...")
                stop_fallback_camera()
                state = DetectState.ULTRASONIC
                fail_count = 0

        sleep(0.1)


# ================================
# MQTT SUBSCRIPTION
# ================================
mqtt_publisher.subscribe_results(handle_inference_result)


# ================================
# START SYSTEM
# ================================
if __name__ == "__main__":
    try:
        hardware.setup_ultrasonic()

        Thread(target=monitor_detection, daemon=True).start()
        Thread(target=telemetry_worker, daemon=True).start()

        print("Smart Bin System Active. Waiting for objects...")
        pause()

    except KeyboardInterrupt:
        print("\n[SYSTEM] Shutting down cleanly...")
        SYSTEM_RUNNING = False
        sleep(0.5)

    finally:
        stop_fallback_camera()
        GPIO.cleanup()