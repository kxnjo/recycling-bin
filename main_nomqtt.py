# main.py
from threading import Thread, Event
from signal import pause
from time import sleep, time
import cv2

import config
import hardware
import ai_vision

capture_event = Event()

# ================================
# 🎯 BUTTON ROUTING
# ================================
def handle_button_press(btn):
    angle = hardware.BUTTON_ANGLES[btn]
    Thread(target=hardware.run_sequence, args=(angle,), daemon=True).start()

# Attach the handlers
hardware.button1.when_pressed = lambda: handle_button_press(hardware.button1)
hardware.button2.when_pressed = lambda: handle_button_press(hardware.button2)
hardware.button3.when_pressed = lambda: handle_button_press(hardware.button3)


# ================================
# 🧠 AI ROUTING (Fully Local)
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

    # 3. Update levels after sorting
    hardware.seq_lock.acquire()  # wait until servo finishes
    bin_levels = hardware.update_bin_levels()
    bin_levels['label'] = target_bin
    bin_levels['timestamp'] = int(time.time())
    hardware.seq_lock.release()

    # 4. Unlock the event
    capture_event.clear()


# ================================
# 👀 DISTANCE MONITOR
# ================================
def monitor_detection():
    """Continuously monitor the 'd' sensor and trigger Local AI."""
    detect_sensor = hardware.sensors['d']
    
    while True:
        if not hardware.seq_lock.locked() and not capture_event.is_set():
            try:
                distance = detect_sensor.distance * 100  # cm
                if 0 < distance <= config.DETECT_THRESHOLD:
                    print(f"\n[DETECT] Object detected at {round(distance, 1)} cm!")
                    
                    # Lock the capture event and spin up the Local AI thread
                    capture_event.set()
                    Thread(target=process_ai_detection, daemon=True).start()
                    
                    sleep(2) # Cooldown
            except Exception as e:
                print(f"[ERROR] Sensor read failed: {e}")
        
        sleep(0.1)

# ================================
# 🚀 START SYSTEM
# ================================
if __name__ == "__main__":
    Thread(target=monitor_detection, daemon=True).start()
    print("Smart Bin System Active with Local AI. Waiting for objects...")
    pause()