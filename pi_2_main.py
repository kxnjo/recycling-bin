import paho.mqtt.client as mqtt
import cv2
import numpy as np

import ai_vision

BROKER = "10.224.74.120"

IMAGE_TOPIC = "smartbin/image"
RESULT_TOPIC = "smartbin/result"


def on_message(client, userdata, msg):

    print("Image received")

    image_bytes = msg.payload

    np_arr = np.frombuffer(image_bytes, np.uint8)

    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        print("Decode failed")
        return

    # result = classify_image(frame)
    result = ai_vision.do_infer(image_bytes)
    print("[Pi 2] Model Prediction:", result)

    client.publish(RESULT_TOPIC, result)

    print("[Pi 2] Result sent back")

client = mqtt.Client("AI_Processor")

client.on_message = on_message

client.connect(BROKER,1883)

client.subscribe(IMAGE_TOPIC)

client.loop_forever()
