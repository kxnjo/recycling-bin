# hardware.py
import statistics
from time import sleep
from threading import Lock
from gpiozero import Button, AngularServo, DistanceSensor
import config

# Lock prevents re-triggering while busy
seq_lock = Lock()

# --- Initialize Sensors ---
sensors = {
    key: DistanceSensor(echo=cfg['echo'], trigger=cfg['trigger']) 
    for key, cfg in config.BIN_CONFIGS.items()
}

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

def update_bin_levels():
    """Triggers a sequential burst for all 3 bins."""
    print("📊 Measuring bin fill levels...")
    results = {}
    
    for key in ['a', 'b', 'c']:
        readings = []
        sensor = sensors[key]
        
        for _ in range(5):
            try:
                val = sensor.distance * 100 # cm
                if 2.0 <= val <= 100.0:
                    readings.append(val)
            except:
                pass
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

        update_bin_levels()

    finally:
        seq_lock.release()
        print("✅ Sequence Complete. Ready for next item.")