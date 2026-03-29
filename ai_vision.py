# ai_vision.py
import time
import cv2
from ultralytics import YOLO

# mobilenet
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
    

# def capture_and_infer():
#     """Captures an image, runs Custom YOLO Classification, and returns the target bin category."""
#     cap = cv2.VideoCapture(0)

#     if not cap.isOpened():
#         # print("[ERROR] Could not open webcam")
#         return "general" # fallback

#     ret, frame = cap.read()
#     cap.release()

#     if not ret:
#         # print("[ERROR] Failed to capture image")
#         return "general" # fallback

#     img_path = "capture.jpg"
#     cv2.imwrite(img_path, frame)
#     # print(f"\n[CAMERA] Image saved as {img_path}. Starting local inference...")

#     # Grab the resolution from config
#     res = config.INFERENCE_RES
    
#     start_time = time.time()
#     results = model(img_path, imgsz=res, verbose=False)
#     end_time = time.time()

#     latency_ms = (end_time - start_time) * 1000
#     fps = 1000 / latency_ms if latency_ms > 0 else 0

#     # --- CLASSIFICATION EXTRACTION ---
#     # Classification models return probabilities (.probs) instead of bounding boxes (.boxes)
#     result = results[0]
#     predicted_class = "None"
#     confidence = 0.0

#     if result.probs is not None:
#         top1_index = result.probs.top1  # Get the index of the highest probability prediction
#         confidence = result.probs.top1conf.item()  # Get the confidence score (0.0 to 1.0)
#         predicted_class = model.names[top1_index]  # Translate index to your custom class name

#     # Clean benchmark-style output
#     # print(f"\n{'Model':<15} {'Resolution':<12} {'Latency (ms)':<18} {'FPS':<8}")
#     # print("-" * 65)
#     # print(f"{'Custom YOLO-cls':<15} {f'{res[0]}x{res[1]}':<12} {latency_ms:<18.2f} {fps:<8.2f}\n")
#     # print(f"Prediction: {predicted_class.upper()} (Confidence: {confidence:.1%})")

#     # --- DIRECT ROUTING LOGIC ---
#     bin_choice = "general" 
    
#     if predicted_class != "None":
#         primary_obj = predicted_class.lower()
        
#         # Safety Check: Ensure the AI didn't hallucinate a weird category
#         valid_bins = ["plastic", "paper", "general"]
#         if primary_obj in valid_bins:
#             bin_choice = primary_obj
#             # print(f"[AI MATCH] Confirmed custom class: {bin_choice.upper()}")
#         else:
#             print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
            
#     else:
#         print("[AI WARNING] Could not classify image. Defaulting to GENERAL.")

#     # Returns exactly "plastic", "paper", or "general" straight to main.py
#     return bin_choice
# def infer_frame(frame):
#     img_path = "capture.jpg"
#     cv2.imwrite(img_path, frame)
#         # Grab the resolution from config
#     res = config.INFERENCE_RES
    
#     start_time = time.time()
#     results = model(img_path, imgsz=res, verbose=False)
#     end_time = time.time()

#     latency_ms = (end_time - start_time) * 1000
#     fps = 1000 / latency_ms if latency_ms > 0 else 0

#     # --- CLASSIFICATION EXTRACTION ---
#     # Classification models return probabilities (.probs) instead of bounding boxes (.boxes)
#     result = results[0]
#     predicted_class = "None"
#     confidence = 0.0

#     if result.probs is not None:
#         top1_index = result.probs.top1  # Get the index of the highest probability prediction
#         confidence = result.probs.top1conf.item()  # Get the confidence score (0.0 to 1.0)
#         predicted_class = model.names[top1_index]  # Translate index to your custom class name

#     # Clean benchmark-style output
#     # print(f"\n{'Model':<15} {'Resolution':<12} {'Latency (ms)':<18} {'FPS':<8}")
#     # print("-" * 65)
#     # print(f"{'Custom YOLO-cls':<15} {f'{res[0]}x{res[1]}':<12} {latency_ms:<18.2f} {fps:<8.2f}\n")
#     # print(f"Prediction: {predicted_class.upper()} (Confidence: {confidence:.1%})")

