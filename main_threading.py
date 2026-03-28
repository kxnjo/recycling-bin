# main.py
from threading import Thread, Event
from signal import pause
from time import sleep, time
from unittest import result
import cv2
import RPi.GPIO as GPIO

import config
import hardware_threading as hardware
import ai_vision_NEW as ai_vision

# Profiling imports added
from profiler import log_profile, now, profile_block, profile_cpu, reset_logs, log_cpu_usage

# MQTT
USE_MQTT = False # temp disable mqtt

if USE_MQTT:
    import mqtt_publisher
else:
    mqtt_publisher = None
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

# limit runs to 15 attempts
MAX_ATTEMPTS = 15

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
    log_cpu_usage("before_update_bin_levels", attempt=current_attempt)

    with profile_block("update_bin_levels", extra={"label": label, "attempt": current_attempt}, attempt=current_attempt):
        # Read the depth sensors
        bin_levels = hardware.update_bin_levels()

    log_cpu_usage("after_update_bin_levels", attempt=current_attempt)

    bin_levels['label'] = label
    bin_levels['timestamp'] = int(time())
    bin_levels['inference_id'] = inference_id
    bin_levels['attempt'] = current_attempt

    print("[BACKGROUND THREAD] Sending data to Manager Pi / Cloud...")
    log_cpu_usage("before_send_http_request", attempt=current_attempt)

    with profile_block("send_http_request", extra={"label": label, "attempt": current_attempt}, attempt=current_attempt):
        send_bin_levels_http(bin_levels)

    log_cpu_usage("after_send_http_request", attempt=current_attempt)
    print("[BACKGROUND THREAD] Update complete.")


# ================================
# AI ROUTING THIS IS FOR LOCAL DETECTION IF PI 2 DIED
# ================================
def process_ai_detection():
    global attempt_no, last_detected_at

    # 1. Take picture and get result from ai_vision module
    ai_vision.init_model()  # Load model before first inference

    log_cpu_usage("before_local_capture_and_infer", attempt=attempt_no)

    with profile_block("local_capture_and_infer", extra={"attempt": attempt_no}, attempt=attempt_no):
        target_bin = ai_vision.capture_and_infer()

    log_cpu_usage("after_local_capture_and_infer", attempt=attempt_no)

    if last_detected_at is not None:
        detect_to_infer_done_ms = (now() - last_detected_at) * 1000
        print(f"[PROFILE] detect_to_infer_done: {detect_to_infer_done_ms:.2f} ms")
        log_profile(
            "detect_to_infer_done",
            detect_to_infer_done_ms,
            {"label": target_bin, "attempt": attempt_no},
            attempt=attempt_no
        )

    # 2. Trigger hardware based on result
    print(f"[ACTION] Routing item to {target_bin.upper()} bin...")
    with profile_block("route_decision", extra={"label": target_bin, "attempt": attempt_no}, attempt=attempt_no):
        if target_bin.lower() == "plastic":
            angle = 90
        elif target_bin.lower() == "paper":
            angle = 180
        else: # general
            angle = 0

    with profile_block("servo_thread_start", extra={"label": target_bin, "target": angle, "attempt": attempt_no}, attempt=attempt_no):
        Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

    # Wait for the hardware sequence to finish
    log_cpu_usage("before_wait_for_servo_finish", attempt=attempt_no)
    sleep(0.1)
    hardware.seq_lock.acquire()
    hardware.seq_lock.release()
    log_cpu_usage("after_wait_for_servo_finish", attempt=attempt_no)

    # 3. Servo is home! Spawn the HTTP/Level checking thread (Use 'local' as inference_id)
    Thread(target=process_levels_and_http, args=(target_bin, "local_fallback", attempt_no), daemon=True).start()

    # Log total pipeline for local fallback
    if last_detected_at is not None:
        total_pipeline_ms = (now() - last_detected_at) * 1000
        log_profile(
            "total_ai_pipeline_main_thread",
            total_pipeline_ms,
            {"label": target_bin, "attempt": attempt_no},
            attempt=attempt_no
        )

    # 4. Unlock the event immediately for the next item
    capture_event.clear()

