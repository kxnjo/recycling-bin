# main.py
from threading import Thread, Event
from signal import pause
from time import sleep, time
from unittest import result
import cv2
import RPi.GPIO as GPIO

import config
import hardware
import ai_vision
# MQTT
import mqtt_publisher
capture_event = Event()
# HTTP
import requests
import http_controller
#MQTT QoS Test
import uuid
import json

inference_event = Event()
inference_result = {"label": None}
current_request_id = None  
SYSTEM_RUNNING = True

# ================================
# BUTTON ROUTING
# ================================
def handle_button_press(btn):
    angle = hardware.BUTTON_ANGLES[btn]
    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

# Attach the handlers
hardware.button1.when_pressed = lambda: handle_button_press(hardware.button1)
hardware.button2.when_pressed = lambda: handle_button_press(hardware.button2)
hardware.button3.when_pressed = lambda: handle_button_press(hardware.button3)

# ================================
# CAMERA CAPTURE & MQTT SENDING
# ================================
def camera_capture():
    global current_request_id
    
    # Prevent concurrent captures
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

    success, buffer = cv2.imencode('.jpg', frame)
    if not success:
        print("[ERROR] Failed to encode image")
        capture_event.clear()
        return

    image_bytes = buffer.tobytes()
    current_request_id = str(uuid.uuid4())

    # Reset MQTT inference state
    inference_event.clear()
    inference_result["label"] = None
    
    inference_id = None # to check who did inference
    result = None

    # ======================
    # 1. TRY MQTT (blocking wait)
    # ======================
    print("[PIPE] Sending via MQTT, waiting for result...")
    message = {
        "id": current_request_id,
        "image": image_bytes.hex()
    }
    # mqtt_publisher.send_image(json.dumps(message), qos=1)

    # if inference_event.wait(timeout=0.1):  # block here until MQTT responds or times out
    #     result = inference_result["label"]
    #     if result:
    #         print(f"[PIPE] MQTT succeeded: {result}")
    #         inference_id = "mqtt"
    #     else:
    #         print("[PIPE] MQTT returned empty label, falling through...")
    #         result = None
    mqtt_ok = mqtt_publisher.send_image(json.dumps(message), qos=1)

    if mqtt_ok and inference_event.wait(timeout=0.3):
        result = inference_result["label"]
        if result:
            print(f"[PIPE] MQTT succeeded: {result}")
            inference_id = "mqtt"
        else:
            result = None
    else:
        print("[PIPE] MQTT unavailable → fallback")

    # ======================
    # 2. TRY LOCAL AI (only if MQTT failed)
    # ======================
    if result is None:
        print("[PIPE] MQTT failed ? trying Local AI...")
        try:
            #result = ai_vision.infer_frame(frame)  # assumed blocking
            result = ai_vision.infer(frame=frame) # new version that takes frame directly
            if result:
                print(f"[PIPE] Local AI succeeded: {result}")
                inference_id = "local"
            else:
                print("[PIPE] Local AI returned None, falling through...")
                result = None
        except Exception as e:
            print(f"[PIPE] Local AI failed: {e}")
            result = None

    # ======================
    # 3. TRY CLOUD (only if local also failed)
    # ======================
    if result is None:
        print("[PIPE] Local AI failed ? trying Cloud...")
        try:
            result = http_controller.send_image_cloud(image_bytes)
            if result:
                print(f"[PIPE] Cloud succeeded: {result}")
                inference_id = "cloud"
            else:
                print("[PIPE] Cloud returned None.")
        except Exception as e:
            print(f"[PIPE] Cloud failed: {e}")
            result = None

    # ======================
    # 4. HANDLE RESULT OR GIVE UP
    # ======================
    if result:
        handle_final_result(result, inference_id)
    else:
        print("[PIPE] ALL SOURCES FAILED, no action taken.")
        capture_event.clear()  # handle_final_result also clears it, so only clear here on total failure


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
    
def handle_final_result(result, inference_id):
    print("[FINAL RESULT]:", result)
    print("[FINAL]: DONE BY:", inference_id)
    if result.lower() == "plastic":
        Thread(target=hardware.run_sequence, args=(90,), daemon=True).start()
    elif result.lower() == "paper":
        Thread(target=hardware.run_sequence, args=(180,), daemon=True).start()
    else:
        Thread(target=hardware.run_sequence, args=(0,), daemon=True).start()

    hardware.seq_lock.acquire()
    try:
        bin_levels = hardware.update_bin_levels()
        bin_levels['label'] = result
        bin_levels['timestamp'] = int(time())
        bin_levels['inference_id'] = inference_id
        http_controller.send_bin_levels_http(bin_levels)
    finally:
        hardware.seq_lock.release()  # ALWAYS releases, even if exception thrown
        capture_event.clear()
