# main.py
from threading import Thread, Event
from signal import pause
from time import sleep, time
import cv2
import RPi.GPIO as GPIO

import config
import hardware_threading as hardware
import ai_vision

# MQTT
try:
    import mqtt_publisher
    USE_MQTT = True
    capture_event = Event()
    print("[MQTT] mqtt_publisher loaded successfully.")
except ImportError:
    mqtt_publisher = None
    USE_MQTT = False
    print("[MQTT] mqtt_publisher not found. MQTT disabled.")

# HTTP
import requests

#MQTT QoS Test
import uuid
import json

import http_controller

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


# ======= THREADING FUNCTIONS =======
def process_levels_and_http(label, inference_id):
    """
    Runs in the background to calculate levels and send HTTP/MQTT updates
    while the main thread goes back to detecting the next item.
    """
    print("[BACKGROUND THREAD] Calculating bin levels...")
    
    # Read the depth sensors
    bin_levels = hardware.update_bin_levels()
    bin_levels['label'] = label
    bin_levels['timestamp'] = int(time())
    bin_levels['inference_id'] = inference_id
    
    print("[BACKGROUND THREAD] Sending data to Manager Pi / Cloud...")
    http_controller.send_bin_levels_http(bin_levels)
    print("[BACKGROUND THREAD] Update complete.")


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
    if USE_MQTT and mqtt_publisher is not None:
        print("[PIPE] Sending via MQTT, waiting for result...")
        message = {
            "id": current_request_id,
            "image": image_bytes.hex()
        }

        try:
            mqtt_publisher.send_image(json.dumps(message), qos=1)

            if inference_event.wait(timeout=3):
                result = inference_result["label"]
                if result:
                    print(f"[PIPE] MQTT succeeded: {result}")
                    inference_id = "mqtt"
                else:
                    print("[PIPE] MQTT returned empty label, falling through...")
                    result = None
            else:
                print("[PIPE] MQTT timeout, falling back...")
        except Exception as e:
            print(f"[PIPE] MQTT failed: {e}")
            result = None
    else:
        print("[PIPE] MQTT disabled, skipping MQTT stage...")

    # ======================
    # 2. TRY LOCAL AI (only if MQTT failed)
    # ======================
    if result is None:
        print("[PIPE] MQTT failed/skipped, trying Local AI...")
        try:
            result = ai_vision.infer(frame)  # assumed blocking
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
        print("[PIPE] Local AI failed, trying Cloud...")
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
    
    # 1. Start the sorting sequence
    if result.lower() == "plastic":
        angle = 90
    elif result.lower() == "paper":
        angle = 180
    else:
        angle = 0
        
    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

    # 2. Wait for the hardware sequence to finish (servo drops item and returns home)
    sleep(0.1) # Brief pause to ensure the hardware thread acquires the lock first
    hardware.seq_lock.acquire() 
    try:
        hardware.seq_lock.release()
    except:
        pass
    
    # 3. Servo is home! Spawn the HTTP/Level checking thread
    Thread(target=process_levels_and_http, args=(result, inference_id), daemon=True).start()

    # 4. Instantly clear the capture event so monitor_detection can find the next item
    capture_event.clear()

    
# ================================
# DISTANCE MONITOR
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

    # Use fallback camera if already started
    if fallback_cap is not None:
        cap = fallback_cap
        using_fallback = True
    else:
        cap = cv2.VideoCapture(0)
        using_fallback = False

        # do optimization on the camera frames
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        # Optional manual camera settings
        # Uncomment these only if auto exposure is causing bad frames
        # Note: exact behavior depends on webcam/driver
        # cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)   # sometimes manual mode
        # cap.set(cv2.CAP_PROP_EXPOSURE, -6)       # try -4 to -8
        # cap.set(cv2.CAP_PROP_GAIN, 0)

    if not cap.isOpened():
        print("[ERROR] Camera not available")
        return False, None

    # If newly opened camera, discard first few unstable frames
    if not using_fallback:
        for _ in range(8):
            ret, frame = cap.read()
            if not ret:
                print("[WARNING] Failed to read warm-up frame")

    # Actual frame to use
    ret, frame = cap.read()

    # make sure that everythihng is captured properly
    if not ret or frame is None:
        print("[ERROR] Failed to capture frame")
        if fallback_cap is None:
            cap.release()
        return False, None
    
    # Optional brightness debug check
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    avg_brightness = gray.mean()
    print(f"[CAMERA] Average brightness: {avg_brightness:.2f}")

    if avg_brightness < 25:
        print("[WARNING] Captured frame is very dark")

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
                state = DetectState.ULTRASONIC
                fail_count = 0

        sleep(0.1)

if USE_MQTT and mqtt_publisher is not None:
    mqtt_publisher.subscribe_results(handle_inference_result) # get prediction from model <= Pi 2 MQTT

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