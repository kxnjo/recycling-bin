# main.py (PROFILED VERSION - LOGGING ONLY)
from threading import Thread, Event, Lock
from signal import pause
from time import sleep, time, perf_counter
import cv2
import RPi.GPIO as GPIO
import config
import hardware
import ai_vision
import mqtt_publisher
import requests
import uuid
import json
from enum import Enum

# ================================
# LOGGING (NUMBERS ONLY)
# ================================
LOG_FILE = "system_profile_log.jsonl"
log_lock = Lock()

def log_event(event, meta=None, start=None):
    """Write structured timing logs (no graphs, just raw numbers)."""
    entry = {
        "timestamp": time(),
        "event": event,
    }
    if meta:
        entry["meta"] = meta
    if start is not None:
        entry["duration_ms"] = round((perf_counter() - start) * 1000, 3)

    try:
        with log_lock:
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print("[LOG ERROR]", e)

# ================================
# GLOBAL STATE
# ================================
SYSTEM_RUNNING = True
capture_event = Event()
inference_event = Event()
inference_result = {"label": None}
current_request_id = None

# ================================
# BUTTON HANDLER
# ================================
def handle_button_press(btn):
    angle = hardware.BUTTON_ANGLES[btn]
    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

hardware.button1.when_pressed = lambda: handle_button_press(hardware.button1)
hardware.button2.when_pressed = lambda: handle_button_press(hardware.button2)
hardware.button3.when_pressed = lambda: handle_button_press(hardware.button3)

# ================================
# AI ROUTING
# ================================
def process_ai_detection():
    t0 = perf_counter()
    log_event("ai_detection_start")

    ai_vision.init_model()
    target_bin = ai_vision.capture_and_infer()

    log_event("ai_inference_done", {"label": target_bin}, t0)

    angle_map = {"plastic": 90, "paper": 180}
    angle = angle_map.get(target_bin.lower(), 0)

    t1 = perf_counter()
    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()
    hardware.seq_lock.acquire()

    bin_levels = hardware.update_bin_levels()
    bin_levels["label"] = target_bin
    bin_levels["timestamp"] = int(time())

    mqtt_publisher.send_bin_levels(bin_levels)

    hardware.seq_lock.release()
    log_event("ai_hardware_cycle_done", bin_levels, t1)

    capture_event.clear()

# ================================
# CAMERA CAPTURE PIPELINE
# ================================

def camera_capture():
    global current_request_id

    if capture_event.is_set():
        log_event("capture_skipped_busy")
        return

    capture_event.set()
    t0 = perf_counter()
    log_event("camera_capture_start")

    ret, frame = get_frame()
    if not ret:
        log_event("camera_fail_no_frame", None, t0)
        capture_event.clear()
        return

    success, buffer = cv2.imencode('.jpg', frame)
    if not success:
        log_event("camera_fail_encode", None, t0)
        capture_event.clear()
        return

    image_bytes = buffer.tobytes()
    current_request_id = str(uuid.uuid4())

    inference_event.clear()
    inference_result["label"] = None

    # ================= MQTT =================
    t_mqtt = perf_counter()
    mqtt_publisher.send_image(json.dumps({"id": current_request_id, "image": image_bytes.hex()}), qos=1)

    result = None
    inference_id = None

    if inference_event.wait(timeout=0.1):
        result = inference_result["label"]
        inference_id = "mqtt"
        log_event("mqtt_success", {"label": result}, t_mqtt)
    else:
        log_event("mqtt_timeout", None, t_mqtt)

    # ================= LOCAL AI =================
    if result is None:
        t_local = perf_counter()
        try:
            result = ai_vision.infer_frame(frame)
            inference_id = "local"
            log_event("local_ai_success", {"label": result}, t_local)
        except Exception as e:
            log_event("local_ai_fail", {"error": str(e)}, t_local)

    # ================= CLOUD =================
    if result is None:
        t_cloud = perf_counter()
        result = send_image_cloud(image_bytes)
        log_event("cloud_result", {"label": result}, t_cloud)
        inference_id = "cloud"

    if result:
        handle_final_result(result, inference_id)
    else:
        log_event("all_sources_failed")
        capture_event.clear()

# ================================
# MQTT HANDLER
# ================================

def handle_inference_result(msg):
    global inference_result
    t0 = perf_counter()

    try:
        data = json.loads(msg)
    except Exception as e:
        log_event("mqtt_invalid_msg", {"error": str(e)})
        return

    if data.get("id") != current_request_id:
        log_event("mqtt_ignore_old")
        return

    inference_result["label"] = data.get("label")
    inference_event.set()

    log_event("mqtt_callback_received", data, t0)

# ================================
# FINAL HANDLER
# ================================

def handle_final_result(result, inference_id):
    t0 = perf_counter()
    log_event("final_result_start", {"label": result, "source": inference_id})

    angle_map = {"plastic": 90, "paper": 180}
    angle = angle_map.get(result.lower(), 0)

    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

    hardware.seq_lock.acquire()

    bin_levels = hardware.update_bin_levels()
    bin_levels["label"] = result
    bin_levels["timestamp"] = int(time())
    bin_levels["inference_id"] = inference_id

    send_bin_levels_http(bin_levels)

    hardware.seq_lock.release()

    capture_event.clear()
    log_event("final_result_done", bin_levels, t0)

# ================================
# HTTP
# ================================
API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"

def send_bin_levels_http(bin_levels):
    t0 = perf_counter()
    try:
        r = requests.post(API_URL, json=bin_levels, timeout=3)
        log_event("http_send", {"status": r.status_code}, t0)
    except Exception as e:
        log_event("http_fail", {"error": str(e)}, t0)

# ================================
# CLOUD
# ================================
CLOUD_MODEL_URL = "http://3.93.218.220:5000/infer"

def send_image_cloud(image_bytes):
    t0 = perf_counter()
    try:
        r = requests.post(CLOUD_MODEL_URL, data=image_bytes, timeout=5)
        if r.status_code == 200:
            label = r.json().get("label")
            log_event("cloud_success", {"label": label}, t0)
            return label
        log_event("cloud_bad_status", {"status": r.status_code}, t0)
    except Exception as e:
        log_event("cloud_fail", {"error": str(e)}, t0)
    return None

# ================================
# CAMERA HELPERS
# ================================
fallback_cap = None
prev_frame = None

def get_frame():
    global fallback_cap
    cap = fallback_cap or cv2.VideoCapture(0)
    if not cap.isOpened():
        return False, None
    ret, frame = cap.read()
    if fallback_cap is None:
        cap.release()
    return ret, frame

# ================================
# DETECTION LOOP
# ================================
class DetectState(Enum):
    ULTRASONIC = 1
    CAMERA = 2


def monitor_detection():
    state = DetectState.ULTRASONIC
    fail_count = 0

    while SYSTEM_RUNNING:
        t_loop = perf_counter()

        if hardware.seq_lock.locked() or capture_event.is_set():
            sleep(0.1)
            continue

        distance = hardware.read_ultrasonic_sensor('d')
        log_event("ultrasonic_read", {"distance": distance}, t_loop)

        if state == DetectState.ULTRASONIC:
            if distance and distance < config.DETECT_THRESHOLD:
                log_event("trigger_detect", {"distance": distance})
                camera_capture()

        sleep(0.1)

# ================================
# START
# ================================
if __name__ == "__main__":
    hardware.setup_ultrasonic()
    Thread(target=monitor_detection, daemon=True).start()
    print("SYSTEM STARTED (PROFILED MODE)")
    pause()

finally:
    GPIO.cleanup()