# ================================
# ÃÆÃÂ°Ãâ€¦ÃÂ¸ÃÂ¢Ã¢â€Â¬ÃÅÃÂ¢Ã¢â¬Å¡ÃÂ¬ DISTANCE MONITOR
# ================================
ultra_history = []
ULTRA_HISTORY_SIZE = 5

def is_ultrasonic_healthy(distance):
    # if distance is None:
        # return False
    # if distance <= 0 or distance > 400:
        # return False
    # # Only flag as stuck if repeating an invalid-looking value
    # if len(ultra_history) >= ULTRA_HISTORY_SIZE:
        # if len(set(ultra_history[-ULTRA_HISTORY_SIZE:])) == 1:
            # stuck_val = ultra_history[-1]
            # if stuck_val <= 0 or stuck_val >= 400:  # only flag boundary values
                # return False
    return True

fallback_cap = None
prev_frame = None

def get_frame():
    global fallback_cap

    if fallback_cap is not None:
        # Use persistent camera
        ret, frame = fallback_cap.read()
        return ret, frame
    else:
        # Create a one-shot capture
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[ERROR] Camera not available")
            return False, None
        ret, frame = cap.read()
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
prev_distance = None

def monitor_detection():
    state = DetectState.ULTRASONIC

    fail_count = 0
    last_retry_time = 0
    global ultra_history

    while SYSTEM_RUNNING:
        if time() - last_retry_time > 10:
            http_controller.resend_offline_logs() # attempt to send any failed logs every 10 seconds
            last_retry_time = time()

        if hardware.seq_lock.locked() or capture_event.is_set():
            sleep(0.1)
            continue

        distance = None
        # =========================
        # READ ULTRASONIC (UPDATED)
        # =========================
        try:
            distance = hardware.read_ultrasonic_sensor('d') 
            
            if distance is not None:
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
                print(f"[â ï¸] Ultrasonic unhealthy ({fail_count}/{ULTRASONIC_FAIL_THRESHOLD})")
                if fail_count >= ULTRASONIC_FAIL_THRESHOLD:
                    print("[â ï¸] Confirmed failure â switching to CAMERA fallback")
                    start_fallback_camera()
                    state = DetectState.CAMERA_FALLBACK
                    fail_count = 0
            else:
                fail_count = 0  # reset on any good reading

            global prev_distance
            print(f"prev distance: {prev_distance}")
            if distance:
                triggered = False

                # 1. Standard threshold (Is an object physically close?)
                if 0 < distance <= config.DETECT_THRESHOLD:
                    triggered = True

                # 2. Sudden drop detection (Did an object suddenly appear?)
                if prev_distance is not None:
                    delta = prev_distance - distance
                            
                    # Trigger only if distance SHRINKS by more than 10cm instantly
                    if delta > 10: 
                        print(f"[DEBUG] Sudden drop detected: {round(delta,1)} cm")
                        triggered = True

                if triggered:
                    print(f"\n[DETECT] Triggered at {round(distance,1)} cm")
                    camera_capture()
                    sleep(2)
                
                prev_distance = distance

        # =========================
        # STATE: CAMERA FALLBACK
        # =========================
        elif state == DetectState.CAMERA_FALLBACK:
            if camera_detects_object():
                print("[ð·] Fallback camera detected object!")
                camera_capture()
                sleep(2)

            # Try recovery
            if healthy:
                print("[ð] Ultrasonic recovering...")
                stop_fallback_camera()
                prev_frame = None
                state = DetectState.ULTRASONIC
                fail_count = 0

        sleep(0.1)

mqtt_publisher.subscribe_results(handle_inference_result) # get prediction from model <= Pi 2 MQTT
#mqtt_publisher.subscribe_results(process_ai_detection) # for local

# ================================
# START SYSTEM
# ================================
if __name__ == "__main__":
    try:
        # 1. Initialize the new sensor logic
        hardware.setup_ultrasonic()
        
        # 2. Start your threads
        Thread(target=monitor_detection, daemon=True).start()
        print("Smart Bin System Active with Local AI. Waiting for objects...")
        pause()
        
    except KeyboardInterrupt:
        print("\n[SYSTEM] Shutting down cleanly...")
        SYSTEM_RUNNING = False
        sleep(0.5) 
    finally:
        # 3. ALWAYS clean up the GPIO pins on exit
        GPIO.cleanup()