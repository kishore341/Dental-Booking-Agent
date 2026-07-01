from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _tool_call_payload(call_id="call-abc", utterance="Hi, my name is Priya"):
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id},
            "toolCalls": [
                {
                    "id": "toolcall-1",
                    "type": "function",
                    "function": {
                        "name": "process_turn",
                        "arguments": {"utterance": utterance, "caller_phone_number": "+15005550006"},
                    },
                }
            ],
        }
    }


def test_webhook_acks_non_tool_call_events():
    payload = {"message": {"type": "status-update", "call": {"id": "call-xyz"}}}
    response = client.post("/webhook/vapi", json=payload)
    assert response.status_code == 200
    assert response.json() == {"received": True}


def test_webhook_dispatches_tool_call_to_state_machine():
    with patch("app.api.webhook.handle_turn", return_value="Thanks, what service do you need?") as mock_handle, patch(
        "app.api.webhook.get_conversation_state", return_value=None
    ):
        response = client.post("/webhook/vapi", json=_tool_call_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["toolCallId"] == "toolcall-1"
    assert body["results"][0]["result"] == "Thanks, what service do you need?"
    mock_handle.assert_called_once()


def test_webhook_rejects_unknown_tool_name():
    payload = _tool_call_payload()
    payload["message"]["toolCalls"][0]["function"]["name"] = "some_other_tool"

    response = client.post("/webhook/vapi", json=payload)
    assert response.status_code == 200
    assert "Unsupported tool" in response.json()["results"][0]["result"]
