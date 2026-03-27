# main.py
from threading import Thread, Event
from signal import pause
from time import sleep, time
from unittest import result
import cv2
import RPi.GPIO as GPIO

import config
import hardware_threading as hardware
import ai_vision

# Profiling imports added
from profiler import log_profile, now, profile_block

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
SYSTEM_RUNNING = True

# Profiling Globals
attempt_no = 0
last_detected_at = None

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
def process_levels_and_http(label, inference_id, current_attempt):
    """
    Runs in the background to calculate levels and send HTTP/MQTT updates
    while the main thread goes back to detecting the next item.
    """
    print("[BACKGROUND THREAD] Calculating bin levels...")
    
    with profile_block("update_bin_levels", extra={"label": label, "attempt": current_attempt}):
        # Read the depth sensors
        bin_levels = hardware.update_bin_levels()
        
    bin_levels['label'] = label
    bin_levels['timestamp'] = int(time())
    bin_levels['inference_id'] = inference_id
    bin_levels['attempt'] = current_attempt
    
    print("[BACKGROUND THREAD] Sending data to Manager Pi / Cloud...")
    with profile_block("send_http_request", extra={"label": label, "attempt": current_attempt}):
        send_bin_levels_http(bin_levels)
        
    print("[BACKGROUND THREAD] Update complete.")


# ================================
# AI ROUTING THIS IS FOR LOCAL DETECTION IF PI 2 DIED
# ================================
def process_ai_detection():
    global attempt_no, last_detected_at
    
    # 1. Take picture and get result from ai_vision module
    ai_vision.init_model()  # Load model before first inference
    
    with profile_block("local_capture_and_infer", extra={"attempt": attempt_no}):
        target_bin = ai_vision.capture_and_infer()

    if last_detected_at is not None:
        detect_to_infer_done_ms = (now() - last_detected_at) * 1000
        print(f"[PROFILE] detect_to_infer_done: {detect_to_infer_done_ms:.2f} ms")
        log_profile("detect_to_infer_done", detect_to_infer_done_ms, {"label": target_bin, "attempt": attempt_no})

    # 2. Trigger hardware based on result
    print(f"[ACTION] Routing item to {target_bin.upper()} bin...")
    with profile_block("route_decision", extra={"label": target_bin, "attempt": attempt_no}):
        if target_bin.lower() == "plastic":
            angle = 90
        elif target_bin.lower() == "paper":
            angle = 180
        else: # general
            angle = 0
            
    with profile_block("servo_thread_start", extra={"label": target_bin, "target": angle, "attempt": attempt_no}):
        Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

    # Wait for the hardware sequence to finish
    sleep(0.1)
    hardware.seq_lock.acquire()  
    hardware.seq_lock.release()
    
    # 3. Servo is home! Spawn the HTTP/Level checking thread (Use 'local' as inference_id)
    Thread(target=process_levels_and_http, args=(target_bin, "local_fallback", attempt_no), daemon=True).start()

    # Log total pipeline for local fallback
    if last_detected_at is not None:
        total_pipeline_ms = (now() - last_detected_at) * 1000
        log_profile("total_ai_pipeline_main_thread", total_pipeline_ms, {"label": target_bin, "attempt": attempt_no})

    # 4. Unlock the event immediately for the next item
    capture_event.clear()

# ================================
# CAMERA CAPTURE & MQTT SENDING
# ================================
def camera_capture():
    global current_request_id, attempt_no
    
    # Prevent concurrent captures
    if capture_event.is_set():
        print("[CAMERA] Already processing, skipping.")
        return
    
    capture_event.set()
    print("[CAMERA] Capturing image...")

    with profile_block("get_frame", extra={"attempt": attempt_no}):
        ret, frame = get_frame()
        
    if not ret:
        print("[ERROR] Could not capture frame")
        capture_event.clear()
        return

    with profile_block("encode_image", extra={"attempt": attempt_no}):
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
    
    with profile_block("mqtt_publish_and_wait", extra={"attempt": attempt_no}):
        mqtt_publisher.send_image(json.dumps(message), qos=1)

        if inference_event.wait(timeout=3):  # block here until MQTT responds or times out
            result = inference_result["label"]
            if result:
                print(f"[PIPE] MQTT succeeded: {result}")
                inference_id = "mqtt"
            else:
                print("[PIPE] MQTT returned empty label, falling through...")
                result = None

    # ======================
    # 2. TRY LOCAL AI (only if MQTT failed)
    # ======================
    if result is None:
        print("[PIPE] MQTT failed ? trying Local AI...")
        try:
            with profile_block("local_ai_inference", extra={"attempt": attempt_no}):
                result = ai_vision.infer_frame(frame)  # assumed blocking
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
            with profile_block("cloud_ai_inference", extra={"attempt": attempt_no}):
                result = send_image_cloud(image_bytes)
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
    

