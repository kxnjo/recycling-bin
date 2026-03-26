# ai_vision.py
import cv2
import numpy as np
import config
import threading
from profiler import profile_block, profile_cpu

# AI Engines
from ultralytics import YOLO
import torch
from torchvision import transforms, models
from PIL import Image

# Global Settings
SAVE_DEBUG_CAPTURE = False
DEBUG_IMAGE_PATH = "capture.jpg"
VALID_BINS = {"plastic", "paper", "general"}
MOBILENET_CLASSES = ["general", "paper", "plastic"]  # Update order to match your training!

# Camera Settings
CAMERA_INDEX = getattr(config, "CAMERA_INDEX", 0)
CAMERA_WIDTH = getattr(config, "CAMERA_WIDTH", 224)
CAMERA_HEIGHT = getattr(config, "CAMERA_HEIGHT", 224)
CAMERA_FPS = getattr(config, "CAMERA_FPS", 30)
CAMERA_BUFFERSIZE = getattr(config, "CAMERA_BUFFERSIZE", 1)
CAMERA_WARMUP_GRABS = getattr(config, "CAMERA_WARMUP_GRABS", 2)
CAMERA_IDLE_TIMEOUT = getattr(config, "CAMERA_IDLE_TIMEOUT", 15.0)  # seconds; set 0 to never auto-release

# Global Model State
model = None
model_type = None
loaded_model_path = None
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Global Camera State
cap = None
camera_lock = threading.Lock()
camera_release_timer = None
model_lock = threading.Lock()

# Build once, reuse every inference
MOBILENET_TRANSFORM = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def _configure_camera(camera):
    """Apply camera settings once after opening."""
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    camera.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, CAMERA_BUFFERSIZE)


def _release_camera_locked():
    """Release the camera. Must be called while holding camera_lock."""
    global cap, camera_release_timer

    if camera_release_timer is not None:
        camera_release_timer.cancel()
        camera_release_timer = None

    if cap is not None:
        with profile_block("camera_release"):
            cap.release()
        cap = None


def _release_camera_from_timer():
    """Timer callback for idle release."""
    with camera_lock:
        _release_camera_locked()


def _schedule_camera_release_locked():
    """Re-arm the idle-release timer. Must be called while holding camera_lock."""
    global camera_release_timer

    if CAMERA_IDLE_TIMEOUT <= 0:
        return

    if camera_release_timer is not None:
        camera_release_timer.cancel()

    camera_release_timer = threading.Timer(CAMERA_IDLE_TIMEOUT, _release_camera_from_timer)
    camera_release_timer.daemon = True
    camera_release_timer.start()


def _ensure_camera_open_locked():
    """
    Open camera only if needed.
    Must be called while holding camera_lock.
    Returns: (camera_object_or_none, just_opened_bool)
    """
    global cap

    if cap is not None and cap.isOpened():
        return cap, False

    with profile_block("camera_open"):
        cap = cv2.VideoCapture(CAMERA_INDEX)
        _configure_camera(cap)

    if not cap.isOpened():
        try:
            cap.release()
        except Exception:
            pass
        cap = None
        return None, False

    return cap, True


def close_resources():
    """Call this once when your app is shutting down."""
    with camera_lock:
        _release_camera_locked()


def init_model(force_reload=False):
    """Dynamically loads the correct model based on config.MODEL_PATH."""
    global model, model_type, loaded_model_path

    with model_lock:
        if (
            not force_reload
            and model is not None
            and model_type is not None
            and loaded_model_path == config.MODEL_PATH
        ):
            return

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
                model = None
                model_type = None
                loaded_model_path = None
                return

        loaded_model_path = config.MODEL_PATH


def _run_inference(frame):
    """Internal helper to run the active model and decode the prediction."""

    if model_type is None:
        print("[AI INFO] AI engine wasn't loaded yet! Auto-loading now...")
        init_model()

        if model_type is None:
            print(f"[AI CRITICAL ERROR] Path '{config.MODEL_PATH}' must contain 'yolo' or 'mobilenet'!")
            return "general"

    predicted_class = "None"
    confidence = 0.0

    with profile_block("model_inference", extra={"imgsz": config.INFERENCE_RES, "engine": model_type}):
        if model_type == "yolo":
            results = model(frame, imgsz=config.INFERENCE_RES, verbose=False)

        elif model_type == "mobilenet":
            with profile_block("mobilenet_preprocess"):
                color_converted = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_image = Image.fromarray(color_converted)
                input_tensor = MOBILENET_TRANSFORM(pil_image).unsqueeze(0).to(device)

            with torch.inference_mode():
                outputs = model(input_tensor)

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
        if primary_obj in VALID_BINS:
            bin_choice = primary_obj
        else:
            if predicted_class != "None":
                print(f"[AI WARNING] Unknown class '{primary_obj}'. Defaulting to GENERAL.")
            else:
                print("[AI WARNING] Could not classify image. Defaulting to GENERAL.")
            bin_choice = "general"

    print(f"[AI] Engine={model_type.upper()} | Prediction={bin_choice.upper()} | Confidence={confidence:.1%}")
    return bin_choice


@profile_cpu
def capture_and_infer():
    """Capture one frame locally and run classification on the frame directly."""
    with camera_lock:
        camera, just_opened = _ensure_camera_open_locked()

        if camera is None:
            print("[AI WARNING] Could not open webcam. Defaulting to GENERAL.")
            return "general"

        with profile_block("camera_read"):
            # If the camera was already open, flush a couple of old frames
            if not just_opened and CAMERA_WARMUP_GRABS > 0:
                for _ in range(CAMERA_WARMUP_GRABS):
                    camera.grab()

            ret, frame = camera.read()

        _schedule_camera_release_locked()

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