# ai_vision.py
import cv2
import numpy as np
import config
from profiler_adjusted import profile_block

# AI Engines
from ultralytics import YOLO
import torch
from torchvision import transforms
from PIL import Image

# Global Settings
SAVE_DEBUG_CAPTURE = False
DEBUG_IMAGE_PATH = "capture.jpg"
VALID_BINS = {"plastic", "paper", "general"}
MOBILENET_CLASSES = ["general", "paper", "plastic"] # Update order to match your training!

# Global Model State
model = None
model_type = None
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def init_model():
    """Dynamically loads the correct model based on config.MODEL_PATH"""
    global model, model_type
    
    path_lower = config.MODEL_PATH.lower()
    
    with profile_block("model_initialization", extra={"path": config.MODEL_PATH}):
        if "yolo" in path_lower:
            model_type = "yolo"
            print(f"Loading YOLO model from {config.MODEL_PATH}...")
            model = YOLO(config.MODEL_PATH)
            
        elif "mobilenet" in path_lower:
            model_type = "mobilenet"
            print(f"Loading MobileNetV3 model from {config.MODEL_PATH}...")
            model = torch.load(config.MODEL_PATH, map_location=device)
            model.eval() # Important: Set PyTorch to evaluation mode
            
        else:
            print(f"[ERROR] Unknown model type in path: {config.MODEL_PATH}")


def _run_inference(frame):
    """Internal helper to run the active model and decode the prediction."""
    
    if model_type is None:
        print("[AI CRITICAL ERROR] No AI engine loaded! Check if config.MODEL_PATH contains 'yolo' or 'mobilenet'.")
        return "general" 
    # ------------------------------------------------

    predicted_class = "None"
    confidence = 0.0

    # 1. Run the AI Model
    with profile_block("model_inference", extra={"imgsz": config.INFERENCE_RES, "engine": model_type}):
        if model_type == "yolo":
            results = model(frame, imgsz=config.INFERENCE_RES, verbose=False)
            
        elif model_type == "mobilenet":
            transform = transforms.Compose([
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            
            color_converted = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(color_converted)
            input_tensor = transform(pil_image).unsqueeze(0).to(device)
            
            with torch.no_grad():
                outputs = model(input_tensor)

    # 2. Parse the Results
    with profile_block("prediction_parse"):
        if model_type == "yolo":
            result = results[0]
            if result.probs is not None:
                top1_index = result.probs.top1
                confidence = result.probs.top1conf.item()
                predicted_class = model.names[top1_index]
                
        elif model_type == "mobilenet":
            # Convert raw outputs to probabilities (0.0 to 1.0) for confidence score
            probabilities = torch.nn.functional.softmax(outputs[0], dim=0)
            conf_tensor, predicted_idx = torch.max(probabilities, 0)
            
            confidence = conf_tensor.item()
            predicted_class = MOBILENET_CLASSES[predicted_idx.item()]

        # 3. Safely map to your physical bins
        primary_obj = predicted_class.lower()
        if primary_obj in VALID_BINS:
            bin_choice = primary_obj
        else:
            if predicted_class != "None":
                print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
            else:
                print(f"[AI WARNING] Could not classify image. Defaulting to GENERAL.")
            bin_choice = "general"

    print(f"[AI] Engine={model_type.upper()} | Prediction={bin_choice.upper()} | Confidence={confidence:.1%}")
    return bin_choice


def capture_and_infer():
    """Capture one frame locally and run classification on the frame directly."""
    with profile_block("camera_open"):
        cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[AI WARNING] Could not open webcam. Defaulting to GENERAL.")
        return "general"

    try:
        with profile_block("camera_read"):
            ret, frame = cap.read()
    finally:
        with profile_block("camera_release"):
            cap.release()

    if not ret or frame is None:
        print("[AI WARNING] Failed to capture image. Defaulting to GENERAL.")
        return "general"

    if SAVE_DEBUG_CAPTURE:
        with profile_block("camera_debug_save"):
            cv2.imwrite(DEBUG_IMAGE_PATH, frame)

    return _run_inference(frame)


def do_infer(image_bytes):
    """Decode received image bytes and run classification directly on the frame."""
    with profile_block("network_image_decode"):
        np_arr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        print("[AI WARNING] Failed to decode image. Defaulting to GENERAL.")
        return "general"

    if SAVE_DEBUG_CAPTURE:
        with profile_block("network_debug_save"):
            cv2.imwrite(DEBUG_IMAGE_PATH, frame)

    return _run_inference(frame)