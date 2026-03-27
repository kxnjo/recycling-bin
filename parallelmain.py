from threading import Thread, Event, Lock
from queue import Queue
from time import sleep, time
import uuid
import json
import cv2
import requests

import config
import hardware
import ai_vision
import mqtt_publisher

# ================================
# GLOBAL QUEUES (PIPELINE)
# ================================
frame_queue = Queue(maxsize=2)
result_queue = Queue(maxsize=2)

capture_event = Event()
inference_event = Event()

hardware_lock = Lock()

current_request_id = None
inference_result = {"label": None}

# ================================
# INIT MODEL ONCE (IMPORTANT FIX)
# ================================
ai_vision.init_model()


# ================================
# CAMERA (REUSED - NO REOPEN)
# ================================
camera = cv2.VideoCapture(0)

def get_frame():
    ret, frame = camera.read()
    return ret, frame


# ================================
# DETECTION LOOP (PRODUCER)
# ================================
def monitor_detection():
    detect_sensor = hardware.sensors['d']

    while True:
        if hardware.seq_lock.locked():
            sleep(0.05)
            continue

        try:
            distance = detect_sensor.distance * 100
        except:
            distance = None

        if distance and 0 < distance <= config.DETECT_THRESHOLD:
            print("[DETECT] Object detected")

            # trigger pipeline instead of blocking call
            if not frame_queue.full():
                frame_queue.put("capture")

            sleep(1.5)

        sleep(0.05)


# ================================
# CAMERA WORKER (STAGE 1)
# ================================
def camera_worker():
    global current_request_id

    while True:
        frame_queue.get()

        ret, frame = get_frame()
        if not ret:
            frame_queue.task_done()
            continue

        # compress (OPTIMIZATION)
        frame = cv2.resize(frame, (320, 240))
        _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
        image_bytes = buffer.tobytes()

        current_request_id = str(uuid.uuid4())

        payload = {
            "id": current_request_id,
            "image": image_bytes  # keep raw (or base64 if needed)
        }

        # send to MQTT (non-blocking expectation)
        mqtt_publisher.send_image(json.dumps(payload), qos=1)

        # push to inference stage
        result = run_inference_pipeline(frame, image_bytes)
        result_queue.put(result)

        frame_queue.task_done()


# ================================
# INFERENCE PIPELINE (STAGE 2)
# ================================
def run_inference_pipeline(frame, image_bytes):
    global inference_result

    inference_event.clear()
    inference_result["label"] = None

    start = time()

    # wait for MQTT result
    if inference_event.wait(timeout=0.4):
        print("[MQTT] Result received")
        return inference_result["label"]

    # fallback 1: local AI
    try:
        print("[FALLBACK] Local AI")
        return ai_vision.infer_frame(frame)

    except Exception as e:
        print("[ERROR] Local AI failed:", e)

        # fallback 2: cloud
        try:
            print("[FALLBACK] Cloud")
            response = requests.post(
                config.CLOUD_MODEL_URL,
                data=image_bytes,
                timeout=2
            )
            return response.json().get("label")

        except Exception as e:
            print("[ERROR] Cloud failed:", e)
            return None


# ================================
# RESULT HANDLER (STAGE 3)
# ================================
def action_worker():
    while True:
        result = result_queue.get()

        if result:
            print("[FINAL RESULT]:", result)

            angle = 0
            if result.lower() == "plastic":
                angle = 90
            elif result.lower() == "paper":
                angle = 180

            # SAFE hardware execution
            with hardware_lock:
                hardware.run_sequence(angle)
                bin_levels = hardware.update_bin_levels()

                bin_levels["label"] = result
                bin_levels["timestamp"] = int(time())

                send_bin_levels_http(bin_levels)

        result_queue.task_done()


# ================================
# HTTP (ASYNC SAFE VERSION)
# ================================
API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"

def send_bin_levels_http(data):
    try:
        requests.post(API_URL, json=data, timeout=2)
    except:
        print("[HTTP] failed")


# ================================
# MQTT CALLBACK
# ================================
def handle_inference_result(msg):
    global inference_result

    try:
        data = json.loads(msg)
    except:
        return

    if data.get("id") != current_request_id:
        return

    inference_result["label"] = data.get("label")
    inference_event.set()


mqtt_publisher.subscribe_results(handle_inference_result)


# ================================
# START SYSTEM
# ================================
if __name__ == "__main__":

    Thread(target=monitor_detection, daemon=True).start()
    Thread(target=camera_worker, daemon=True).start()
    Thread(target=action_worker, daemon=True).start()

    print("System running (PIPELINED ARCHITECTURE)")
    while True:
        sleep(1)