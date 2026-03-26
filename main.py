# main.py
from threading import Thread, Event
from signal import pause
from time import sleep, time
from unittest import result
import cv2

import config
import hardware
import ai_vision
# MQTT
import mqtt_publisher
capture_event = Event()
# HTTP
import requests

#MQTT QoS Test
import uuid
import json

inference_event = Event()
inference_result = {"label": None}
current_request_id = None  

# ================================
# ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ BUTTON ROUTING
# ================================
def handle_button_press(btn):
    angle = hardware.BUTTON_ANGLES[btn]
    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

# Attach the handlers
hardware.button1.when_pressed = lambda: handle_button_press(hardware.button1)
hardware.button2.when_pressed = lambda: handle_button_press(hardware.button2)
hardware.button3.when_pressed = lambda: handle_button_press(hardware.button3)


# ================================
# ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â  AI ROUTING THIS IS FOR LOCAL DETECTION IF PI 2 DIED
# ================================
def process_ai_detection():
    # 1. Take picture and get result from ai_vision module
    ai_vision.init_model()  # Load model before first inference
    target_bin = ai_vision.capture_and_infer()

    # 2. Trigger hardware based on result
    print(f"[ACTION] Routing item to {target_bin.upper()} bin...")
    if target_bin.lower() == "plastic":
        Thread(target=hardware.run_sequence, args=(90,), daemon=True).start()
    elif target_bin.lower() == "paper":
        Thread(target=hardware.run_sequence, args=(180,), daemon=True).start()
    else: # general
        Thread(target=hardware.run_sequence, args=(0,), daemon=True).start()

    hardware.seq_lock.acquire()  # wait until servo finishes
    bin_levels = hardware.update_bin_levels()
    print(f"[ACTION] sending data to dashboard")
    bin_levels['label'] = target_bin
    bin_levels['timestamp'] = int(time())
    mqtt_publisher.send_bin_levels(bin_levels)  # send via MQTT
    hardware.seq_lock.release()
    print(f"[ACTION] sending data to dashboard")

    # 3. Unlock the event
    capture_event.clear()

# ================================
# CAMERA CAPTURE & MQTT SENDING
# ================================
def camera_capture():
    global current_request_id

    print("[CAMERA] Capturing image...")
    capture_event.set()

    ret, frame = get_frame()

    if not ret:
        print("[ERROR] Could not capture frame")
        capture_event.clear()
        return

    # Encode image
    success, buffer = cv2.imencode('.jpg', frame)
    if not success:
        print("[ERROR] Failed to encode image")
        capture_event.clear()
        return

    image_bytes = buffer.tobytes()

    # ðŸ†” Generate unique request ID
    current_request_id = str(uuid.uuid4())

    message = {
        "id": current_request_id,
        "image": image_bytes.hex()  # (can optimize later)
    }

    # Reset state
    inference_event.clear()
    inference_result["label"] = None

    start = time()
    result = None

    # =========================
    # 1. MQTT Inference (Pi 2)
    # =========================
    mqtt_publisher.send_image(json.dumps(message), qos=1)

    if inference_event.wait(timeout=0.2): # if too slow , do local inference
        latency = time() - start
        print(f"[SUCCESS] Result received in {latency:.2f}s")
        result = inference_result["label"]

    # =========================
    # 2. Local AI fallback (Pi 1)
    # =========================
    else:
        try:
            print("[FALLBACK] Timeout → running local AI")
            ai_vision.init_model()
            current_request_id = None
            # result = ai_vision.capture_and_infer()
            result = ai_vision.infer_frame(frame)
        # =========================
        # 3. Cloud fallback
        # =========================
        except Exception as e:
            print(f"[FALLBACK] Local AI failed: {e}")
            print("[FALLBACK] Trying cloud inference...")
            result = send_image_cloud(image_bytes)

    # Once we have a result, 
    if result is not None:
        handle_final_result(result)
    else:
        print("[ERROR] All inference methods failed")

# ================================
# SEND HTTP REQUEST I THINK
# ================================
API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"

