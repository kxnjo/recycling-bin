import json
import requests   

API_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/bin"
BATCH_URL = "https://zoax1qwl2f.execute-api.us-east-1.amazonaws.com/batch"
def send_bin_levels_http(bin_levels):
    try:
        response = requests.post(API_URL, json=bin_levels)
        if response.status_code == 200:
            print(f"[HTTP] Sent bin levels, status: {response.status_code}")
        else:
            print(f"[HTTP] Failed to send bin levels, status: {response.status_code}")
            save_to_local_log(bin_levels)
    except Exception as e:
        print(f"[HTTP] Failed: {e}")
        save_to_local_log(bin_levels)

LOG_FILE = "offline_bin_logs.jsonl"

def save_to_local_log(data):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(data) + "\n")
        print("[LOG] Saved data locally")
    except Exception as e:
        print(f"[LOG ERROR] Could not write to file: {e}")

BATCH_SIZE = 10

def resend_offline_logs():
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return

    remaining_lines = []

    # Process in batches
    for i in range(0, len(lines), BATCH_SIZE):
        batch = lines[i:i+BATCH_SIZE]
        data_batch = [json.loads(line) for line in batch]

        try:
            response = requests.post(BATCH_URL, json=data_batch, timeout=3)

            if response.status_code == 200:
                print(f"[RETRY] Sent batch of {len(batch)} logs")
            else:
                print("[RETRY] Batch failed:", response.status_code, response.text)
                remaining_lines.extend(batch)

        except:
            print("[RETRY] Network error, keeping batch")
            remaining_lines.extend(batch)

    # Rewrite file with failed logs only
    with open(LOG_FILE, "w") as f:
        f.writelines(remaining_lines)
    
# Fallback if Pi 2 and Pi 1 cannot perform inference
CLOUD_MODEL_URL = "http://54.227.231.254:5000/infer"
def send_image_cloud(image_bytes):
    try:
        response = requests.post(CLOUD_MODEL_URL, data=image_bytes)
        if response.status_code == 200:
            result = response.json().get("label")
            print(f"[CLOUD] Received result: {result}")
            return result
        else:
            print(f"[CLOUD] Inference failed with status: {response.status_code}")
            return None
    except Exception as e:
        print(f"[CLOUD] Failed to send image: {e}")
        return None
