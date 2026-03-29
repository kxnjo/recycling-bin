from flask import Flask, jsonify, request
from flask_cors import CORS
import boto3

app = Flask(__name__)
CORS(app)

# Allow all hosts (fix 400 Bad Request)
app.config['SERVER_NAME'] = None  # don't restrict host matching

latest_bin_data = {}
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
table = dynamodb.Table('smartbin')

@app.route("/bin", methods=["GET", "POST"])
def bin_data():
    global latest_bin_data
    if request.method == "POST":
        data = request.json
        if not data:
            return jsonify({"error": "No JSON payload received"}), 400
        
        latest_bin_data = data.copy()
        table.put_item(Item={
            'timestamp': data['timestamp'],
            'a': str(data.get('a', 0)),
            'b': str(data.get('b', 0)),
            'c': str(data.get('c', 0)),
            'label': data.get('label', ''),
            'inference_id': data.get('inference_id', '')
        })
        return jsonify({"status": "success"}), 200
    else:
        # Always query DynamoDB for the true latest record
        try:
            response = table.scan()
            items = response['Items']
            if not items:
                return jsonify({"message": "No data yet"}), 404
            latest = max(items, key=lambda x: int(x['timestamp']))
            return jsonify({
                'a': float(latest.get('a', 0)),
                'b': float(latest.get('b', 0)),
                'c': float(latest.get('c', 0)),
                'label': latest.get('label', ''),
                'timestamp': int(latest['timestamp']),
                'inference_id': latest.get('inference_id', '')
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

# Batch endpoint for multiple records at once (used for retrying offline logs)
@app.route("/batch", methods=["POST"])
def batch_bin_data():
    try:
        data_list = request.get_json()

        if not isinstance(data_list, list):
            return jsonify({"error": "Expected a list of items"}), 400

        with table.batch_writer() as batch:
            for data in data_list:
                batch.put_item(Item={
                    'timestamp': data['timestamp'],
                    'a': str(data.get('a', 0)),
                    'b': str(data.get('b', 0)),
                    'c': str(data.get('c', 0)),
                    'label': data.get('label', ''),
                    'inference_id': data.get('inference_id', '')
                })

        return jsonify({
            "status": "success",
            "inserted": len(data_list)
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
        
@app.route("/history", methods=["GET"])
def get_history():
    try:
        response = table.scan()
        items = response['Items']
        
        # Sort by timestamp descending (newest first)
        items.sort(key=lambda x: int(x['timestamp']), reverse=True)
        
        return jsonify(items), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def lambda_handler(event, context):
    import awsgi
    if 'requestContext' in event and 'http' in event['requestContext']:
        # Translate v2 fields back to v1 so awsgi understands them
        event['httpMethod'] = event['requestContext']['http']['method']
        event['path'] = event['requestContext']['http']['path']
        event['queryStringParameters'] = event.get('queryStringParameters', {})
    return awsgi.response(app, event, context)