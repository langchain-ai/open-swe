from flask import Flask, jsonify, request

from src.greeting_handler import process_greeting

app = Flask(__name__)


@app.route("/greet", methods=["POST"])
def greet():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400
    message = data.get("message", "")
    if not isinstance(message, str):
        return jsonify({"error": "Invalid input type"}), 400
    response = process_greeting(message)
    if response is None:
        return jsonify({"error": "Invalid input"}), 400
    if response.startswith("Hello"):
        return jsonify({"response": response, "status": "success"}), 200
    else:
        return jsonify({"response": response, "status": "unrecognized"}), 200