def send_bin_levels_http(bin_levels):
    try:
        response = requests.post(API_URL, json=bin_levels)
        if response.status_code == 200:
            print(f"[HTTP] Sent bin levels, status: {response.status_code}")
        else:
            print(f"[HTTP] Failed to send bin levels, status: {response.status_code}")
            save_to_local_log(bin_levels)
    except Exception as e:
        print(f"[HTTP] Failed: {e}")
        save_to_local_log(bin_levels)


LOG_FILE = "offline_bin_logs.jsonl"

def save_to_local_log(data):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(data) + "\n")
        print("[LOG] Saved data locally")
    except Exception as e:
        print(f"[LOG ERROR] Could not write to file: {e}")

BATCH_SIZE = 10

def resend_offline_logs():
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return

    remaining_lines = []

    # Process in batches
    for i in range(0, len(lines), BATCH_SIZE):
        batch = lines[i:i+BATCH_SIZE]
        data_batch = [json.loads(line) for line in batch]

        try:
            response = requests.post(API_URL + "/batch", json=data_batch, timeout=3)

            if response.status_code == 200:
                print(f"[RETRY] Sent batch of {len(batch)} logs")
            else:
                print("[RETRY] Batch failed")
                remaining_lines.extend(batch)

        except:
            print("[RETRY] Network error, keeping batch")
            remaining_lines.extend(batch)

    # Rewrite file with failed logs only
    with open(LOG_FILE, "w") as f:
        f.writelines(remaining_lines)
    

CLOUD_MODEL_URL = "http://44.201.198.140:5000/infer"
def send_image_cloud(image_bytes):
    try:
        response = requests.post(CLOUD_MODEL_URL, data=image_bytes)
        if response.status_code == 200:
            result = response.json().get("label")
            print(f"[CLOUD] Received result: {result}")
            return result
        else:
            print(f"[CLOUD] Inference failed with status: {response.status_code}")
            return None
    except Exception as e:
        print(f"[CLOUD] Failed to send image: {e}")
        return None

# ================================
# ACTION AFTER RECEIVING MQTT RESULT
# ================================

def handle_inference_result(msg):
    global inference_result

    try:
        data = json.loads(msg)
    except Exception as e:
        print("[ERROR] Invalid MQTT message:", e)
        return
    
    if current_request_id is None:
        print("[MQTT] TOO SLOW")
        return
        
    # Ignore old / duplicate responses
    if data.get("id") != current_request_id:
        print("[IGNORE] Old/duplicate message")
        return

    label = data.get("label")
    if not label:
        print("[ERROR] Missing label")
        return

    print("[MQTT] Result received:", label)

    inference_result["label"] = label
    inference_event.set()
    
def handle_final_result(result):
    print("[FINAL RESULT]:", result)

    if result.lower() == "plastic":
        Thread(target=hardware.run_sequence, args=(90,), daemon=True).start()
    elif result.lower() == "paper":
        Thread(target=hardware.run_sequence, args=(180,), daemon=True).start()
    else:
        Thread(target=hardware.run_sequence, args=(0,), daemon=True).start()

    hardware.seq_lock.acquire()

    bin_levels = hardware.update_bin_levels()
    bin_levels['label'] = result
    bin_levels['timestamp'] = int(time())

    send_bin_levels_http(bin_levels)

    hardware.seq_lock.release()
    capture_event.clear()
# ================================
# ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ¢â€šÂ¬ DISTANCE MONITOR
# ================================
ultra_history = []
ULTRA_HISTORY_SIZE = 5

def is_ultrasonic_healthy(distance):
    if distance is None:
        return False
    if distance <= 0 or distance > 400:
        return False
    # Only flag as stuck if repeating an invalid-looking value
    if len(ultra_history) >= ULTRA_HISTORY_SIZE:
        if len(set(ultra_history[-ULTRA_HISTORY_SIZE:])) == 1:
            stuck_val = ultra_history[-1]
            if stuck_val <= 0 or stuck_val >= 400:  # only flag boundary values
                return False
    return True

fallback_cap = None
prev_frame = None

