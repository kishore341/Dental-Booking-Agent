"""VAPI webhook intake.

We only meaningfully act on `message.type == "tool-calls"` -- that's the
event Vapi fires when the assistant invokes our configured `process_turn`
tool. Every other event type (status-update, transcript, end-of-call-report,
hang, etc.) is acknowledged with 200 and otherwise ignored, per Vapi's own
guidance that unhandled server-message types should simply be ACKed.

Expected Vapi Custom Tool config (set on the assistant, not here):

    {
      "type": "function",
      "function": {
        "name": "process_turn",
        "description": "Send the caller's latest utterance to the booking backend and get back what to say next.",
        "parameters": {
          "type": "object",
          "properties": {
            "utterance": {"type": "string"},
            "caller_phone_number": {"type": "string"}
          },
          "required": ["utterance"]
        }
      },
      "server": {"url": "https://<your-deploy>/webhook/vapi"}
    }
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.core.state_machine import handle_turn
from app.models.schemas import VapiToolResult, VapiToolResultsResponse, VapiWebhookPayload
from app.services.firestore_service import get_conversation_state, save_conversation_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/vapi")
async def vapi_webhook(request: Request):
    body_bytes = await request.body()
    try:
        raw = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.")

    try:
        payload = VapiWebhookPayload.model_validate(raw)
    except Exception:
        raise HTTPException(
            status_code=400, detail="Request body does not match the expected VAPI webhook shape."
        )

    message = payload.message

    logger.info("vapi_webhook_received type=%s call_id=%s", message.type, message.call.id if message.call else None)

    if message.type != "tool-calls":
        # ACK everything else (status-update, transcript, end-of-call-report, hang...)
        return {"received": True}

    if not message.call or not message.toolCalls:
        return {"received": True}

    call_id = message.call.id
    results = []

    for tool_call in message.toolCalls:
        if tool_call.function.name != "process_turn":
            results.append(
                VapiToolResult(toolCallId=tool_call.id, result="Unsupported tool.")
            )
            continue

        args = tool_call.function.arguments
        utterance = args.get("utterance", "")
        caller_phone = args.get("caller_phone_number")

        if caller_phone:
            state = get_conversation_state(call_id)
            if state and not state.slots.phone_number:
                state.slots.phone_number = caller_phone
                save_conversation_state(state)

        reply_text = handle_turn(call_id=call_id, user_utterance=utterance)
        results.append(VapiToolResult(toolCallId=tool_call.id, result=reply_text))

    return VapiToolResultsResponse(results=results).model_dump()