CLOUD_MODEL_URL = "http://3.93.218.220:5000/infer"
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
    
def handle_final_result(result, inference_id):
    global last_detected_at, attempt_no
    
    print("[FINAL RESULT]:", result)
    print("[FINAL]: DONE BY:", inference_id)
    
    # Calculate inference time
    if last_detected_at is not None:
        detect_to_infer_done_ms = (now() - last_detected_at) * 1000
        print(f"[PROFILE] detect_to_infer_done: {detect_to_infer_done_ms:.2f} ms")
        log_profile("detect_to_infer_done", detect_to_infer_done_ms, {"label": result, "attempt": attempt_no, "source": inference_id})
    
    # 1. Start the sorting sequence
    with profile_block("route_decision", extra={"label": result, "attempt": attempt_no}):
        if result.lower() == "plastic":
            angle = 90
        elif result.lower() == "paper":
            angle = 180
        else:
            angle = 0
            
    with profile_block("servo_thread_start", extra={"label": result, "target": angle, "attempt": attempt_no}):
        Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

    # 2. Wait for the hardware sequence to finish (servo drops item and returns home)
    sleep(0.1) # Brief pause to ensure the hardware thread acquires the lock first
    hardware.seq_lock.acquire() 
    hardware.seq_lock.release() 
    
    # 3. Servo is home! Spawn the HTTP/Level checking thread
    Thread(target=process_levels_and_http, args=(result, inference_id, attempt_no), daemon=True).start()

    # Log total main-thread pipeline time
    if last_detected_at is not None:
        total_pipeline_ms = (now() - last_detected_at) * 1000
        log_profile("total_ai_pipeline_main_thread", total_pipeline_ms, {"label": result, "attempt": attempt_no})

    # 4. Instantly clear the capture event so monitor_detection can find the next item
    capture_event.clear()

    
# ================================
# DISTANCE MONITOR
# ================================
ultra_history = []
ULTRA_HISTORY_SIZE = 5

def is_ultrasonic_healthy(distance):
    # Update logic here to properly return True/False based on your needs
    return False

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
    global attempt_no, last_detected_at, ultra_history
    
    state = DetectState.CAMERA_FALLBACK
    fail_count = 0
    last_retry_time = 0

    while SYSTEM_RUNNING:
        if time() - last_retry_time > 10:
            resend_offline_logs() # attempt to send any failed logs every 10 seconds
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

        # Force False right now based on your current setup for testing fallback
        healthy = False

        # =========================
        # STATE: ULTRASONIC
        # =========================
        if state == DetectState.ULTRASONIC:
            if not healthy:
                fail_count += 1
                print(f"[⚠️] Ultrasonic unhealthy ({fail_count}/{ULTRASONIC_FAIL_THRESHOLD})")
                if fail_count >= ULTRASONIC_FAIL_THRESHOLD:
                    print("[⚠️] Confirmed failure -> switching to CAMERA fallback")
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
                    attempt_no += 1
                    last_detected_at = now()
                    print(f"\n[DETECT] Attempt {attempt_no}: Triggered at {round(distance,1)} cm")
                    camera_capture()
                    sleep(2)
                
                prev_distance = distance

        # =========================
        # STATE: CAMERA FALLBACK
        # =========================
        elif state == DetectState.CAMERA_FALLBACK:
            start_fallback_camera() # Ensure camera is started
            
            if camera_detects_object():
                attempt_no += 1
                last_detected_at = now()
                print(f"[📷] Attempt {attempt_no}: Fallback camera detected object!")
                camera_capture()
                sleep(2)

            # Try recovery
            if healthy:
                print("[🔄] Ultrasonic recovering...")
                stop_fallback_camera()
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