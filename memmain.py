import cv2
import time
import uuid
import requests
from threading import Thread, Lock, Event

import config
import hardware
import ai_vision
import mqtt_publisher


# =========================
# GLOBAL SHARED STATE
# =========================
latest_frame = None
frame_lock = Lock()

result_queue = []
result_lock = Lock()

inference_event = Event()
hardware_lock = Lock()

camera = cv2.VideoCapture(0)

# init model ONCE
ai_vision.init_model()


# =========================
# CAMERA WORKER (NO QUEUES, NO ENCODING)
# =========================
def camera_worker():
    global latest_frame

    while True:
        ret, frame = camera.read()
        if not ret:
            continue

        frame = cv2.resize(frame, (320, 240))

        with frame_lock:
            latest_frame = frame

        time.sleep(0.01)  # slight throttle (prevents CPU spin)


# =========================
# INFERENCE WORKER (LOW COPY)
# =========================
def inference_worker():
    global latest_frame

    last_frame_id = None

    while True:
        inference_event.wait()
        inference_event.clear()

        with frame_lock:
            if latest_frame is None:
                continue
            frame = latest_frame.copy()  # only safe copy point

        try:
            result = ai_vision.infer_frame(frame)
        except Exception as e:
            print("[AI ERROR]", e)
            result = None

        if result:
            with result_lock:
                result_queue.append(result)


# =========================
# DETECTION WORKER
# =========================
def detection_worker():
    sensor = hardware.sensors['d']

    while True:
        if hardware.seq_lock.locked():
            time.sleep(0.05)
            continue

        try:
            distance = sensor.distance * 100
        except:
            distance = None

        if distance and 0 < distance <= config.DETECT_THRESHOLD:
            inference_event.set()
            time.sleep(1.0)  # debounce

        time.sleep(0.05)


# =========================
# ACTION WORKER (HARDWARE + HTTP)
# =========================
def action_worker():
    API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"

    while True:
        if not result_queue:
            time.sleep(0.05)
            continue

        with result_lock:
            if not result_queue:
                continue
            result = result_queue.pop(0)

        print("[RESULT]", result)

        # map result → hardware action
        angle = 0
        if result.lower() == "plastic":
            angle = 90
        elif result.lower() == "paper":
            angle = 180

        with hardware_lock:
            hardware.run_sequence(angle)
            bin_levels = hardware.update_bin_levels()

        # attach metadata only
        payload = {
            "id": str(uuid.uuid4()),
            "label": result,
            "timestamp": int(time.time()),
            "bin_levels": bin_levels
        }

        # HTTP call (async safe)
        try:
            requests.post(API_URL, json=payload, timeout=2)
        except:
            print("[HTTP] failed")


# =========================
# MQTT CALLBACK (OPTIONAL)
# =========================
def mqtt_callback(msg):
    """
    Optional: use only if you still receive cloud inference results
    """
    pass


# =========================
# START SYSTEM
# =========================
if __name__ == "__main__":

    Thread(target=camera_worker, daemon=True).start()
    Thread(target=inference_worker, daemon=True).start()
    Thread(target=detection_worker, daemon=True).start()
    Thread(target=action_worker, daemon=True).start()

    print("🚀 Optimized Memory-Pipelined System Running")

    while True:
        time.sleep(1)