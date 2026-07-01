"""Simulates a full phone call by firing a sequence of VAPI-shaped
`tool-calls` webhooks at a running instance of this backend.

Usage:
    python scripts/simulate_call.py --url http://localhost:8000
    python scripts/simulate_call.py --url https://your-app.up.railway.app
"""

import argparse
import uuid

import requests

TURNS = [
    "Hi there",
    "My name is Kishore Kumar",
    "I'd like to book a cleaning",
    "How about July 12th at 10 AM",
    "Yes, that works, please book it",
]


def build_payload(call_id: str, utterance: str) -> dict:
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id},
            "toolCalls": [
                {
                    "id": str(uuid.uuid4()),
                    "type": "function",
                    "function": {
                        "name": "process_turn",
                        "arguments": {
                            "utterance": utterance,
                            "caller_phone_number": "+919505204816",  # Twilio magic test number
                        },
                    },
                }
            ],
        }
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    call_id = f"sim-{uuid.uuid4().hex[:8]}"
    print(f"Simulating call_id={call_id} against {args.url}\n")

    for turn in TURNS:
        payload = build_payload(call_id, turn)
        resp = requests.post(f"{args.url}/webhook/vapi", json=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()["results"][0]["result"]
        print(f"Caller: {turn}")
        print(f"Agent:  {result}\n")


if __name__ == "__main__":
    main()
