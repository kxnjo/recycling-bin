# ai_vision.py
import time
import cv2
import numpy as np
import config

# Ultralytics for YOLO
from ultralytics import YOLO

# PyTorch for MobileNetV3
import torch
from torchvision import transforms
from PIL import Image

model = None 
model_type = None  # Will be 'yolo' or 'mobilenet'
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# =========================================================
# ⚠️ MOBILE-NET CLASS MAPPING (IMPORTANT)
# YOLO automatically saves class names in `model.names`. 
# Pure PyTorch models DO NOT. You must define the exact order 
# your MobileNetV3 model outputs its classes (0, 1, 2).
# =========================================================
MOBILENET_CLASSES = ["general", "paper", "plastic"] # Update this order to match your training!


def init_model():
    global model, model_type
    
    path_lower = config.MODEL_PATH.lower()
    
    if "yolo" in path_lower:
        model_type = "yolo"
        print(f"Loading YOLO model from {config.MODEL_PATH}...")
        model = YOLO(config.MODEL_PATH)
        
    elif "mobilenet" in path_lower:
        model_type = "mobilenet"
        print(f"Loading MobileNetV3 model from {config.MODEL_PATH}...")
        # Load the PyTorch model
        model = torch.load(config.MODEL_PATH, map_location=device)
        model.eval() # Set to evaluation mode
        
    else:
        print(f"[ERROR] Unknown model type in path: {config.MODEL_PATH}")


def _run_inference(frame):
    """Internal helper function that handles the actual AI math for both model types."""
    start_time = time.time()
    predicted_class = "None"

    if model_type == "yolo":
        # YOLO handles its own BGR->RGB conversion and resizing natively
        results = model(frame, imgsz=config.INFERENCE_RES, verbose=False)
        result = results[0]
        
        if result.probs is not None:
            top1_index = result.probs.top1
            predicted_class = model.names[top1_index]

    elif model_type == "mobilenet":
        # MobileNetV3 requires specific PyTorch transformations
        transform = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        
        # Convert OpenCV BGR frame to PIL RGB Image
        color_coverted = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(color_coverted)
        
        # Transform and push to device
        input_tensor = transform(pil_image).unsqueeze(0).to(device)
        
        with torch.no_grad():
            outputs = model(input_tensor)
            _, predicted = torch.max(outputs, 1)
            predicted_class = MOBILENET_CLASSES[predicted.item()]

    # Calculate performance metrics
    end_time = time.time()
    latency_ms = (end_time - start_time) * 1000
    fps = 1000 / latency_ms if latency_ms > 0 else 0
    # print(f"[{model_type.upper()}] Latency: {latency_ms:.2f}ms | FPS: {fps:.2f} | Raw Pred: {predicted_class}")

    # --- ROUTING LOGIC ---
    bin_choice = "general" 
    
    if predicted_class != "None":
        primary_obj = predicted_class.lower()
        valid_bins = ["plastic", "paper", "general"]
        
        if primary_obj in valid_bins:
            bin_choice = primary_obj
        else:
            print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
    else:
        print("[AI WARNING] Could not classify image. Defaulting to GENERAL.")

    return bin_choice


def capture_and_infer():
    """Captures an image via local webcam and runs inference."""
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        return "general"

    ret, frame = cap.read()
    cap.release()

    if not ret:
        return "general"

    # Save for debugging/viewing later
    cv2.imwrite("capture.jpg", frame)

    # Pass the raw frame straight to the shared inference function!
    return _run_inference(frame)


def do_infer(image_bytes):
    """Decodes image bytes from MQTT/Network and runs inference."""
    np_arr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return "general"

    # Save for debugging/viewing later
    cv2.imwrite("capture.jpg", frame)

    # Pass the decoded frame straight to the shared inference function!
    return _run_inference(frame)