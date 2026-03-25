import paho.mqtt.client as mqtt
import cv2
import numpy as np
import json
import ai_vision
# TO UPDATE IP ADDRESS IN mqtt_publisher.py AS WELL
# BROKER = "10.174.191.120"
BROKER = "10.39.196.120"  # for local testing

IMAGE_TOPIC = "smartbin/image"
ACK_TOPIC = "smartbin/image_ack"
RESULT_TOPIC = "smartbin/result"
STATUS_TOPIC = "smartbin/status"

client = mqtt.Client("AI_Processor")

# Last Will â†’ if Pi2 crashes, broker sends offline
client.will_set(STATUS_TOPIC, payload="offline", retain=True)

def on_message(client, userdata, msg):
    data = json.loads(msg.payload)

    request_id = data["id"]
    image_hex = data["image"]
    image_bytes = bytes.fromhex(image_hex)

    # Run inference
    label = ai_vision.do_infer(image_bytes)

    # Send result back
    response = {
        "id": request_id,
        "label": label
    }

    client.publish("smartbin/result", json.dumps(response), qos=1)

client.on_message = on_message

client.connect(BROKER, 1883)
client.loop_start()

# Publish "alive" after connecting (retain=True)
client.publish(STATUS_TOPIC, payload="alive", retain=True)

client.subscribe(IMAGE_TOPIC)
print("[Pi2] AI Processor initialized, waiting for images...")

# Keep the script alive
from signal import pause
pause()
