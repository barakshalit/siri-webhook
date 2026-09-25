import os
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)

# ==================== CONFIGURATION & SECRETS ====================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
# =================================================================

# Initialize Clients
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

def process_with_ai(raw_text):
    """Sends the Hebrew text to Claude and enforces a JSON response."""
    try:
        unwrapped = json.loads(raw_text)
        if isinstance(unwrapped, dict) and "message" in unwrapped:
            raw_text = unwrapped["message"]
    except Exception:
        pass # It was already plain text, proceed normally

    print(f"Sending to AI: {raw_text}", flush=True)
    
    # Calculate current time in Israel to give Claude an anchor for relative time math
    tz = ZoneInfo("Asia/Jerusalem")
    now = datetime.now(tz)
    current_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    current_day = now.strftime("%A")
    
    prompt = f"""
    You are an assistant that parses Siri dictations in Hebrew.
    Current date and time: {current_time_str} (Day: {current_day})

    Parse the user input and return a JSON object with:
    - task_id: 1 for reminders (תזכורת), 2 for tasks (משימה)
    - context: The core action/task (in Hebrew, stripped of time words)
    - time: ISO 8601 datetime string for reminders, null for tasks

    For reminders with time mentions:
    - IMPORTANT: Times between 00:00-06:00 (midnight to 6 AM) are NOT acceptable reminder times. If a time falls in this range, automatically use the PM version instead (add 12 hours).
    - If explicit time-of-day words exist (בוקר, ערב, לילה, צהריים, מחר), use them
    - If only a time is mentioned (e.g., "8:40" or "four"), determine if it should be AM or PM:
      * Pick the version in the future and closest to now
      * Never use times between 00:00-06:00
    - If already passed today, set for tomorrow
    - Always use 24-hour format for calculations

    Respond ONLY with valid JSON, no markdown or extra text.

    Example:
    {{
        "task_id": 1,
        "context": "להוריד את הכלב",
        "time": "2026-07-24T20:40:00"
    }}

    User Input: "{raw_text}"
    """
    
    result_text = None
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-5",
            max_tokens=200,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        # Handle both TextBlock and ThinkingBlock responses
        result_text = None
        for block in response.content:
            if hasattr(block, 'text'):
                result_text = block.text.strip()
                break

        if not result_text:
            print(f"No text content in response", flush=True)
            return None

        clean_text = result_text.replace("```json", "").replace("```", "").strip()
        parsed_json = json.loads(clean_text)
        return parsed_json
    except Exception as e:
        print(f"AI API Error: {e}, output: {result_text}", flush=True)
        return None

def send_ntfy_trigger(payload):
    body = json.dumps(payload, ensure_ascii=False)
    url = f"https://ntfy.sh/{NTFY_TOPIC}"

    print(f"[NTFY] About to POST to: {url}", flush=True)
    print(f"[NTFY] Payload: {payload}", flush=True)
    print(f"[NTFY] Body size: {len(body)} bytes", flush=True)

    try:
        print(f"[NTFY] Sending POST request...", flush=True)
        response = requests.post(url, data=body.encode('utf-8'), timeout=10)

        print(f"[NTFY] Response status code: {response.status_code}", flush=True)
        print(f"[NTFY] Response headers: {dict(response.headers)}", flush=True)
        print(f"[NTFY] Response body: {response.text}", flush=True)

        if response.status_code == 200:
            print(f"[NTFY] Success! Message dispatched", flush=True)
            return True
        else:
            print(f"[NTFY] Warning: Status code {response.status_code} (expected 200)", flush=True)
            return False

    except Exception as e:
        print(f"[NTFY] Exception occurred: {e}", flush=True)
        import traceback
        print(f"[NTFY] Traceback: {traceback.format_exc()}", flush=True)
        return None

@app.route("/", methods=["POST"])
def webhook():
    """Triggered when an HTTP POST request hits your Cloud Run URL."""
    try:
        data = request.get_json(force=True, silent=True) or {}

        # Extracts incoming text whether sent from ntfy JSON or a raw string
        incoming_text = data.get("message") or data.get("text") or request.get_data(as_text=True)

        if not incoming_text:
            return jsonify({"status": "error", "message": "No input text provided"}), 400

        # 1. Process the raw text through Claude
        structured_data = process_with_ai(incoming_text)

        # 2. If successful, trigger the iPhone via email
        if structured_data:
            send_ntfy_trigger(structured_data)
            return jsonify({"status": "success", "data": structured_data}), 200
        else:
            return jsonify({"status": "error", "message": "AI parsing failed"}), 500

    except Exception as e:
        print(f"Webhook processing error: {e}", flush=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)