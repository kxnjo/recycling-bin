# hardware.py
import statistics
from time import sleep, time
from threading import Lock
from gpiozero import Button, AngularServo 
import config
import RPi.GPIO as GPIO

# Lock prevents re-triggering while busy
seq_lock = Lock()

# --- Initialize Buttons ---
button1 = Button(config.BTN_1_PIN, pull_up=True)
button2 = Button(config.BTN_2_PIN, pull_up=True)
button3 = Button(config.BTN_3_PIN, pull_up=True)

BUTTON_ANGLES = {button1: 0, button2: 90, button3: 180}

# --- Initialize Servos ---
servo = AngularServo(
    config.SERVO_MAIN_PIN, 
    min_angle=0, 
    max_angle=180, 
    min_pulse_width=0.0005, 
    max_pulse_width=0.0025
)

servo2 = AngularServo(
    config.SERVO_LID_PIN, 
    min_angle=0, 
    max_angle=90,             
    min_pulse_width=0.0010,   
    max_pulse_width=0.0020    
)

# Set home positions
servo.angle = config.MAIN_HOME
servo2.angle = config.FS90_HOME

def setup_ultrasonic():
    """Initializes all ultrasonic pins identically."""
    GPIO.setmode(GPIO.BCM)
    for key, config in config.BIN_CONFIGS.items():
        GPIO.setup(config['trigger'], GPIO.OUT)
        GPIO.setup(config['echo'], GPIO.IN)
        GPIO.output(config['trigger'], False) # Start LOW
    sleep(0.5) # Let sensors settle
    print("[SYSTEM] Ultrasonic hardware initialized.")


def read_ultrasonic_sensor(sensor_key):
    """Retrieves distance (in cm) for a specific sensor safely."""
    if sensor_key not in config.BIN_CONFIGS: return None
    trig, echo = config.BIN_CONFIGS[sensor_key]['trigger'], config.BIN_CONFIGS[sensor_key]['echo']

    # 10us Pulse
    GPIO.output(trig, True)
    sleep(0.00001)
    GPIO.output(trig, False)

    start_time = time()
    pulse_start, pulse_end = None, None

    # Wait for echo START (0.1s timeout)
    while GPIO.input(echo) == 0:
        pulse_start = time()
        if pulse_start - start_time > 0.1: return None

    # Wait for echo END (0.1s timeout)
    while GPIO.input(echo) == 1:
        pulse_end = time()
        if pulse_end - pulse_start > 0.1: return None

    if pulse_start and pulse_end:
        return ((pulse_end - pulse_start) * 34300) / 2
    return None


def update_bin_levels():
    """Triggers a sequential burst for all 3 interior bins."""
    print("📊 Measuring bin fill levels...")
    results = {}
    
    for key in ['a', 'b', 'c']:
        readings = []
        
        for _ in range(5):
            val = read_ultrasonic_sensor(key)
            if val is not None and 2.0 <= val <= 100.0:
                readings.append(val)
            sleep(0.06) 
            
        median_val = round(statistics.median(readings), 2) if readings else None
        results[key] = median_val
        label = config.BIN_CONFIGS[key]['label']
        print(f"  - Bin {key.upper()} ({label}): {median_val} cm")
        sleep(0.1)

    return results

def run_sequence(target_angle):
    if not seq_lock.acquire(blocking=False):
        return  # ignore presses while running

    try:
        print(f"Moving main servo to {target_angle}")
        servo.angle = target_angle
        sleep(0.8)

        print("Opening lid")
        servo2.angle = config.FS90_TARGET
        sleep(2) 

        print("Closing lid")
        servo2.angle = config.FS90_HOME
        sleep(0.5)

        print("Returning home...")
        servo.angle = config.MAIN_HOME
        sleep(1.0) 

        # update_bin_levels() changing to call in main.py after receiving AI result

    finally:
        seq_lock.release()
        print("✅ Sequence Complete. Ready for next item.")