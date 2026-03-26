# config.py

# --- AI CONFIGURATION ---
MODEL_PATH = "model/2_best_50epoch.pt" 
INFERENCE_RES = (320, 320) # Set this to the resolution that gave you the best FPS in the benchmark

# --- SENSOR CONFIGURATION ---
BIN_CONFIGS = {
    'a': {'echo': 5, 'trigger': 6, 'label': 'General'},
    'b': {'echo': 12, 'trigger': 13, 'label': 'Plastic'},
    'c': {'echo': 16, 'trigger': 26, 'label': 'Paper'},
    'd': {'echo': 25, 'trigger': 24, 'label': 'Detect'}
}
DETECT_THRESHOLD = 20  # cm

# --- BUTTON PINS ---
BTN_1_PIN = 17
BTN_2_PIN = 27
BTN_3_PIN = 22

# --- SERVO PINS & POSITIONS ---
SERVO_MAIN_PIN = 18
SERVO_LID_PIN = 23

MAIN_HOME = 90
FS90_HOME = 0         
FS90_TARGET = 90

# --- MQTT --- 
BROKER_IP = "10.39.196.120"
BROKER_PORT = 1883