# ================================
# MARK: STEP 2: CAMERA CAPTURE & MQTT SENDING + AI INFERENCE
# ================================
def camera_capture():
    global current_request_id, attempt_no

    # Prevent concurrent captures
    if capture_event.is_set():
        print("[CAMERA] Already processing, skipping.")
        return

    capture_event.set()
    print("[CAMERA] Capturing image...")
    log_cpu_usage("before_get_frame", attempt=attempt_no)

    with profile_block("get_frame", extra={"attempt": attempt_no}, attempt=attempt_no):
        ret, frame = get_frame()

    log_cpu_usage("after_get_frame", attempt=attempt_no)

    if not ret:
        print("[ERROR] Could not capture frame")
        capture_event.clear()
        return

    with profile_block("encode_image", extra={"attempt": attempt_no}, attempt=attempt_no):
        success, buffer = cv2.imencode('.jpg', frame)

    log_cpu_usage("after_encode_image", attempt=attempt_no)

    if not success:
        print("[ERROR] Failed to encode image")
        capture_event.clear()
        return

    image_bytes = buffer.tobytes()
    current_request_id = str(uuid.uuid4())

    # Reset MQTT inference state
    inference_event.clear()
    inference_result["label"] = None

    inference_id = None
    result = None

    # ======================
    # 1. TRY MQTT (only if enabled)
    # ======================
    if USE_MQTT and mqtt_publisher is not None:
        print("[PIPE] Sending via MQTT, waiting for result...")
        message = {
            "id": current_request_id,
            "image": image_bytes.hex()
        }

        log_cpu_usage("before_mqtt_publish_and_wait", attempt=attempt_no)
        with profile_block("mqtt_publish_and_wait", extra={"attempt": attempt_no}, attempt=attempt_no):
            mqtt_publisher.send_image(json.dumps(message), qos=1)

            if inference_event.wait(timeout=3):  # wait up to 3s for MQTT response
                result = inference_result["label"]
                if result:
                    print(f"[PIPE] MQTT succeeded: {result}")
                    inference_id = "mqtt"
                else:
                    print("[PIPE] MQTT returned empty label, falling through...")
                    result = None
        log_cpu_usage("after_mqtt_publish_and_wait", attempt=attempt_no)
    else:
        print("[PIPE] MQTT disabled, skipping MQTT stage...")

    # ======================
    # 2. TRY LOCAL AI (if MQTT failed or was skipped)
    # ======================
    if result is None:
        print("[PIPE] MQTT failed/skipped, trying Local AI...")
        try:
            log_cpu_usage("before_local_ai_inference", attempt=attempt_no)

            with profile_block("local_ai_inference", extra={"attempt": attempt_no}, attempt=attempt_no):
                result = ai_vision.infer_frame(frame)

            log_cpu_usage("after_local_ai_inference", attempt=attempt_no)

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
    # 3. TRY CLOUD (if local also failed)
    # ======================
    if result is None:
        print("[PIPE] Local AI failed, trying Cloud...")
        try:
            log_cpu_usage("before_cloud_ai_inference", attempt=attempt_no)

            with profile_block("cloud_ai_inference", extra={"attempt": attempt_no}, attempt=attempt_no):
                result = send_image_cloud(image_bytes)

            log_cpu_usage("after_cloud_ai_inference", attempt=attempt_no)

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
    log_cpu_usage("before_handle_final_result", attempt=attempt_no)

    if result:
        handle_final_result(result, inference_id)
    else:
        print("[PIPE] ALL SOURCES FAILED, no action taken.")
        capture_event.clear()
    
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
    

CLOUD_MODEL_URL = "http://54.227.231.254:5000/infer"
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
    
