# ai_vision.py
import time
import cv2
from ultralytics import YOLO
import config

print(f"Loading custom YOLO model from {config.MODEL_PATH}...")
model = YOLO(config.MODEL_PATH)

def capture_and_infer():
    """Captures an image, runs Custom YOLO inference, and returns the target bin category."""
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[ERROR] Could not open webcam")
        return "general" # fallback

    ret, frame = cap.read()
    cap.release()

    if not ret:
        print("[ERROR] Failed to capture image")
        return "general" # fallback

    img_path = "capture.jpg"
    cv2.imwrite(img_path, frame)
    print(f"\n[CAMERA] Image saved as {img_path}. Starting local inference...")

    # Grab the resolution from config
    res = config.INFERENCE_RES
    
    start_time = time.time()
    results = model(img_path, imgsz=res, verbose=False)
    end_time = time.time()

    latency_ms = (end_time - start_time) * 1000
    fps = 1000 / latency_ms if latency_ms > 0 else 0

    detected_objects = []
    for r in results:
        if r.boxes is not None and r.boxes.cls is not None:
            detected_objects += [model.names[int(cls)] for cls in r.boxes.cls]

    detections_str = ", ".join(detected_objects) if detected_objects else "None"

    # Clean benchmark-style output
    print(f"\n{'Model':<15} {'Resolution':<12} {'Latency (ms)':<18} {'FPS':<8}")
    print("-" * 65)
    print(f"{'Custom YOLO':<15} {f'{res[0]}x{res[1]}':<12} {latency_ms:<18.2f} {fps:<8.2f}\n")
    print(f"Detected Objects: {detections_str}")

    # --- DIRECT ROUTING LOGIC ---
    bin_choice = "general" 
    
    if detected_objects:
        # 1. Grab the first recognized item
        # 2. Convert to lowercase just in case your custom model has uppercase names
        primary_obj = detected_objects[0].lower()
        
        # 3. Safety Check: Ensure the AI didn't hallucinate a weird category
        valid_bins = ["plastic", "paper", "general"]
        if primary_obj in valid_bins:
            bin_choice = primary_obj
            print(f"[AI MATCH] Confirmed custom class: {bin_choice.upper()}")
        else:
            print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
            
    else:
        print("[AI MATCH] Nothing recognized. Defaulting to GENERAL.")

    # Returns exactly "plastic", "paper", or "general" straight to main.py
    return bin_choice