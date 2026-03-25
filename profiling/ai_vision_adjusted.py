import cv2
import numpy as np
from ultralytics import YOLO
import config
from profiler_adjusted import profile_block

# Set to True only if you still want to save a debug image occasionally.
SAVE_DEBUG_CAPTURE = False
DEBUG_IMAGE_PATH = "capture.jpg"
VALID_BINS = {"plastic", "paper", "general"}

model = YOLO(config.MODEL_PATH)


def _decode_prediction(results):
    predicted_class = "None"
    confidence = 0.0

    result = results[0]
    if result.probs is not None:
        top1_index = result.probs.top1
        confidence = result.probs.top1conf.item()
        predicted_class = model.names[top1_index]

    primary_obj = predicted_class.lower()
    if primary_obj in VALID_BINS:
        return primary_obj, confidence

    if predicted_class != "None":
        print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
    else:
        print("[AI WARNING] Could not classify image. Defaulting to GENERAL.")

    return "general", confidence


def capture_and_infer():
    """Capture one frame locally and run YOLO classification on the frame directly."""
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

    with profile_block("model_inference", extra={"imgsz": config.INFERENCE_RES}):
        results = model(frame, imgsz=config.INFERENCE_RES, verbose=False)

    with profile_block("prediction_parse"):
        bin_choice, confidence = _decode_prediction(results)

    print(f"[AI] Prediction={bin_choice.upper()} confidence={confidence:.1%}")
    return bin_choice


def do_infer(image_bytes):
    """Decode received image bytes and run YOLO classification directly on the frame."""
    with profile_block("network_image_decode"):
        np_arr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        print("[AI WARNING] Failed to decode image. Defaulting to GENERAL.")
        return "general"

    if SAVE_DEBUG_CAPTURE:
        with profile_block("network_debug_save"):
            cv2.imwrite(DEBUG_IMAGE_PATH, frame)

    with profile_block("model_inference", extra={"imgsz": config.INFERENCE_RES}):
        results = model(frame, imgsz=config.INFERENCE_RES, verbose=False)

    with profile_block("prediction_parse"):
        bin_choice, confidence = _decode_prediction(results)

    print(f"[AI] Prediction={bin_choice.upper()} confidence={confidence:.1%}")
    return bin_choice