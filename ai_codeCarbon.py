import cv2
import numpy as np
import config
from profiler import profile_block

# AI Engines
from ultralytics import YOLO
import torch
from torchvision import transforms, models
from PIL import Image

# --- NEW: Energy Profiling ---
from codecarbon import EmissionsTracker

# Global Settings
SAVE_DEBUG_CAPTURE = False
DEBUG_IMAGE_PATH = "capture.jpg"
VALID_BINS = {"plastic", "paper", "general"}
MOBILENET_CLASSES = ["general", "paper", "plastic"] 

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
            model = models.mobilenet_v3_small(num_classes=len(MOBILENET_CLASSES))
            state_dict = torch.load(config.MODEL_PATH, map_location=device)
            model.load_state_dict(state_dict)
            model = model.to(device)
            model.eval() 
        else:
            print(f"[ERROR] Unknown model type in path: {config.MODEL_PATH}")

def _run_inference(frame):
    """Internal helper to run the active model with Energy Profiling."""
    if model_type is None:
        init_model()
        if model_type is None: return "general"

    predicted_class = "None"
    confidence = 0.0

    # Initialize CodeCarbon Tracker for this specific inference call
    # It will save data to 'emissions.csv' by default
    tracker = EmissionsTracker(
        project_name=f"smart_bin_{model_type}",
        measure_power_secs=1,
        save_to_file=True,
        logging_level='error' # Keep console clean
    )

    tracker.start()
    try:
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
    finally:
        # Stop tracking and capture the energy used in kWh
        energy_consumed = tracker.stop()
        print(f"[ENERGY] {model_type.upper()} Inference: {energy_consumed:.10f} kWh")

    # 2. Parse the Results
    with profile_block("prediction_parse"):
        if model_type == "yolo":
            result = results[0]
            if result.probs is not None:
                top1_index = result.probs.top1
                confidence = result.probs.top1conf.item()
                predicted_class = model.names[top1_index]
        elif model_type == "mobilenet":
            probabilities = torch.nn.functional.softmax(outputs[0], dim=0)
            conf_tensor, predicted_idx = torch.max(probabilities, 0)
            confidence = conf_tensor.item()
            predicted_class = MOBILENET_CLASSES[predicted_idx.item()]

        primary_obj = predicted_class.lower()
        bin_choice = primary_obj if primary_obj in VALID_BINS else "general"

    print(f"[AI] Engine={model_type.upper()} | Prediction={bin_choice.upper()} | Confidence={confidence:.1%}")
    return bin_choice

def capture_and_infer():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened(): return "general"
    try:
        ret, frame = cap.read()
    finally:
        cap.release()
    if not ret or frame is None: return "general"
    return _run_inference(frame)

def do_infer(image_bytes):
    np_arr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if frame is None: return "general"
    return _run_inference(frame)