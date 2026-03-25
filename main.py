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

# ================================
# Ã°Å¸Å½Â¯ BUTTON ROUTING
# ================================
def handle_button_press(btn):
    angle = hardware.BUTTON_ANGLES[btn]
    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

# Attach the handlers
hardware.button1.when_pressed = lambda: handle_button_press(hardware.button1)
hardware.button2.when_pressed = lambda: handle_button_press(hardware.button2)
hardware.button3.when_pressed = lambda: handle_button_press(hardware.button3)


# ================================
# Ã°Å¸Â§Â  AI ROUTING THIS IS FOR LOCAL DETECTION IF PI 2 DIED
# ================================
def process_ai_detection():
    # 1. Take picture and get result from ai_vision module
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
    bin_levels['timestamp'] = int(time.time())
    mqtt_publisher.send_bin_levels(bin_levels)  # send via MQTT
    hardware.seq_lock.release()
    print(f"[ACTION] sending data to dashboard")

    # 3. Unlock the event
    capture_event.clear()

# ================================
# CAMERA CAPTURE & MQTT SENDING
# ================================
def camera_capture():
    print("[main.py CAMERA] Capturing image from webcam...")
    capture_event.set() 
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[ERROR] Could not open webcam")
        exit()

    # Capture one frame
    ret, frame = cap.read()

    if not ret:
        print("[ERROR] Failed to capture image")
    else:
        cv2.imwrite("capture.jpg", frame)
        print("Image saved as capture.jpg")
        # Encode image to bytes
        success, buffer = cv2.imencode('.jpg', frame)
        if not success:
            print("[ERROR] Failed to encode image")
            return

        image_bytes = buffer.tobytes()

        # Send via MQTT
        mqtt_publisher.send_image(image_bytes)

    cap.release()

# ================================
# ACTION AFTER RECEIVING MQTT RESULT
# ================================
def handle_inference_result(result):
    print("[AI RESULT] Received:", result)
    result = "paper"
    if result.lower() == "plastic":
        Thread(target=hardware.run_sequence, args=(90,), daemon=True).start()
    elif result.lower() == "paper":
        Thread(target=hardware.run_sequence, args=(180,), daemon=True).start()
    elif result.lower() == "general":
        Thread(target=hardware.run_sequence, args=(0,), daemon=True).start()
    else:
        print("[AI RESULT] Unknown label:", result)
        return  # also add this so it doesn't try to send levels for unknown labels
    
    hardware.seq_lock.acquire()  # wait until servo finishes
    print("[DEBUG] about to call update_bin_levels")
    bin_levels = hardware.update_bin_levels()
    print("[DEBUG] update_bin_levels returned")
    bin_levels['label'] = result
    bin_levels['timestamp'] = int(time()) 
    mqtt_publisher.send_bin_levels(bin_levels) # send to dashboard via MQTT
    hardware.seq_lock.release()
    capture_event.clear()
# ================================
# Ã°Å¸â€˜â‚¬ DISTANCE MONITOR
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
# Ã°Å¸Å¡â‚¬ START SYSTEM
# ================================
if __name__ == "__main__":
    Thread(target=monitor_detection, daemon=True).start()
    print("Smart Bin System Active with Local AI. Waiting for objects...")
    pause()