# MARK: STEP 3: MOVE SERVOS
def handle_final_result(result, inference_id):
    global last_detected_at, attempt_no

    print("[FINAL RESULT]:", result)
    print("[FINAL]: DONE BY:", inference_id)

    # Calculate inference time
    if last_detected_at is not None:
        detect_to_infer_done_ms = (now() - last_detected_at) * 1000
        print(f"[PROFILE] detect_to_infer_done: {detect_to_infer_done_ms:.2f} ms")
        log_profile(
            "detect_to_infer_done",
            detect_to_infer_done_ms,
            {"label": result, "attempt": attempt_no, "source": inference_id},
            attempt=attempt_no
        )

    # 1. Start the sorting sequence
    with profile_block("route_decision", extra={"label": result, "attempt": attempt_no}, attempt=attempt_no):
        if result.lower() == "plastic":
            angle = 90
        elif result.lower() == "paper":
            angle = 180
        else:
            angle = 0

    with profile_block("servo_thread_start", extra={"label": result, "target": angle, "attempt": attempt_no}, attempt=attempt_no):
        Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

    # 2. Wait for the hardware sequence to finish (servo drops item and returns home)
    log_cpu_usage("before_wait_for_servo_finish", attempt=attempt_no)
    sleep(0.1) # Brief pause to ensure the hardware thread acquires the lock first
    hardware.seq_lock.acquire()
    hardware.seq_lock.release()
    log_cpu_usage("after_wait_for_servo_finish", attempt=attempt_no)

    # 3. Servo is home! Spawn the HTTP/Level checking thread
    Thread(target=process_levels_and_http, args=(result, inference_id, attempt_no), daemon=True).start()

    # Log total main-thread pipeline time
    if last_detected_at is not None:
        total_pipeline_ms = (now() - last_detected_at) * 1000
        log_profile(
            "total_ai_pipeline_main_thread",
            total_pipeline_ms,
            {"label": result, "attempt": attempt_no},
            attempt=attempt_no
        )

    # 4. Instantly clear the capture event so monitor_detection can find the next item
    capture_event.clear()

    
# ================================
# DISTANCE MONITOR
# ================================
ultra_history = []
ULTRA_HISTORY_SIZE = 5

def is_ultrasonic_healthy(distance):
    """
    Returns True if the ultrasonic sensor appears healthy,
    False if readings look broken / unstable / stuck.
    Uses the recent ultra_history values.
    """
    global ultra_history

    # 1. Reject obvious invalid current reading
    if distance is None:
        return False

    # Adjust these based on your real sensor range
    MIN_VALID_CM = 2
    MAX_VALID_CM = 400

    if not (MIN_VALID_CM <= distance <= MAX_VALID_CM):
        return False

    # 2. Need enough samples before judging health
    if len(ultra_history) < ULTRA_HISTORY_SIZE:
        return True

    recent = ultra_history[-ULTRA_HISTORY_SIZE:]

    # 3. Too many invalid values in recent history
    invalid_count = sum(
        1 for d in recent
        if d is None or d < MIN_VALID_CM or d > MAX_VALID_CM
    )
    if invalid_count >= 3:
        return False

    # Keep only valid values for further checks
    valid_recent = [d for d in recent if d is not None and MIN_VALID_CM <= d <= MAX_VALID_CM]

    if len(valid_recent) < 3:
        return False

    # 4. Check if sensor is wildly unstable
    spread = max(valid_recent) - min(valid_recent)
    if spread > 100:   # very large random jumps = suspicious
        return False

    # 5. Check if sensor is "stuck" on exactly the same value
    # (common symptom of bad / frozen readings)
    rounded = [round(d, 1) for d in valid_recent]
    if len(set(rounded)) == 1:
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

# ================================
# SMALL RAW SENSOR CHECKS
# ================================
@profile_cpu
def ultrasonic_check(attempt=None):
    distance = hardware.read_ultrasonic_sensor('d')
    return distance

@profile_cpu
def camera_trigger_check(attempt=None):
    start_fallback_camera()   # ensure fallback camera is active
    detected = camera_detects_object()
    return detected

# helper to check history
def update_ultra_history(distance):
    global ultra_history
    ultra_history.append(round(distance, 1) if distance is not None else None)
    if len(ultra_history) > ULTRA_HISTORY_SIZE:
        ultra_history.pop(0)

# ================================
# MODE-LEVEL MONITORING CYCLES
# ================================
@profile_cpu
def ultrasonic_monitor_cycle(attempt=None):
    """
    One full ultrasonic monitoring cycle.
    Includes:
    - ultrasonic read
    - history update
    - health check
    - threshold logic
    - sudden drop logic
    - trigger action
    - state transition decision

    Excludes:
    - outer while-loop overhead
    - downstream shared post-trigger pipeline analysis
    """
    global attempt_no, last_detected_at, prev_distance

    next_state = DetectState.ULTRASONIC
    fail_increment = 0
    distance = None
    healthy = False

    try:
        distance = ultrasonic_check(attempt=attempt)
        update_ultra_history(distance)
    except Exception as e:
        print(f"[ERROR] Sensor read failed: {e}")
        distance = None

    healthy = is_ultrasonic_healthy(distance)

    if not healthy:
        fail_increment = 1
    else:
        fail_increment = 0

    print(f"prev distance: {prev_distance}")

    triggered = False
    if distance:
        # 1. Standard threshold trigger
        if 0 < distance <= config.DETECT_THRESHOLD:
            triggered = True

        # 2. Sudden drop trigger
        if prev_distance is not None:
            delta = prev_distance - distance
            if delta > 10:
                print(f"[DEBUG] Sudden drop detected: {round(delta, 1)} cm")
                triggered = True

        if triggered:
            attempt_no += 1
            last_detected_at = now()
            print(f"\n[DETECT] Attempt {attempt_no}: Triggered at {round(distance, 1)} cm")
            log_cpu_usage("before_camera_capture_from_ultrasonic_mode", attempt=attempt_no)
            camera_capture()
            log_cpu_usage("after_camera_capture_from_ultrasonic_mode", attempt=attempt_no)
            sleep(2)  # cooldown

        prev_distance = distance

    return {
        "healthy": healthy,
        "fail_increment": fail_increment,
        "next_state": next_state,
        "distance": distance,
        "triggered": triggered,
    }


