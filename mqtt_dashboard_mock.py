import paho.mqtt.client as mqtt
import json
import time

BROKER_IP = "10.174.191.241"
LOG_FILE = "smartbin_log.jsonl"

is_connected = False

def on_connect(client, userdata, flags, rc):
    global is_connected
    if rc == 0:
        is_connected = True
        print("Connected to broker")
    else:
        is_connected = False
        print("Connection failed, rc:", rc)

def on_disconnect(client, userdata, rc):
    global is_connected
    is_connected = False
    print("Disconnected from broker, rc:", rc)

def log_locally(payload):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(payload) + "\n")
    print("Logged locally")

client = mqtt.Client()
client.on_connect = on_connect
client.on_disconnect = on_disconnect

try:
    client.connect(BROKER_IP, 1883)
except Exception as e:
    print("Initial connect failed:", e)

client.loop_start()  # handles reconnects + callbacks in background

# while True:
    # payload = {
        # "label": "plastic",
        # "a" : 12,
        # "b" : 34,
        # "c" : 56,
        # "timestamp": int(time.time())
    # }

    # if is_connected:
        # result = client.publish("smartbin/data", json.dumps(payload), qos=1, retain=True)
        # if result.rc == mqtt.MQTT_ERR_SUCCESS:
            # print("Published update to broker")
        # else:
            # print("Publish returned error, logging locally")
            # log_locally(payload)
    # else:
        # print("No connection, logging locally")
        # log_locally(payload)

    # time.sleep(5)

toggle = True
while True:
    if toggle:
        payload = {
            "label": "plastic",
            "a" : 12,
            "b" : 34,
            "c" : 56,
            "timestamp": int(time.time())
        }
    else:
        payload = {
            "label": "metal",
            "a" : 78,
            "b" : 90,
            "c" : 21,
            "timestamp": int(time.time())
        }
        
    toggle = not toggle
    if is_connected:
        result = client.publish("smartbin/data", json.dumps(payload), qos=1, retain=True)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print("Published update to broker")
        else: # if connection with broker unsuccessful, then log locally into a .jsonl file
            print("Publish returned error, logging locally")
            log_locally(payload)
    else:
        print("No connection, logging locally")
        log_locally(payload)

    time.sleep(5)