def get_frame():
    global fallback_cap

    # Use fallback camera if already started
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
    if fallback_cap:
        print("[FALLBACK] Stopping camera...")
        fallback_cap.release()
        fallback_cap = None
    prev_frame = None

def camera_detects_object():
    global fallback_cap, prev_frame

    if fallback_cap is None:
        return False

    # ret, frame = fallback_cap.read()
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

    return motion > 300000  # tune this

from enum import Enum

class DetectState(Enum):
    ULTRASONIC = 1
    CAMERA_FALLBACK = 2

ULTRASONIC_FAIL_THRESHOLD = 5  # number of consecutive bad readings to trigger fallback
def monitor_detection():
    detect_sensor = hardware.sensors['d']
    state = DetectState.ULTRASONIC
    fail_count = 0
    last_retry_time = 0
    global ultra_history

    while True:
        if time() - last_retry_time > 10:
            resend_offline_logs() # attempt to send any failed logs every 10 seconds
            last_retry_time = time()

        if hardware.seq_lock.locked() or capture_event.is_set():
            sleep(0.1)
            continue

        distance = None

        # =========================
        # READ ULTRASONIC
        # =========================
        try:
            distance = detect_sensor.distance * 100
            ultra_history.append(round(distance, 1))
            if len(ultra_history) > ULTRA_HISTORY_SIZE:
                ultra_history.pop(0)
        except Exception as e:
            print(f"[ERROR] Sensor read failed: {e}")

        healthy = is_ultrasonic_healthy(distance)

        # =========================
        # STATE: ULTRASONIC
        # =========================
        if state == DetectState.ULTRASONIC:
            if not healthy:
                fail_count += 1
                print(f"[⚠️] Ultrasonic unhealthy ({fail_count}/{ULTRASONIC_FAIL_THRESHOLD})")
                if fail_count >= ULTRASONIC_FAIL_THRESHOLD:
                    print("[⚠️] Confirmed failure → switching to CAMERA fallback")
                    start_fallback_camera()
                    state = DetectState.CAMERA_FALLBACK
                    fail_count = 0
            else:
                fail_count = 0  # reset on any good reading

                if distance and 0 < distance <= config.DETECT_THRESHOLD:
                    print(f"\n[DETECT] Ultrasonic: {round(distance,1)} cm")
                    camera_capture()
                    sleep(2)

        # =========================
        # STATE: CAMERA FALLBACK
        # =========================
        elif state == DetectState.CAMERA_FALLBACK:
            if camera_detects_object():
                print("[📷] Fallback camera detected object!")
                camera_capture()
                sleep(2)

            # Try recovery
            if healthy:
                print("[🔄] Ultrasonic recovering...")
                stop_fallback_camera()
                state = DetectState.ULTRASONIC
                fail_count = 0

        sleep(0.1)

# def monitor_detection():
#     """Continuously monitor the 'd' sensor and trigger AI."""
#     detect_sensor = hardware.sensors['d']
    
#     while True:
#         if not hardware.seq_lock.locked() and not capture_event.is_set():
#             try:
#                 distance = detect_sensor.distance * 100  # cm
#                 if 0 < distance <= config.DETECT_THRESHOLD:
#                     print(f"\n[DETECT] Object detected at {round(distance, 1)} cm!")
                    
#                     # Lock the capture event and spin up AI thread
#                     camera_capture()
#                     capture_event.clear()
#                     # Thread(target=camera_capture, daemon=True).start()
                    
#                     sleep(2) # Cooldown
#             except Exception as e:
#                 print(f"[ERROR] Sensor read failed: {e}")
        
#         sleep(0.1)

mqtt_publisher.subscribe_results(handle_inference_result) # get prediction from model <= Pi 2 MQTT
#mqtt_publisher.subscribe_results(process_ai_detection) # for local

# ================================
# ÃƒÂ°Ã…Â¸Ã…Â¡Ã¢â€šÂ¬ START SYSTEM
# ================================
if __name__ == "__main__":
    Thread(target=monitor_detection, daemon=True).start()
    print("Smart Bin System Active with Local AI. Waiting for objects...")
    pause()