@profile_cpu
def camera_monitor_cycle(attempt=None):
    """
    One full camera-based monitoring cycle.
    Includes:
    - camera trigger check
    - optional ultrasonic recovery check
    - health check
    - trigger action
    - recovery decision

    This is closer to 'camera mode cost' than profiling only camera_trigger_check().
    """
    global attempt_no, last_detected_at

    next_state = DetectState.CAMERA_FALLBACK
    triggered = False
    distance = None
    healthy = False

    try:
        triggered = camera_trigger_check(attempt=attempt)
    except Exception as e:
        print(f"[ERROR] Camera trigger check failed: {e}")
        triggered = False

    # Still check ultrasonic for recovery
    try:
        distance = hardware.read_ultrasonic_sensor('d')
        update_ultra_history(distance)
    except Exception:
        distance = None

    healthy = is_ultrasonic_healthy(distance)

    if triggered:
        attempt_no += 1
        last_detected_at = now()
        print(f"[📷] Attempt {attempt_no}: Fallback camera detected object!")
        log_cpu_usage("before_camera_capture_from_camera_fallback_mode", attempt=attempt_no)
        camera_capture()
        log_cpu_usage("after_camera_capture_from_camera_fallback_mode", attempt=attempt_no)
        sleep(2)  # cooldown

    if healthy:
        print("[🔄] Ultrasonic recovering...")
        stop_fallback_camera()
        next_state = DetectState.ULTRASONIC

    return {
        "healthy": healthy,
        "next_state": next_state,
        "distance": distance,
        "triggered": triggered,
    }

# MARK: STEP 1: Monitor detection
def monitor_detection():
    global attempt_no, last_detected_at, ultra_history, prev_distance, SYSTEM_RUNNING

    state = DetectState.ULTRASONIC
    fail_count = 0
    last_retry_time = 0

    while SYSTEM_RUNNING:
        if attempt_no >= MAX_ATTEMPTS:
            print(f"[SYSTEM] Reached max attempts ({MAX_ATTEMPTS}). Stopping run.")
            SYSTEM_RUNNING = False
            break

        if time() - last_retry_time > 10:
            resend_offline_logs()
            last_retry_time = time()

        # do nothing if the system is already busy
        if hardware.seq_lock.locked() or capture_event.is_set():
            sleep(0.1)
            continue

        distance = None

        if state == DetectState.ULTRASONIC:
            result = ultrasonic_monitor_cycle(attempt=attempt_no + 1)

            if not result["healthy"]:
                fail_count += result["fail_increment"]
                print(f"[⚠️] Ultrasonic unhealthy ({fail_count}/{ULTRASONIC_FAIL_THRESHOLD})")

                if fail_count >= ULTRASONIC_FAIL_THRESHOLD:
                    print("[⚠️] Confirmed failure -> switching to CAMERA fallback")
                    start_fallback_camera()
                    state = DetectState.CAMERA_FALLBACK
                    fail_count = 0
            else:
                fail_count = 0  # reset on any good reading

        # =========================
        # STATE: CAMERA FALLBACK
        # =========================
        elif state == DetectState.CAMERA_FALLBACK:
            result = camera_monitor_cycle(attempt=attempt_no + 1)

            if result["next_state"] == DetectState.ULTRASONIC:
                state = DetectState.ULTRASONIC
                fail_count = 0

        sleep(0.1)

