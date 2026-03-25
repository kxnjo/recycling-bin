from flask import Flask, jsonify
import paho.mqtt.client as mqtt
import json
import threading

from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# For a single bin, just store the latest payload
latest_bin_data = {}

# ------------------------------
# MQTT Callback
# ------------------------------
def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        latest_bin_data.update(data)  # store/overwrite latest state
        print(f"[MQTT] Receive update: {data}")
    except Exception as e:
        print("Error processing MQTT message:", e)

# ------------------------------
# MQTT Setup
# ------------------------------
def start_mqtt():
    client = mqtt.Client()
    # client.connect("10.174.191.241", 1883)  # to match BROKER IP !!
    client.connect("10.39.196.120", 1883)  # to match BROKER IP !!
    client.subscribe("smartbin/bin_levels")
    client.on_message = on_message
    client.loop_forever()

threading.Thread(target=start_mqtt, daemon=True).start()

# ------------------------------
# API Endpoint
# ------------------------------
@app.route("/bin")
def get_bin():
    if latest_bin_data:
        return jsonify(latest_bin_data)
    else:
        return jsonify({"message": "No data yet"}), 404

# ------------------------------
# Run Flask
# ------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
