import paho.mqtt.client as mqtt
import json

import config

# TO UPDATE IP ADDRESS IN pi_2_main.py AS WELL
# BROKER = "10.39.196.120"   # IP of your broker
IMAGE_TOPIC = "smartbin/image"
RESULT_TOPIC = "smartbin/result"

# Initialize client and connect
client = mqtt.Client("SmartBin_Publisher")
mqtt_connected = False

try:
    # make sure IP is correct / matches whatever Pi is running
    client.connect(config.BROKER_IP, config.BROKER_PORT, 60)
    client.loop_start()
    mqtt_connected = True
    print("[MQTT] Connected")
except Exception as e:
    print("\n[MQTT WARNING] ⚠️ Cannot connect to broker!")
    print(f"[MQTT WARNING] Reason: {e}")
    print("[MQTT] Running WITHOUT MQTT (fallback enabled)\n")
    mqtt_connected = False


# def send_image(image_bytes):
#     """Publish image bytes to AI processing Pi."""
#     client.publish(IMAGE_TOPIC, image_bytes)
#     print("[MQTT] Image sent to broker")``

def send_image(payload, qos=1):
    """Publish image bytes to AI processing Pi."""
    if not mqtt_connected:
        print("[MQTT] Skipped (not connected)")
        return False

    try:
        client.publish(IMAGE_TOPIC, payload, qos=qos)
        print("[MQTT] Image sent to broker")
        return True
    except Exception as e:
        print(f"[MQTT] Publish failed: {e}")
        return False

def subscribe_results(on_result_callback):
    """Subscribe to AI results."""
    if not mqtt_connected:
        print("[MQTT] Subscribe skipped (not connected)")
        return

    def on_message(client, userdata, message):
        result = message.payload.decode()
        on_result_callback(result)

    client.subscribe(RESULT_TOPIC)
    client.on_message = on_message

def send_bin_levels(levels: dict):
    payload = json.dumps(levels)
    client.publish("smartbin/bin_levels", payload)
    print(f"[MQTT] Sent bin levels: {payload}")