# main.py
from threading import Thread, Event
from signal import pause
from time import sleep, time
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

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] Could not open webcam")
        capture_event.clear()
        return

    ret, frame = cap.read()
    cap.release()

    if not ret:
        print("[ERROR] Failed to capture image")
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

    # # ðŸ“¡ Send with QoS 1
    # mqtt_publisher.send_image(json.dumps(message), qos=1)

    # print("[WAIT] Waiting for Pi 2 result...")
    # start = time()

    # # â³ WAIT (timeout)
    # if inference_event.wait(timeout=0.2):
    #     latency = time() - start
    #     print(f"[SUCCESS] Result received in {latency:.2f}s")
    #     result = inference_result["label"]
    # else:
    #     print("[FALLBACK] Timeout â†’ running local AI")
    #     ai_vision.init_model()
    #     current_request_id = None
    #     result = ai_vision.capture_and_infer()

    # # ðŸš€ Handle result
    # handle_final_result(result)

    send_image_cloud(image_bytes)
# ================================
# SEND HTTP REQUEST I THINK
# ================================
API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"

def send_bin_levels_http(bin_levels):
    try:
        response = requests.post(API_URL, json=bin_levels)
        print(f"[HTTP] Sent bin levels, status: {response.status_code}")
    except Exception as e:
        print(f"[HTTP] Failed to send bin levels: {e}")

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
# def handle_inference_result(result):
#     print("[AI RESULT] Received:", result)

#     if result.lower() == "plastic":
#         Thread(target=hardware.run_sequence, args=(90,), daemon=True).start()
#     elif result.lower() == "paper":
#         Thread(target=hardware.run_sequence, args=(180,), daemon=True).start()
#     elif result.lower() == "general":
#         Thread(target=hardware.run_sequence, args=(0,), daemon=True).start()
#     else:
#         print("[AI RESULT] Unknown label:", result)
#         return  # also add this so it doesn't try to send levels for unknown labels

#     hardware.seq_lock.acquire()  # wait until servo finishes
#     print("[DEBUG] about to call update_bin_levels")
#     bin_levels = hardware.update_bin_levels()
#     print("[DEBUG] update_bin_levels returned")
#     bin_levels['label'] = result
#     bin_levels['timestamp'] = int(time())
#     #mqtt_publisher.send_bin_levels(bin_levels)
#     send_bin_levels_http(bin_levels)
#     hardware.seq_lock.release()
#     capture_event.clear()
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
def monitor_detection():
    """Continuously monitor the 'd' sensor and trigger AI."""
    detect_sensor = hardware.sensors['d']
    
    while True:
        if not hardware.seq_lock.locked() and not capture_event.is_set():
            try:
                distance = detect_sensor.distance * 100  # cm
                if 0 < distance <= config.DETECT_THRESHOLD:
                    print(f"\n[DETECT] Object detected at {round(distance, 1)} cm!")
                    
                    # Lock the capture event and spin up AI thread
                    camera_capture()
                    capture_event.clear()
                    # Thread(target=camera_capture, daemon=True).start()
                    
                    sleep(2) # Cooldown
            except Exception as e:
                print(f"[ERROR] Sensor read failed: {e}")
        
        sleep(0.1)

mqtt_publisher.subscribe_results(handle_inference_result) # get prediction from model <= Pi 2 MQTT
#mqtt_publisher.subscribe_results(process_ai_detection) # for local

# ================================
# ÃƒÂ°Ã…Â¸Ã…Â¡Ã¢â€šÂ¬ START SYSTEM
# ================================
if __name__ == "__main__":
    Thread(target=monitor_detection, daemon=True).start()
    print("Smart Bin System Active with Local AI. Waiting for objects...")
    pause()
