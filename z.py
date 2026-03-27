import cv2
import time
import uuid
import requests

import config
import hardware
import ai_vision
import mqtt_publisher


API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"


def main():
    print("🚀 Single-run pipeline starting...")

    # -------------------------
    # INIT
    # -------------------------
    ai_vision.init_model()
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("❌ Camera failed")
        return

    # -------------------------
    # CAPTURE ONE FRAME
    # -------------------------
    ret, frame = camera.read()
    camera.release()

    if not ret:
        print("❌ Failed to capture frame")
        return

    frame = cv2.resize(frame, (320, 240))

    print("📸 Frame captured")

    # -------------------------
    # DETECT (OPTIONAL SENSOR CHECK)
    # -------------------------
    try:
        sensor = hardware.sensors['d']
        distance = sensor.distance * 100
    except:
        distance = None

    if not distance or distance > config.DETECT_THRESHOLD:
        print("🚫 No valid detection — exiting")
        return

    print("✅ Detection valid — running inference")

    # -------------------------
    # INFERENCE (ONCE ONLY)
    # -------------------------
    try:
        result = ai_vision.infer_frame(frame)
    except Exception as e:
        print("❌ Inference failed:", e)
        return

    print(f"🧠 Result: {result}")

    # -------------------------
    # HARDWARE ACTION
    # -------------------------
    angle = 0
    if result.lower() == "plastic":
        angle = 90
    elif result.lower() == "paper":
        angle = 180

    try:
        hardware.run_sequence(angle)
        bin_levels = hardware.update_bin_levels()
    except Exception as e:
        print("⚠️ Hardware error:", e)
        bin_levels = {}

    # -------------------------
    # SEND RESULT (MQTT + HTTP)
    # -------------------------
    payload = {
        "id": str(uuid.uuid4()),
        "label": result,
        "timestamp": int(time.time()),
        "bin_levels": bin_levels
    }

    # MQTT (metadata only)
    try:
        mqtt_publisher.send_json(payload, qos=1)
        print("📡 MQTT sent")
    except Exception as e:
        print("⚠️ MQTT failed:", e)

    # HTTP
    try:
        requests.post(API_URL, json=payload, timeout=2)
        print("🌐 HTTP sent")
    except Exception as e:
        print("⚠️ HTTP failed:", e)

    print("🏁 Done — exiting program")


if __name__ == "__main__":
    main()