#     # --- DIRECT ROUTING LOGIC ---
#     bin_choice = "general" 
    
#     if predicted_class != "None":
#         primary_obj = predicted_class.lower()
        
#         # Safety Check: Ensure the AI didn't hallucinate a weird category
#         valid_bins = ["plastic", "paper", "general"]
#         if primary_obj in valid_bins:
#             bin_choice = primary_obj
#             # print(f"[AI MATCH] Confirmed custom class: {bin_choice.upper()}")
#         else:
#             print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
            
#     else:
#         print("[AI WARNING] Could not classify image. Defaulting to GENERAL.")

#     return bin_choice

# def do_infer(image_bytes):
#     # print(f"\n[AI VISION] Running inference on received image bytes...")
#     """runs Custom YOLO Classification, and returns the target bin category."""
#     np_arr = np.frombuffer(image_bytes, np.uint8)
#     frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

#     if frame is None:
#         # print("[ERROR] Failed to decode image")
#         return "general"  # fallback

#     img_path = "capture.jpg"
#     cv2.imwrite(img_path, frame)
#     # print(f"\n[CAMERA] Image saved as {img_path}. Starting inference on Pi 2...")

#     # Grab the resolution from config
#     res = config.INFERENCE_RES
    
#     start_time = time.time()
#     results = model(img_path, imgsz=res, verbose=False)
#     end_time = time.time()

#     latency_ms = (end_time - start_time) * 1000
#     fps = 1000 / latency_ms if latency_ms > 0 else 0

#     # --- CLASSIFICATION EXTRACTION ---
#     # Classification models return probabilities (.probs) instead of bounding boxes (.boxes)
#     result = results[0]
#     predicted_class = "None"
#     confidence = 0.0

#     if result.probs is not None:
#         top1_index = result.probs.top1  # Get the index of the highest probability prediction
#         confidence = result.probs.top1conf.item()  # Get the confidence score (0.0 to 1.0)
#         predicted_class = model.names[top1_index]  # Translate index to your custom class name

#     # Clean benchmark-style output
#     # print(f"\n{'Model':<15} {'Resolution':<12} {'Latency (ms)':<18} {'FPS':<8}")
#     # print("-" * 65)
#     # print(f"{'Custom YOLO-cls':<15} {f'{res[0]}x{res[1]}':<12} {latency_ms:<18.2f} {fps:<8.2f}\n")
#     # print(f"Prediction: {predicted_class.upper()} (Confidence: {confidence:.1%})")

#     # --- DIRECT ROUTING LOGIC ---
#     bin_choice = "general" 
    
#     if predicted_class != "None":
#         primary_obj = predicted_class.lower()
        
#         # Safety Check: Ensure the AI didn't hallucinate a weird category
#         valid_bins = ["plastic", "paper", "general"]
#         if primary_obj in valid_bins:
#             bin_choice = primary_obj
#             # print(f"[AI MATCH] Confirmed custom class: {bin_choice.upper()}")
#         else:
#             print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
            
#     else:
#         print("[AI WARNING] Could not classify image. Defaulting to GENERAL.")

#     # Returns exactly "plastic", "paper", or "general" straight to main.py
#     return bin_choice

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

    # --- CLASSIFICATION EXTRACTION ---
    if model_type == "yolo":
        # Classification models return probabilities (.probs) instead of bounding boxes (.boxes)
        result = results[0]
        predicted_class = "None"
        confidence = 0.0

        if result.probs is not None:
            top1_index = result.probs.top1  # Get the index of the highest probability prediction
            confidence = result.probs.top1conf.item()  # Get the confidence score (0.0 to 1.0)
            predicted_class = model.names[top1_index]  # Translate index to your custom class name
        elif model_type == "mobilenet":
            # Convert raw outputs to probabilities (0.0 to 1.0) for confidence score
            probabilities = torch.nn.functional.softmax(outputs[0], dim=0)
            conf_tensor, predicted_idx = torch.max(probabilities, 0)
            
            confidence = conf_tensor.item()
            predicted_class = MOBILENET_CLASSES[predicted_idx.item()]

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