# MARK: ULTRASONIC ONLY
@profile_cpu
def ultrasonic_trigger_monitor_cycle(attempt=None):
    """
    One pure ultrasonic-trigger monitoring cycle.
    No fallback logic.
    No camera trigger logic.
    Only:
    - ultrasonic read
    - history update
    - threshold check
    - sudden drop check
    """
    global prev_distance

    distance = None
    triggered = False

    try:
        distance = ultrasonic_check(attempt=attempt)
        update_ultra_history(distance)
    except Exception as e:
        print(f"[ERROR] Ultrasonic read failed: {e}")
        distance = None

    print(f"prev distance: {prev_distance}")

    if distance:
        # 1. Standard threshold trigger
        if 0 < distance <= config.DETECT_THRESHOLD:
            triggered = True

        # 2. Sudden drop trigger
        if prev_distance is not None:
            delta = prev_distance - distance
            if delta > 10:
                print(f"[DEBUG] Sudden drop detected: {round(delta, 1)} cm")
                triggered = True

        prev_distance = distance

    return {
        "triggered": triggered,
        "distance": distance,
    }

def monitor_detection_ultrasonic_only():
    global attempt_no, last_detected_at, SYSTEM_RUNNING

    while SYSTEM_RUNNING:
        if attempt_no >= MAX_ATTEMPTS:
            print(f"[SYSTEM] Reached max attempts ({MAX_ATTEMPTS}). Stopping run.")
            SYSTEM_RUNNING = False
            break

        # do nothing if busy
        if hardware.seq_lock.locked() or capture_event.is_set():
            sleep(0.1)
            continue

        result = ultrasonic_trigger_monitor_cycle(attempt=attempt_no + 1)

        if result["triggered"]:
            attempt_no += 1
            last_detected_at = now()

            distance = result["distance"]
            if distance is not None:
                print(f"\n[DETECT-ULTRA] Attempt {attempt_no}: Triggered at {round(distance, 1)} cm")
            else:
                print(f"\n[DETECT-ULTRA] Attempt {attempt_no}: Triggered")

            log_cpu_usage("before_camera_capture_ultrasonic_only", attempt=attempt_no)
            camera_capture()
            log_cpu_usage("after_camera_capture_ultrasonic_only", attempt=attempt_no)

            sleep(2)

        sleep(0.1)

# MARK: CAMERA ONLY
@profile_cpu
def pure_camera_monitor_cycle(attempt=None):
    try:
        triggered = camera_trigger_check(attempt=attempt)
    except Exception as e:
        print(f"[ERROR] Camera trigger check failed: {e}")
        triggered = False

    return {"triggered": triggered}

def monitor_detection_camera_only():
    global attempt_no, last_detected_at, SYSTEM_RUNNING

    while SYSTEM_RUNNING:
        if attempt_no >= MAX_ATTEMPTS:
            print(f"[SYSTEM] Reached max attempts ({MAX_ATTEMPTS}). Stopping run.")
            SYSTEM_RUNNING = False
            break

        if hardware.seq_lock.locked() or capture_event.is_set():
            sleep(0.1)
            continue

        result = pure_camera_monitor_cycle(attempt=attempt_no + 1)

        if result["triggered"]:
            attempt_no += 1
            last_detected_at = now()
            print(f"[📷] Attempt {attempt_no}: Camera-only mode detected object!")

            log_cpu_usage("before_camera_capture_camera_only", attempt=attempt_no)
            camera_capture()
            log_cpu_usage("after_camera_capture_camera_only", attempt=attempt_no)

            sleep(2)

        sleep(0.1)

if USE_MQTT and mqtt_publisher is not None:
    mqtt_publisher.subscribe_results(handle_inference_result) # get prediction from model <= Pi 2 MQTT
    #mqtt_publisher.subscribe_results(process_ai_detection) # for local

# ================================
# START SYSTEM
# ================================
if __name__ == "__main__":
    try:
        # setup logs
        reset_logs()

        # 1. Initialize the new sensor logic
        hardware.setup_ultrasonic()
        
        # 2. Start your threads
        Thread(target=monitor_detection_ultrasonic_only, daemon=True).start()
        print("Smart Bin System Active with Local AI. Waiting for objects...")
        pause()
        
    except KeyboardInterrupt:
        print("\n[SYSTEM] Shutting down cleanly...")
        SYSTEM_RUNNING = False
        sleep(0.5) 
    finally:
        # 3. ALWAYS clean up the GPIO pins on exit
        GPIO.cleanup()