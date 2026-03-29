# ai_vision.py
import time
import cv2
from ultralytics import YOLO

# mobilenet
from PIL import Image
import torch
from torchvision import transforms, models
MOBILENET_CLASSES = ["general", "paper", "plastic"]

import config
import numpy as np

model = None 
model_type = None
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def init_model():
    global model, model_type
    path_lower = config.MODEL_PATH.lower()

    if model is not None:
        return
    
    if "yolo" in path_lower:
        print(f"Loading YOLO model from {config.MODEL_PATH}...")
        model_type = "yolo"
        model = YOLO(config.MODEL_PATH)
        
    elif "mobilenet" in path_lower:
        print(f"Loading MobileNetV3 model from {config.MODEL_PATH}...")
        model_type = "mobilenet"
        
        model = models.mobilenet_v3_small(num_classes=len(MOBILENET_CLASSES))
        
        state_dict = torch.load(config.MODEL_PATH, map_location=device)
        
        model.load_state_dict(state_dict)
        
        model = model.to(device)
        model.eval() 
        
    else:
        print(f"[ERROR] Unknown model type in path: {config.MODEL_PATH}")
    
def infer(frame=None, image_bytes=None):
    if model is None:
        try:
            init_model()
        except:
            print("[ERROR] Model not loaded")
            return "general"
        
    predicted_class = "None"
    confidence = 0.0

    # Get frame from whichever source was provided
    if frame is None and image_bytes is not None:
        np_arr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    
    if frame is None:
        print("[ERROR] No valid input provided")
        return "general"

    img_path = "capture.jpg"
    cv2.imwrite(img_path, frame)

    # Grab the resolution from config
    res = config.INFERENCE_RES
    
    if model_type == "yolo":
            results = model(frame, imgsz=config.INFERENCE_RES, verbose=False)
            
    elif model_type == "mobilenet":
        transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406], 
                std=[0.229, 0.224, 0.225]
            ),
        ])
        
        color_converted = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(color_converted)
        input_tensor = transform(pil_image).unsqueeze(0).to(device)
        
        with torch.no_grad():
            outputs = model(input_tensor)

    # --- CLASSIFICATION EXTRACTION ---
    if model_type == "yolo":
        try: 
            # Classification models return probabilities (.probs) instead of bounding boxes (.boxes)
            result = results[0]
            predicted_class = "None"
            confidence = 0.0

            if result.probs is not None:
                top1_index = result.probs.top1  # Get the index of the highest probability prediction
                confidence = result.probs.top1conf.item()  # Get the confidence score (0.0 to 1.0)
                predicted_class = model.names[top1_index]  # Translate index to your custom class name
        except Exception as e:
            print(f"[ERROR] YOLO inference failed: {e}")
            return "general"
        
    elif model_type == "mobilenet":
        # Convert raw outputs to probabilities (0.0 to 1.0) for confidence score
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb)
            input_tensor = transform(pil_image).unsqueeze(0).to(device)

            with torch.no_grad():
                outputs = model(input_tensor)

            probabilities = torch.nn.functional.softmax(outputs[0], dim=0)
            conf_tensor, predicted_idx = torch.max(probabilities, 0)

            confidence = conf_tensor.item()
            predicted_class = MOBILENET_CLASSES[predicted_idx.item()]

        except Exception as e:
            print(f"[ERROR] MobileNet inference failed: {e}")
            return "general"

    # --- DIRECT ROUTING LOGIC ---
    bin_choice = "general" 
    
    if predicted_class != "None":
        primary_obj = predicted_class.lower()
        
        # Safety Check: Ensure the AI didn't hallucinate a weird category
        valid_bins = ["plastic", "paper", "general"]
        if primary_obj in valid_bins:
            bin_choice = primary_obj
            # print(f"[AI MATCH] Confirmed custom class: {bin_choice.upper()}")
        else:
            print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
            
    else:
        print("[AI WARNING] Could not classify image. Defaulting to GENERAL.")

    # Returns exactly "plastic", "paper", or "general" straight to main.py
    return bin_choice