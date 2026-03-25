import paho.mqtt.client as mqtt
import json

from pi_2_main import STATUS_TOPIC

# TO UPDATE IP ADDRESS IN pi_2_main.py AS WELL
BROKER = "10.39.196.120"   # IP of your broker
IMAGE_TOPIC = "smartbin/image"
RESULT_TOPIC = "smartbin/result"

# Initialize client and connect
client = mqtt.Client("SmartBin_Publisher")
client.connect(BROKER, 1883)
client.loop_start()

# def send_image(image_bytes):
#     """Publish image bytes to AI processing Pi."""
#     client.publish(IMAGE_TOPIC, image_bytes)
#     print("[MQTT] Image sent to broker")

def send_image(payload, qos=1):
    """Publish image bytes to AI processing Pi."""
    client.publish(IMAGE_TOPIC, payload, qos=qos)
    print("[MQTT] Image sent to broker")

def subscribe_results(on_result_callback):
    """Subscribe to AI results."""
    def on_message(client, userdata, message):
        result = message.payload.decode()
        on_result_callback(result)

    client.subscribe(RESULT_TOPIC)
    client.on_message = on_message

def send_bin_levels(levels: dict):
    payload = json.dumps(levels)
    client.publish("smartbin/bin_levels", payload)
    print(f"[MQTT] Sent bin levels: {payload}")